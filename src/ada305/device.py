"""High-level ADA305 device operations.

Reads are always safe.  Volume writes go through :mod:`ada305.safety`, use the
0..100 user-facing scale, and are verified by reading the register back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from . import protocol as p
from .safety import (
    SafetyError,
    VolumePlan,
    db_to_level,
    plan_volume,
)
from .transport import Transport


class DeviceError(Exception):
    """The device returned data that does not match the protocol."""


@dataclass(frozen=True)
class ChannelReading:
    channel: p.Channel
    raw: bytes
    db: int
    mirrored: bool = False
    source: Optional[str] = None


@dataclass(frozen=True)
class Status:
    mute: bool
    channels: Mapping[str, ChannelReading]
    mode_state: int
    mode_byte: int
    mode: int

    def volume_db(self, channel: str) -> int:
        return self.channels[channel].db

    def volume_level(self, channel: str) -> int:
        reading = self.channels[channel]
        return db_to_level(reading.channel, reading.db)


@dataclass(frozen=True)
class VolumeResult:
    """The verified state after a volume operation."""

    level: int
    db: int
    changed: bool


class Ada305:
    """One ADA305 control link over a :class:`~ada305.transport.Transport`."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    @property
    def interrupt_packet_size(self) -> int:
        size = getattr(self.transport, "interrupt_packet_size", 2)
        try:
            size = int(size)
        except (TypeError, ValueError):
            return 2
        return size if size > 0 else 2

    # -- register helpers ------------------------------------------------

    def read_register(self, register: int, length: int) -> bytes:
        if not isinstance(register, int) or isinstance(register, bool):
            raise DeviceError("register address must be an integer")
        if not 0 <= register <= 0xFFFF:
            raise DeviceError(f"register 0x{register:X} is not a 16-bit address")
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise DeviceError("length must be a non-negative integer")
        raw = self.transport.read_control(p.REQ_REGISTER_READ, register, length)
        raw = bytes(raw)
        if len(raw) != length:
            raise DeviceError(
                f"register 0x{register:04X}: expected {length} byte(s), "
                f"got {len(raw)} ({raw.hex(' ')})"
            )
        return raw

    def read_state_byte(self) -> int:
        raw = self.transport.read_control(p.REQ_STATE_READ, 0x0000, 1)
        raw = bytes(raw)
        if len(raw) != 1:
            raise DeviceError(
                f"state read expected 1 byte, got {len(raw)} "
                f"({raw.hex(' ')})"
            )
        return raw[0]

    # -- reads -----------------------------------------------------------

    def read_mute(self) -> bool:
        raw = self.read_register(p.REG_MUTE, 1)
        try:
            return p.decode_mute(raw)
        except p.ProtocolError as exc:
            raise DeviceError(str(exc)) from exc

    def _read_volume_raw(self, channel: p.Channel) -> bytes:
        return self.read_register(channel.register, 2)

    def read_channel(self, channel_name: str) -> ChannelReading:
        try:
            channel = p.resolve_channel(channel_name)
        except KeyError as exc:
            raise DeviceError(str(exc)) from None

        if channel.mirror_of is not None:
            source = p.resolve_channel(channel.mirror_of)
            raw = self._read_volume_raw(source)
            return ChannelReading(
                channel=channel,
                raw=raw,
                db=self._decode_db(raw, source),
                mirrored=True,
                source=source.key,
            )

        raw = self._read_volume_raw(channel)
        return ChannelReading(
            channel=channel, raw=raw, db=self._decode_db(raw, channel)
        )

    def _decode_db(self, raw: bytes, channel: p.Channel) -> int:
        try:
            db = p.decode_volume(raw)
        except p.ProtocolError as exc:
            raise DeviceError(
                f"{channel.label} register 0x{channel.register:03X}: {exc}"
            ) from exc
        if not channel.min_db <= db <= channel.max_db:
            raise DeviceError(
                f"{channel.label} reports {db} dB attenuation, outside the "
                f"documented range {channel.min_db}..{channel.max_db}; "
                "refusing to interpret it"
            )
        return db

    def read_mode(self) -> int:
        state = self.read_state_byte()
        raw_mode = self.read_register(p.REG_MODE_STATE, 1)
        try:
            return p.decode_mode(state, raw_mode[0])
        except p.ProtocolError as exc:
            raise DeviceError(str(exc)) from exc

    def read_status(self) -> Status:
        mute = self.read_mute()
        channels: Dict[str, ChannelReading] = {}
        for channel in p.CHANNELS:
            if channel.mirror_of is None:
                channels[channel.key] = self.read_channel(channel.key)
        for channel in p.CHANNELS:
            if channel.mirror_of is not None:
                source = channels[channel.mirror_of]
                channels[channel.key] = ChannelReading(
                    channel=channel,
                    raw=source.raw,
                    db=source.db,
                    mirrored=True,
                    source=channel.mirror_of,
                )
        state = self.read_state_byte()
        raw_mode = self.read_register(p.REG_MODE_STATE, 1)
        try:
            mode = p.decode_mode(state, raw_mode[0])
        except p.ProtocolError as exc:
            raise DeviceError(str(exc)) from exc
        return Status(
            mute=mute,
            channels=channels,
            mode_state=state,
            mode_byte=raw_mode[0],
            mode=mode,
        )

    # -- writes ----------------------------------------------------------

    def _begin_host_control(self) -> None:
        """Bracket a host volume write (ALSERV writes 0x0500 <- 2)."""

        self.transport.write_control(
            p.REQ_REGISTER_WRITE,
            p.REG_CONTROL,
            p.encode_control(p.CONTROL_HOST),
        )

    def return_control(self) -> None:
        """Give the speaker's local buttons and knob control again.

        ALSERV writes register ``0x0500 = 3`` immediately after every
        host-initiated volume change.  Without that release the speaker can
        stay in host-control mode and ignore its own controls until it is
        power cycled.
        """

        self.transport.write_control(
            p.REQ_REGISTER_WRITE,
            p.REG_CONTROL,
            p.encode_control(p.CONTROL_LOCAL),
        )

    def write_mute(self, muted: bool) -> bool:
        """Set mute, verify it, and return the read-back state."""

        if not isinstance(muted, bool):
            raise DeviceError("muted must be a bool")
        current = self.read_mute()
        if current == muted:
            return current
        target = p.encode_mute(muted)
        self.transport.write_control(p.REQ_REGISTER_WRITE, p.REG_MUTE, target)
        actual = self.read_mute()
        if actual != muted:
            raise DeviceError(
                f"mute read-back mismatch: requested {muted}, got {actual}"
            )
        return actual

    def prepare_volume(
        self,
        channel: str,
        requested_level: int,
        *,
        allow_very_loud: bool = False,
    ) -> VolumePlan:
        """Read the current value and validate a proposed 0..100 level."""

        try:
            ch = p.resolve_channel(channel)
        except KeyError as exc:
            raise SafetyError(str(exc)) from None
        if not ch.writable:
            # Let plan_volume produce the mirrored-channel message without
            # reading a register that ADA305 does not use independently.
            return plan_volume(
                ch.key,
                requested_level,
                current_db=ch.max_db,
                allow_very_loud=allow_very_loud,
            )
        current_db = self.read_channel(ch.key).db
        return plan_volume(
            ch.key,
            requested_level,
            current_db,
            allow_very_loud=allow_very_loud,
        )

    def execute_volume(self, plan: VolumePlan) -> VolumeResult:
        """Execute a previously validated plan and verify the read-back.

        The current value is re-read immediately before the write.  If it has
        changed since the plan was made, the write is refused.
        """

        if plan.is_noop:
            return VolumeResult(plan.current_level, plan.current_db, False)

        actual_current = self.read_channel(plan.channel.key).db
        if actual_current != plan.current_db:
            raise SafetyError(
                f"{plan.channel.label} changed from {plan.current_db} to "
                f"{actual_current} dB while planning; refusing to write. "
                "Read the status again."
            )

        self._begin_host_control()
        try:
            self.transport.write_control(
                p.REQ_REGISTER_WRITE, plan.channel.register, plan.encoded
            )
        finally:
            # Always hand control back, even if the volume write failed, so a
            # physical knob/button cannot be left locked out.
            self.return_control()
        actual_db = self.read_channel(plan.channel.key).db
        if actual_db != plan.requested_db:
            raise DeviceError(
                f"{plan.channel.label} read-back mismatch: requested "
                f"{plan.requested_db} dB, got {actual_db} dB"
            )
        return VolumeResult(
            level=db_to_level(plan.channel, actual_db),
            db=actual_db,
            changed=True,
        )

    def set_volume(
        self,
        channel: str,
        requested_level: int,
        *,
        allow_very_loud: bool = False,
    ) -> VolumeResult:
        """Prepare and execute a volume change in one call."""

        plan = self.prepare_volume(
            channel,
            requested_level,
            allow_very_loud=allow_very_loud,
        )
        return self.execute_volume(plan)

    # -- interrupt monitor ----------------------------------------------

    def read_interrupt(
        self,
        length: Optional[int] = None,
        timeout_ms: int = 5000,
    ) -> p.InterruptReport:
        if length is None:
            length = self.interrupt_packet_size
        raw = self.transport.read_interrupt(length, timeout_ms)
        return p.decode_interrupt_report(raw)


__all__ = [
    "Ada305",
    "ChannelReading",
    "DeviceError",
    "Status",
    "VolumeResult",
]
