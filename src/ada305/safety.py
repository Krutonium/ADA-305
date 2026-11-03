"""Loudness policy for ADA305 volume writes.

The hardware range is not enough on its own.  The ADA305 master register is an
attenuation value where **smaller means louder**, and ``25`` is the absolute
maximum volume documented by Altec.  This module refuses to write that value,
puts an extra guard on the loud end of the range, and refuses to increase
loudness unless the caller passes an explicit opt-in.

No function here clamps or coerces an out-of-range value: it raises instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import (
    Channel,
    HW_MASTER_MAX_DB,
    HW_MASTER_MIN_DB,
    ProtocolError,
    encode_volume,
    resolve_channel,
)

# The hardware maximum is 25 dB attenuation.  The tool never writes it.
MASTER_WRITABLE_MIN_DB = HW_MASTER_MIN_DB + 1  # 26

# Values below this are treated as the loud zone and require --allow-loud even
# when the change is in the quieter direction.  36 dB is deliberately
# conservative: it is 11 dB away from the absolute maximum.
MASTER_LOUD_THRESHOLD_DB = 36


class SafetyError(Exception):
    """Base class for refused volume writes."""


class UnknownChannelError(SafetyError):
    pass


class MirroredChannelError(SafetyError):
    pass


class RangeError(SafetyError):
    pass


class LouderNotAllowed(SafetyError):
    pass


class LoudNotAllowed(SafetyError):
    pass


@dataclass(frozen=True)
class VolumePlan:
    """A validated, not-yet-executed volume change."""

    channel: Channel
    current_db: int
    requested_db: int
    allow_louder: bool = False
    allow_loud: bool = False

    @property
    def is_noop(self) -> bool:
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
    def encoded(self) -> bytes:
        return encode_volume(self.requested_db)

    def describe(self) -> str:
        if self.is_noop:
            return f"no change ({self.requested_db} dB attenuation)"
        direction = "LOUDER" if self.is_louder else "quieter"
        return (
            f"{direction} by {abs(self.delta_db)} dB "
            f"({self.current_db} -> {self.requested_db} dB attenuation)"
        )


def _require_int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RangeError(f"{what} must be an integer, not {type(value).__name__}")
    return value


def plan_volume(
    channel_name: str,
    requested_db: int,
    current_db: int,
    *,
    allow_louder: bool = False,
    allow_loud: bool = False,
) -> VolumePlan:
    """Validate a requested volume change and return a plan.

    Raises :class:`SafetyError` (or a subclass) when the request must not be
    sent.  The caller is responsible for actually executing the plan and for
    confirming the current value again immediately before writing.
    """

    try:
        channel = resolve_channel(channel_name)
    except KeyError as exc:
        raise UnknownChannelError(str(exc)) from None

    requested_db = _require_int(requested_db, "requested attenuation")
    current_db = _require_int(current_db, "current attenuation")

    if not channel.writable:
        assert channel.mirror_of is not None
        raise MirroredChannelError(
            f"{channel.label} is not independently writable on the ADA305; "
            f"it mirrors {channel.mirror_of}. Use "
            f"'--channel {channel.mirror_of}' instead."
        )

    # Never guess about a value outside the documented hardware range.
    if not channel.min_db <= current_db <= channel.max_db:
        raise RangeError(
            f"current {channel.label} attenuation {current_db} dB is outside "
            f"the documented range {channel.min_db}..{channel.max_db}; "
            "refusing to write until this is understood"
        )

    if not channel.min_db <= requested_db <= channel.max_db:
        raise RangeError(
            f"requested {channel.label} attenuation {requested_db} dB is "
            f"outside the documented range {channel.min_db}.."
            f"{channel.max_db}; refusing to clamp"
        )

    if channel.is_master:
        if requested_db < MASTER_WRITABLE_MIN_DB:
            raise RangeError(
                f"{requested_db} dB is the hardware maximum volume on the "
                f"ADA305 (range {HW_MASTER_MIN_DB}..{HW_MASTER_MAX_DB}); "
                "the safety policy never writes the maximum"
            )
        if requested_db < MASTER_LOUD_THRESHOLD_DB and not allow_loud:
            raise LoudNotAllowed(
                f"{requested_db} dB is in the loud zone "
                f"(< {MASTER_LOUD_THRESHOLD_DB} dB attenuation); pass "
                "--allow-loud to permit it"
            )

    if requested_db < current_db and not allow_louder:
        raise LouderNotAllowed(
            f"requested {requested_db} dB is louder than the current "
            f"{current_db} dB (attenuation decreases by "
            f"{current_db - requested_db} dB); pass --allow-louder to permit it"
        )

    return VolumePlan(
        channel=channel,
        current_db=current_db,
        requested_db=requested_db,
        allow_louder=allow_louder,
        allow_loud=allow_loud,
    )


def describe_limits(channel: Channel) -> str:
    """Human-readable hardware range for status output."""

    if channel.is_master:
        return (
            f"{channel.min_db}..{channel.max_db} dB attenuation "
            f"(loudest allowed write: {MASTER_WRITABLE_MIN_DB} dB)"
        )
    return f"{channel.min_db}..{channel.max_db} dB attenuation"


__all__ = [
    "LoudNotAllowed",
    "LouderNotAllowed",
    "MASTER_LOUD_THRESHOLD_DB",
    "MASTER_WRITABLE_MIN_DB",
    "MirroredChannelError",
    "ProtocolError",
    "RangeError",
    "SafetyError",
    "UnknownChannelError",
    "VolumePlan",
    "describe_limits",
    "plan_volume",
]
