"""Safe control library for the Altec Lansing ADA305 USB speaker control link.

The ADA305 exposes a vendor-specific USB interface whose only endpoint is an
interrupt-IN endpoint (physical buttons / volume knob).  All host-to-device
commands are class-interface control transfers, recovered from the Windows
driver ``al60.sys`` and the AMS24 service ``ALSERV.exe``.

Volumes are exposed as a 0..100 level: 0 is minimum volume, 100 is maximum.
The wire protocol still uses dB attenuation.  See ``docs/PROTOCOL.md``.
"""

__version__ = "0.3.0"

from .device import Ada305, DeviceError, Status, VolumeResult
from .follow import Follower, FollowerSettings
from .protocol import (
    CHANNELS,
    CONTROL_HOST,
    CONTROL_LOCAL,
    Channel,
    PID_ADA305,
    PID_ADA495,
    VID_ALTEC,
    decode_interrupt_report,
    decode_mode,
    decode_mute,
    decode_volume,
    encode_control,
    encode_mute,
    encode_volume,
    resolve_channel,
)
from .safety import (
    LEVEL_MAX,
    LEVEL_MIN,
    VERY_LOUD_LEVEL,
    SafetyError,
    VeryLoudNotAllowed,
    VolumePlan,
    db_to_level,
    level_to_db,
    plan_volume,
)
from .transport import (
    PyUsbTransport,
    Transport,
    TransportError,
    TransportOverflow,
    TransportTimeout,
)

__all__ = [
    "Ada305",
    "CHANNELS",
    "CONTROL_HOST",
    "CONTROL_LOCAL",
    "Channel",
    "DeviceError",
    "Follower",
    "FollowerSettings",
    "LEVEL_MAX",
    "LEVEL_MIN",
    "PID_ADA305",
    "PID_ADA495",
    "PyUsbTransport",
    "SafetyError",
    "Status",
    "Transport",
    "TransportError",
    "TransportOverflow",
    "TransportTimeout",
    "VERY_LOUD_LEVEL",
    "VID_ALTEC",
    "VeryLoudNotAllowed",
    "VolumePlan",
    "VolumeResult",
    "__version__",
    "db_to_level",
    "decode_interrupt_report",
    "decode_mode",
    "decode_mute",
    "decode_volume",
    "encode_control",
    "encode_mute",
    "encode_volume",
    "level_to_db",
    "plan_volume",
    "resolve_channel",
]
