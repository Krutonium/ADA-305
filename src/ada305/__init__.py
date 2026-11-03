"""Safe control library for the Altec Lansing ADA305 USB speaker control link.

The ADA305 exposes a vendor-specific USB interface whose only endpoint is an
interrupt-IN endpoint (physical buttons / volume knob).  All host-to-device
commands are class-interface control transfers, recovered from the Windows
driver ``al60.sys`` and the AMS24 service ``ALSERV.exe``.

The library is deliberately conservative: nothing is written unless the caller
asks for a specific write, and volume writes are validated against the
documented attenuation range and a loudness policy.  See ``docs/PROTOCOL.md``.
"""

from .device import Ada305, DeviceError, Status
from .protocol import (
    CHANNELS,
    Channel,
    PID_ADA305,
    PID_ADA495,
    VID_ALTEC,
    decode_interrupt_report,
    decode_mode,
    decode_mute,
    decode_volume,
    encode_mute,
    encode_volume,
    resolve_channel,
)
from .safety import SafetyError, VolumePlan, plan_volume
from .transport import PyUsbTransport, Transport, TransportError, TransportTimeout

__all__ = [
    "Ada305",
    "CHANNELS",
    "Channel",
    "DeviceError",
    "PID_ADA305",
    "PID_ADA495",
    "PyUsbTransport",
    "SafetyError",
    "Status",
    "Transport",
    "TransportError",
    "TransportTimeout",
    "VID_ALTEC",
    "VolumePlan",
    "decode_interrupt_report",
    "decode_mode",
    "decode_mute",
    "decode_volume",
    "encode_mute",
    "encode_volume",
    "plan_volume",
    "resolve_channel",
]
