"""Wire protocol constants and codecs for the Altec Lansing ADA305.

All facts below were recovered by static analysis of the Windows AMS24 stack:

* ``al60.sys`` (the ADA305 kernel driver) builds ``URB_FUNCTION_CLASS_INTERFACE``
  control transfers.  Its user request structure is::

      +0x00  bRequest   (1 byte)
      +0x02  wValue     (2 bytes, little endian)  -> register address
      +0x04  wIndex     (2 bytes, little endian)  -> 0xF01 in the driver
      +0x06  wLength    (2 bytes, little endian)
      +0x08  data pointer (OUT requests only)

* ``ALSERV.exe`` issues the actual register I/O:

      read register : bRequest 8, class/interface IN
      write register: bRequest 7, class/interface OUT
      read state    : bRequest 6, class/interface IN,  wValue 0, wIndex 0xA01
      write state   : bRequest 5, class/interface OUT, wValue 0, wIndex 0xA01

* The original ``wIndex`` is ``0xF01``.  On Linux, usbfs masks wIndex to its
  low byte for interface-recipient requests and looks up that interface.  The
  ADA305 has only interface 0, so ``0xF01`` is rejected by the kernel with
  ``-ENOENT`` before it ever reaches the device.  The device ignores wIndex for
  these register requests (verified live), so this library uses ``0x0000``.

Volume registers hold **attenuation**, not gain.  The wire value is two bytes,
little endian, and is always ``00 dd`` where ``dd`` is the attenuation in dB.
A larger value means a *quieter* speaker.  The INF files call the value at
minimum volume ``Min`` and the value at maximum volume ``Max``; that naming is
inverted relative to attenuation, so the constants below spell it out.

See ``docs/PROTOCOL.md`` for the full register map, evidence, and the
interrupt-report format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional, Tuple

# ---------------------------------------------------------------------------
# Device identity
# ---------------------------------------------------------------------------

VID_ALTEC: Final = 0x04D2
PID_ADA305: Final = 0x0305
PID_ADA495: Final = 0xFF05  # ALSERV treats product 5/0x305 and 0xFF05 alike

# Other Altec models recognised by the same driver / profile set.
KNOWN_PRODUCTS: Final = {
    0xFF61: "ADA310",
    0xFF60: "ADA600",
    0x0305: "ADA305",
    0xFF05: "ADA495",
    0xFF33: "ADA315 / ADA355",
}

# ---------------------------------------------------------------------------
# Control-transfer setup
# ---------------------------------------------------------------------------

# bmRequestType: direction | type=class (0b10) | recipient=interface (0b01)
BM_CLASS_IFACE_IN: Final = 0xA1
BM_CLASS_IFACE_OUT: Final = 0x21

REQ_REGISTER_READ: Final = 8
REQ_REGISTER_WRITE: Final = 7
REQ_STATE_READ: Final = 6
REQ_STATE_WRITE: Final = 5

# Linux-safe value.  The Windows driver used WINDEX_WINDOWS_DRIVER.
WINDEX: Final = 0x0000
WINDEX_WINDOWS_DRIVER: Final = 0x0F01

# ---------------------------------------------------------------------------
# Registers
# ---------------------------------------------------------------------------

REG_MUTE: Final = 0x0100
REG_VOLUME_BASE: Final = 0x0200
REG_INPUT_CONTROL: Final = 0x0300  # 1-byte write-only, source/input routing
REG_AUX_CONTROL: Final = 0x0500    # 1-byte write-only, model-specific setup
REG_MODE_STATE: Final = 0xA500     # 1-byte read/write mode state

# Hardware limits taken from ALADA.INF / acs495.inf for ADA305 / ADA495.
# Register value is attenuation in dB; larger is quieter.
HW_MASTER_MIN_DB: Final = 25   # 0x19: maximum volume (do not use; see safety)
HW_MASTER_MAX_DB: Final = 96   # 0x60: minimum volume (most attenuation)
HW_CHANNEL_MIN_DB: Final = 0   # 0x00: maximum channel level
HW_CHANNEL_MAX_DB: Final = 31  # 0x1F: minimum channel level

# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Channel:
    """A logical ADA305 channel and its volume register."""

    key: str
    label: str
    register: int
    min_db: int
    max_db: int
    writable: bool = True
    mirror_of: Optional[str] = None
    aliases: Tuple[str, ...] = ()

    @property
    def is_master(self) -> bool:
        return self.key == "master"


# Order and names follow the ADA305 AddReg section of ALADA.INF:
# Master, Left, Right, Center, Sub, SurLeft, SurRight.
# ALSERV reads/writes register 0x200+index; for ADA305 it maps channel 6
# (Surround right) onto channel 5 (Surround left), so 0x206 is not an
# independent ADA305 control.
CHANNELS: Final[Tuple[Channel, ...]] = (
    Channel("master", "Master", REG_VOLUME_BASE + 0,
            HW_MASTER_MIN_DB, HW_MASTER_MAX_DB,
            aliases=("m", "main")),
    Channel("left", "Left", REG_VOLUME_BASE + 1,
            HW_CHANNEL_MIN_DB, HW_CHANNEL_MAX_DB,
            aliases=("front-left", "front_left", "fl", "l")),
    Channel("right", "Right", REG_VOLUME_BASE + 2,
            HW_CHANNEL_MIN_DB, HW_CHANNEL_MAX_DB,
            aliases=("front-right", "front_right", "fr", "r")),
    Channel("center", "Center", REG_VOLUME_BASE + 3,
            HW_CHANNEL_MIN_DB, HW_CHANNEL_MAX_DB,
            aliases=("c",)),
    Channel("sub", "Subwoofer", REG_VOLUME_BASE + 4,
            HW_CHANNEL_MIN_DB, HW_CHANNEL_MAX_DB,
            aliases=("subwoofer", "sub-woofer", "lfe", "sw")),
    Channel("surround-left", "Surround left", REG_VOLUME_BASE + 5,
            HW_CHANNEL_MIN_DB, HW_CHANNEL_MAX_DB,
            aliases=("surround_left", "sl", "surleft")),
    Channel("surround-right", "Surround right", REG_VOLUME_BASE + 6,
            HW_CHANNEL_MIN_DB, HW_CHANNEL_MAX_DB,
            writable=False, mirror_of="surround-left",
            aliases=("surround_right", "sr", "surright")),
)

_BY_ALIAS = {}
for _channel in CHANNELS:
    _BY_ALIAS[_channel.key] = _channel
    _BY_ALIAS[_channel.label.lower()] = _channel
    for _alias in _channel.aliases:
        _BY_ALIAS[_alias] = _channel


def _normalise(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def resolve_channel(name: str) -> Channel:
    """Return the channel for a user-supplied name or alias.

    Raises :class:`KeyError` for unknown names.  Aliases are matched
    case-insensitively, with spaces/underscores treated as hyphens.
    """

    if not isinstance(name, str):
        raise TypeError("channel name must be a string")
    key = _normalise(name)
    # Try the normalised form, then a few common spellings.
    candidates = (key, key.replace("-", ""), key.replace("-", "_"))
    for candidate in candidates:
        channel = _BY_ALIAS.get(candidate)
        if channel is not None:
            return channel
    known = ", ".join(channel.key for channel in CHANNELS)
    raise KeyError(f"unknown channel {name!r}; known channels: {known}")


# ---------------------------------------------------------------------------
# Codecs
# ---------------------------------------------------------------------------


class ProtocolError(ValueError):
    """A register value did not match the documented encoding."""


def _require_int(value: object, what: str) -> int:
    # bool is an int subclass; accepting it would make True mean 1 dB.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be an integer, not {type(value).__name__}")
    return value


def encode_volume(db: int) -> bytes:
    """Encode an attenuation value as the two-byte wire form ``00 dd``."""

    db = _require_int(db, "volume attenuation")
    if not 0 <= db <= 0xFF:
        raise ProtocolError(
            f"volume attenuation {db} does not fit in one byte (0..255)"
        )
    return bytes((0x00, db))


def decode_volume(raw: bytes) -> int:
    """Decode the two-byte wire form into an attenuation value in dB."""

    raw = bytes(raw)
    if len(raw) != 2:
        raise ProtocolError(
            f"volume register must be 2 bytes, got {len(raw)}: {raw.hex(' ')}"
        )
    if raw[0] != 0x00:
        raise ProtocolError(
            "unexpected volume high byte "
            f"0x{raw[0]:02X} in {raw.hex(' ')}; refusing to guess"
        )
    return raw[1]


def encode_mute(muted: bool) -> bytes:
    """Encode the mute register: 0 = unmuted, 1 = muted."""

    if not isinstance(muted, bool):
        raise TypeError("muted must be a bool")
    return bytes((1 if muted else 0,))


def decode_mute(raw: bytes) -> bool:
    """Decode the mute register; values other than 0/1 are rejected."""

    raw = bytes(raw)
    if len(raw) != 1:
        raise ProtocolError(
            f"mute register must be 1 byte, got {len(raw)}: {raw.hex(' ')}"
        )
    if raw[0] not in (0, 1):
        raise ProtocolError(
            f"unexpected mute value 0x{raw[0]:02X}; refusing to guess"
        )
    return bool(raw[0])


def decode_mode(state_byte: int, mode_byte: int) -> int:
    """Combine the two state reads into the logical mode number.

    ALSERV reads ``bRequest 6`` (one byte) and then register ``0xA500``.  A
    state byte other than 1 is itself the mode.  A state byte of 1 is
    ambiguous and is resolved by the ``0xA500`` byte: 0 -> mode 0,
    non-zero -> mode 1.  The ADA305 INF documents modes 0, 1 and 3.
    """

    state_byte = _require_int(state_byte, "state byte")
    mode_byte = _require_int(mode_byte, "mode byte")
    for value, what in ((state_byte, "state byte"), (mode_byte, "mode byte")):
        if not 0 <= value <= 0xFF:
            raise ProtocolError(f"{what} {value} does not fit in one byte")
    if state_byte == 1:
        return 1 if mode_byte else 0
    return state_byte


# ---------------------------------------------------------------------------
# Interrupt-IN reports
# ---------------------------------------------------------------------------

# Report flags seen in ALSERV's 7-byte ReadFileEx buffer.
FLAG_MUTE_TOGGLE: Final = 0x01
FLAG_MODE_CYCLE: Final = 0x02   # ignored for ADA305/ADA495
FLAG_UNMUTE: Final = 0x08       # non-ADA305 models
FLAG_INPUT_4: Final = 0x10      # non-ADA305 input selection
FLAG_INPUT_3: Final = 0x20      # non-ADA305 input selection
FLAG_INPUT_7: Final = 0x40      # non-ADA305 input selection
FLAG_RECONNECT: Final = 0x80    # re-read all state


@dataclass(frozen=True)
class InterruptReport:
    """Decoded physical-control report from endpoint 0x81.

    ``volume_delta`` is a signed attenuation delta.  ALSERV computes the new
    attenuation as ``current - delta``, so a positive delta means *louder*.
    The host is expected to apply the change with a control write; the device
    does not apply it itself.
    """

    flags: int
    volume_delta: int
    raw: bytes
    extra: bytes = b""

    @property
    def mute_toggle(self) -> bool:
        return bool(self.flags & FLAG_MUTE_TOGGLE)

    @property
    def mode_cycle(self) -> bool:
        return bool(self.flags & FLAG_MODE_CYCLE)

    @property
    def unmute_request(self) -> bool:
        return bool(self.flags & FLAG_UNMUTE)

    @property
    def reconnect(self) -> bool:
        return bool(self.flags & FLAG_RECONNECT)

    @property
    def input_select(self) -> int:
        """Return the encoded input-selection bits (0x10/0x20/0x40)."""

        return self.flags & 0x70


def decode_interrupt_report(raw: bytes) -> InterruptReport:
    """Decode a report from interrupt-IN endpoint 0x81.

    The ADA305 endpoint has a 2-byte max packet and the old USB305 profile
    specifies ``ReportSize=2``; ALSERV nevertheless reads 7 bytes because the
    same code serves 6-byte remote-control models.  Only bytes 0 and 1 are
    meaningful for the ADA305.  A 2-byte or longer report is accepted.
    """

    raw = bytes(raw)
    if len(raw) < 2:
        raise ProtocolError(
            f"interrupt report must be at least 2 bytes, got {len(raw)}: "
            f"{raw.hex(' ')}"
        )
    delta = raw[1] if raw[1] < 0x80 else raw[1] - 0x100
    return InterruptReport(
        flags=raw[0], volume_delta=delta, raw=raw, extra=raw[2:]
    )
