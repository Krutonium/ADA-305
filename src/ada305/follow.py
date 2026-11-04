"""Host-side follower for the ADA305's physical controls.

In USB mode the speaker does not apply its own knob and button changes.  It
only *reports* them on interrupt endpoint ``0x81``; the host is expected to
apply them with control writes.  :class:`Follower` implements that loop:

* ``10`` / ``20`` / ``40`` select subwoofer / center / surround; pressing the
  same selector again returns to master, and the selection expires after three
  seconds without a volume change (matching the AMS help text and ALSERV).
* ``00 01`` / ``00 ff`` are one wheel click louder / quieter.  The delta is
  applied to the active channel (master when none is selected).
* ``01`` toggles mute.  Repeated packets for one press are debounced.
* ``80`` means reconnect: drop the active selection and re-read the device.

Safety is explicit: quieter clicks are always applied; louder clicks need
``allow_increase``; targets above level 90 additionally need
``allow_very_loud``.  Nothing is ever clamped: a click that would leave the
documented attenuation range is ignored.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from . import protocol as p
from .device import Ada305, DeviceError
from .safety import LEVEL_MAX, VERY_LOUD_LEVEL, SafetyError, db_to_level
from .transport import TransportError


@dataclass(frozen=True)
class FollowerSettings:
    """Policy and timing for :class:`Follower`."""

    allow_increase: bool = False
    allow_very_loud: bool = False
    debounce_seconds: float = 0.35
    channel_timeout_seconds: float = 3.0
    warning_interval_seconds: float = 2.0
    dry_run: bool = False

    def __post_init__(self) -> None:
        for name in (
            "debounce_seconds",
            "channel_timeout_seconds",
            "warning_interval_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            if value < 0:
                raise ValueError(f"{name} must not be negative")


class Follower:
    """Apply ADA305 physical-control reports through :class:`Ada305`."""

    def __init__(
        self,
        device: Ada305,
        settings: Optional[FollowerSettings] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
        warn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.device = device
        self.settings = settings or FollowerSettings()
        self._clock = clock
        self._log = log
        self._warn_output = warn or (lambda message: log(f"WARNING: {message}"))

        self.active = p.resolve_channel("master")
        self._deadline: Optional[float] = None
        self._last_selector: Dict[int, float] = {}
        self._last_mute: Optional[float] = None
        self._last_warning: Dict[str, float] = {}

    # -- public API ------------------------------------------------------

    @property
    def active_key(self) -> str:
        return self.active.key

    def tick(self) -> None:
        """Expire an idle channel selection back to master."""

        if self.active.is_master or self._deadline is None:
            return
        now = self._clock()
        if now >= self._deadline:
            self._log(
                f"{self.active.label} selection timed out; "
                "the wheel now adjusts master"
            )
            self._set_active(p.resolve_channel("master"), now)

    def read_timeout_ms(self, maximum_ms: int) -> int:
        """Shorten an interrupt read so the selection timer stays accurate."""

        if self.active.is_master or self._deadline is None:
            return maximum_ms
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            return 1
        return max(1, min(maximum_ms, int(remaining * 1000) + 10))

    def handle_report(self, report: p.InterruptReport) -> None:
        """Apply one decoded report.  Never raises for device-side refusals."""

        self.tick()
        now = self._clock()

        if report.reconnect:
            self._handle_reconnect(now)
            return

        if report.selector_flags:
            self._handle_selector(report, now)

        if report.mute_toggle:
            self._handle_mute(now)

        if report.volume_delta:
            self._handle_wheel(report.volume_delta, now)

        if report.mode_cycle:
            self._log("mode-cycle report ignored on ADA305")

        self.tick()

    # -- report handlers -------------------------------------------------

    def _handle_reconnect(self, now: float) -> None:
        self._log("reconnect report: re-reading device state")
        self._set_active(p.resolve_channel("master"), now)
        try:
            status = self.device.read_status()
        except (DeviceError, SafetyError, TransportError) as exc:
            self._warn_once("reconnect", f"could not re-read state: {exc}")
            return
        self._log(
            f"device reports mute={'on' if status.mute else 'off'}, "
            f"mode {status.mode}; wheel now adjusts master"
        )

    def _handle_selector(self, report: p.InterruptReport, now: float) -> None:
        flag = report.selector_flag
        if flag is None:
            self._warn_once(
                "selector",
                "report has more than one selector bit set; ignoring it",
            )
            return

        last = self._last_selector.get(flag)
        if last is not None and now - last < self.settings.debounce_seconds:
            return
        self._last_selector[flag] = now

        channel = p.resolve_channel(p.SELECTOR_CHANNELS[flag])
        if self.active.key == channel.key:
            self._log(f"{channel.label} deselected; the wheel adjusts master")
            self._set_active(p.resolve_channel("master"), now)
            return

        self._log(f"{channel.label} selected; the wheel adjusts {channel.key}")
        self._set_active(channel, now)

    def _handle_mute(self, now: float) -> None:
        if (
            self._last_mute is not None
            and now - self._last_mute < self.settings.debounce_seconds
        ):
            return
        self._last_mute = now

        try:
            current = self.device.read_mute()
        except (DeviceError, SafetyError, TransportError) as exc:
            self._warn_once("mute-read", f"could not read mute: {exc}")
            return

        target = not current
        if target is False:
            self._warn_once(
                "unmute", "unmuting may make audio audible", always=True
            )
        if self.settings.dry_run:
            self._log(f"[dry-run] mute would be {'on' if target else 'off'}")
            return
        try:
            actual = self.device.write_mute(target)
        except (DeviceError, SafetyError, TransportError) as exc:
            self._warn_once("mute-write", f"could not set mute: {exc}")
            return
        self._log(f"mute {'on' if actual else 'off'}")

    def _handle_wheel(self, delta: int, now: float) -> None:
        channel = self.active
        try:
            current_db = self.device.read_channel(channel.key).db
        except (DeviceError, SafetyError, TransportError) as exc:
            self._warn_once("volume-read", f"could not read volume: {exc}")
            return

        # ALSERV: new_attenuation = current_attenuation - delta.
        target_db = current_db - delta
        if not channel.min_db <= target_db <= channel.max_db:
            limit = "maximum" if delta > 0 else "minimum"
            self._warn_once(
                f"limit-{channel.key}",
                f"{channel.label} is already at its {limit} level; "
                "click ignored",
            )
            return
        if target_db == current_db:
            return

        louder = target_db < current_db
        target_level = db_to_level(channel, target_db)

        if louder and not self.settings.allow_increase:
            self._warn_once(
                "increase",
                f"louder click ignored for {channel.label}; "
                "pass --allow-increase to permit volume increases",
            )
            return
        if target_level > VERY_LOUD_LEVEL and not self.settings.allow_very_loud:
            self._warn_once(
                "very-loud",
                f"{channel.label} target level {target_level} is above "
                f"{VERY_LOUD_LEVEL}; pass --yes-very-loud to permit it",
            )
            return

        if self.settings.dry_run:
            self._log(
                f"[dry-run] {channel.label}: {current_db} -> {target_db} dB "
                f"(level {target_level}/{LEVEL_MAX})"
            )
            self._set_deadline(now)
            return

        try:
            result = self.device.write_channel_db(channel.key, target_db)
        except (DeviceError, SafetyError, TransportError) as exc:
            self._warn_once("volume-write", f"could not change volume: {exc}")
            return

        direction = "louder" if louder else "quieter"
        self._log(
            f"{channel.label}: {direction} to level {result.level}/100 "
            f"({result.db} dB attenuation)"
        )
        self._set_deadline(now)

    # -- helpers ---------------------------------------------------------

    def _set_active(self, channel: p.Channel, now: float) -> None:
        self.active = channel
        self._set_deadline(now)

    def _set_deadline(self, now: float) -> None:
        if self.active.is_master:
            self._deadline = None
        else:
            self._deadline = now + self.settings.channel_timeout_seconds

    def _warn_once(self, key: str, message: str, *, always: bool = False) -> None:
        now = self._clock()
        last = self._last_warning.get(key)
        if (
            not always
            and last is not None
            and now - last < self.settings.warning_interval_seconds
        ):
            return
        self._last_warning[key] = now
        self._warn_output(message)


__all__ = ["Follower", "FollowerSettings"]
