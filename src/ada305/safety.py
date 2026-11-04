"""Volume-level policy for ADA305 writes.

The protocol works in dB attenuation, where a larger value is quieter.  The
user-facing scale is the inverse: **level 0 is minimum volume** (maximum
attenuation) and **level 100 is maximum volume** (minimum attenuation).

Levels above :data:`VERY_LOUD_LEVEL` (90) require an explicit
``allow_very_loud`` / ``--yes-very-loud`` opt-in.  No function here clamps a
value: anything outside 0..100 is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import Channel, ProtocolError, encode_volume, resolve_channel

LEVEL_MIN = 0
LEVEL_MAX = 100
VERY_LOUD_LEVEL = 90

_SPAN = LEVEL_MAX - LEVEL_MIN


class SafetyError(Exception):
    """Base class for refused volume writes."""


class UnknownChannelError(SafetyError):
    pass


class MirroredChannelError(SafetyError):
    pass


class RangeError(SafetyError):
    pass


class VeryLoudNotAllowed(SafetyError):
    pass


def _require_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RangeError(f"{what} must be an integer, not {type(value).__name__}")
    return value


def _round_half_up(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def level_to_db(channel: Channel, level: int) -> int:
    """Map a 0..100 volume level to the channel's attenuation in dB.

    Level 0 is the quietest documented value (``channel.max_db``); level 100
    is the loudest (``channel.min_db``).
    """

    level = _require_int(level, "volume level")
    if not LEVEL_MIN <= level <= LEVEL_MAX:
        raise RangeError(
            f"volume level {level} is outside {LEVEL_MIN}..{LEVEL_MAX}; "
            "refusing to clamp"
        )
    span = channel.max_db - channel.min_db
    return channel.max_db - _round_half_up(level * span, _SPAN)


def db_to_level(channel: Channel, db: int) -> int:
    """Map a channel attenuation in dB to the nearest user-facing level.

    The scale has more levels than the hardware has attenuation steps, so
    several levels can share a dB value.  This returns the lower of the
    equivalent levels; the CLI reports the effective level after a write.
    """

    db = _require_int(db, "attenuation")
    if not channel.min_db <= db <= channel.max_db:
        raise RangeError(
            f"{channel.label} attenuation {db} dB is outside "
            f"{channel.min_db}..{channel.max_db}"
        )
    span = channel.max_db - channel.min_db
    level = ((channel.max_db - db) * _SPAN) // span
    return max(LEVEL_MIN, min(LEVEL_MAX, level))


@dataclass(frozen=True)
class VolumePlan:
    """A validated, not-yet-executed volume change."""

    channel: Channel
    current_db: int
    current_level: int
    requested_level: int
    requested_db: int
    allow_very_loud: bool = False

    @property
    def is_noop(self) -> bool:
        """True when the requested level maps to the current attenuation."""

        return self.requested_db == self.current_db

    @property
    def is_louder(self) -> bool:
        """True when the requested attenuation is lower (more loudness)."""

        return self.requested_db < self.current_db

    @property
    def is_quieter(self) -> bool:
        return self.requested_db > self.current_db

    @property
    def delta_db(self) -> int:
        """Signed change in attenuation; negative means louder."""

        return self.requested_db - self.current_db

    @property
    def is_very_loud(self) -> bool:
        return self.requested_level > VERY_LOUD_LEVEL

    @property
    def encoded(self) -> bytes:
        return encode_volume(self.requested_db)

    def describe(self) -> str:
        if self.is_noop:
            return (
                f"no change (level {self.requested_level}, "
                f"{self.requested_db} dB attenuation)"
            )
        direction = "louder" if self.is_louder else "quieter"
        return (
            f"{direction}: level {self.current_level} -> "
            f"{self.requested_level} "
            f"({self.current_db} -> {self.requested_db} dB attenuation)"
        )


def plan_volume(
    channel_name: str,
    requested_level: int,
    current_db: int,
    *,
    allow_very_loud: bool = False,
) -> VolumePlan:
    """Validate a requested volume level and return a plan.

    Raises :class:`SafetyError` (or a subclass) when the request must not be
    sent.  The caller is responsible for executing the plan; the device layer
    re-reads the current value immediately before writing.
    """

    try:
        channel = resolve_channel(channel_name)
    except KeyError as exc:
        raise UnknownChannelError(str(exc)) from None

    requested_level = _require_int(requested_level, "volume level")
    current_db = _require_int(current_db, "current attenuation")

    if not channel.writable:
        assert channel.mirror_of is not None
        raise MirroredChannelError(
            f"{channel.label} is not independently writable on the ADA305; "
            f"it mirrors {channel.mirror_of}. Use "
            f"'--channel {channel.mirror_of}' instead."
        )

    if not channel.min_db <= current_db <= channel.max_db:
        raise RangeError(
            f"current {channel.label} attenuation {current_db} dB is outside "
            f"the documented range {channel.min_db}..{channel.max_db}; "
            "refusing to write until this is understood"
        )

    if not LEVEL_MIN <= requested_level <= LEVEL_MAX:
        raise RangeError(
            f"volume level {requested_level} is outside "
            f"{LEVEL_MIN}..{LEVEL_MAX}; refusing to clamp"
        )

    requested_db = level_to_db(channel, requested_level)
    current_level = db_to_level(channel, current_db)

    # A no-op transmits nothing, so it does not need the very-loud opt-in.
    if (
        requested_db != current_db
        and requested_level > VERY_LOUD_LEVEL
        and not allow_very_loud
    ):
        raise VeryLoudNotAllowed(
            f"volume level {requested_level} is above {VERY_LOUD_LEVEL}; "
            "pass --yes-very-loud to permit it"
        )

    return VolumePlan(
        channel=channel,
        current_db=current_db,
        current_level=current_level,
        requested_level=requested_level,
        requested_db=requested_db,
        allow_very_loud=allow_very_loud,
    )


def describe_limits(channel: Channel) -> str:
    """Human-readable user-facing range for status output."""

    return f"level {LEVEL_MIN}..{LEVEL_MAX} (0 = minimum, 100 = maximum)"


__all__ = [
    "LEVEL_MAX",
    "LEVEL_MIN",
    "MirroredChannelError",
    "ProtocolError",
    "RangeError",
    "SafetyError",
    "UnknownChannelError",
    "VERY_LOUD_LEVEL",
    "VeryLoudNotAllowed",
    "VolumePlan",
    "db_to_level",
    "describe_limits",
    "level_to_db",
    "plan_volume",
]
