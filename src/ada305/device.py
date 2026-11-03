"""High-level ADA305 device operations.

Reads are always safe.  The only writes exposed here are mute and volume, and
both require an explicit ``confirm=True`` from the caller.  Volume writes also
go through :mod:`ada305.safety` and are read back for verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from . import protocol as p
from .safety import SafetyError, VolumePlan, plan_volume
from .transport import Transport


class DeviceError(Exception):
    """The device returned data that does not match the protocol."""


class WriteNotConfirmed(SafetyError):
    """A write was attempted without an explicit confirmation."""


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


class Ada305:
    """One ADA305 control link over a :class:`~ada305.transport.Transport`."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

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
        return ChannelReading(channel=channel, raw=raw, db=self._decode_db(raw, channel))

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

    def write_mute(self, muted: bool, *, confirm: bool = False) -> bool:
        """Set mute and verify it.  ``confirm`` must be True to write."""

        if not isinstance(muted, bool):
            raise DeviceError("muted must be a bool")
        current = self.read_mute()
        if current == muted:
            return current
        if not confirm:
            raise WriteNotConfirmed(
                "mute write not confirmed; pass confirm=True after user approval"
            )
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
        requested_db: int,
        *,
        allow_louder: bool = False,
        allow_loud: bool = False,
    ) -> VolumePlan:
        """Read the current value and validate a proposed change."""

        try:
            ch = p.resolve_channel(channel)
        except KeyError as exc:
            raise SafetyError(str(exc)) from None
        if not ch.writable:
            # Let plan_volume produce the mirrored-channel message without
            # reading a register that ADA305 does not use independently.
            return plan_volume(
                ch.key,
                requested_db,
                current_db=ch.min_db,
                allow_louder=allow_louder,
                allow_loud=allow_loud,
            )
        current = self.read_channel(ch.key).db
        return plan_volume(
            ch.key,
            requested_db,
            current,
            allow_louder=allow_louder,
            allow_loud=allow_loud,
        )

    def execute_volume(
        self, plan: VolumePlan, *, confirm: bool = False
    ) -> int:
        """Execute a previously validated plan and verify the read-back.

        The current value is re-read immediately before the write.  If it has
        changed since the plan was made, the write is refused.
        """

        if not confirm:
            raise WriteNotConfirmed(
                "volume write not confirmed; pass confirm=True after user "
                "approval"
            )
        if plan.is_noop:
            return plan.current_db

        actual_current = self.read_channel(plan.channel.key).db
        if actual_current != plan.current_db:
            raise SafetyError(
                f"{plan.channel.label} changed from {plan.current_db} to "
                f"{actual_current} dB while planning; refusing to write. "
                "Read the status again."
            )

        self.transport.write_control(
            p.REQ_REGISTER_WRITE, plan.channel.register, plan.encoded
        )
        actual = self.read_channel(plan.channel.key).db
        if actual != plan.requested_db:
            raise DeviceError(
                f"{plan.channel.label} read-back mismatch: requested "
                f"{plan.requested_db} dB, got {actual} dB"
            )
        return actual

    def set_volume(
        self,
        channel: str,
        requested_db: int,
        *,
        confirm: bool = False,
        allow_louder: bool = False,
        allow_loud: bool = False,
    ) -> int:
        """Prepare and execute a volume change in one call."""

        plan = self.prepare_volume(
            channel,
            requested_db,
            allow_louder=allow_louder,
            allow_loud=allow_loud,
        )
        return self.execute_volume(plan, confirm=confirm)

    # -- interrupt monitor ----------------------------------------------

    def read_interrupt(self, length: int = 7, timeout_ms: int = 5000) -> p.InterruptReport:
        raw = self.transport.read_interrupt(length, timeout_ms)
        return p.decode_interrupt_report(raw)


__all__ = [
    "Ada305",
    "ChannelReading",
    "DeviceError",
    "Status",
    "WriteNotConfirmed",
]
