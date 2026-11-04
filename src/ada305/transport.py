"""USB transport for the ADA305 control link.

The transport is deliberately thin: it exposes the two control-transfer
directions and a read from the interrupt-IN endpoint.  It does not configure,
reset, or claim the device except for the read-only interrupt monitor.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from typing import List, Optional, Protocol

import usb.core
import usb.util

from .protocol import (
    BM_CLASS_IFACE_IN,
    BM_CLASS_IFACE_OUT,
    PID_ADA305,
    VID_ALTEC,
    WINDEX,
)


class TransportError(Exception):
    """A USB transfer or device-open failure."""


class TransportTimeout(TransportError):
    """A read timed out (normal for the interrupt monitor when idle)."""


class TransportOverflow(TransportError):
    """The device sent more data than the transfer buffer could hold."""


class Transport(Protocol):
    """Minimal interface the device layer needs; easy to fake in tests."""

    interrupt_packet_size: int

    def read_control(self, request: int, value: int, length: int) -> bytes:
        ...

    def write_control(self, request: int, value: int, data: bytes) -> int:
        ...

    def read_interrupt(self, length: int, timeout_ms: int) -> bytes:
        ...


@dataclass(frozen=True)
class DeviceInfo:
    bus: int
    address: int
    id_vendor: int
    id_product: int
    product: Optional[str] = None
    manufacturer: Optional[str] = None

    def label(self) -> str:
        name = self.product or "Altec USB speaker"
        return (
            f"{self.id_vendor:04x}:{self.id_product:04x} "
            f"bus {self.bus:03d} address {self.address:03d}  {name}"
        )


def _safe_string(device: usb.core.Device, attr: str) -> Optional[str]:
    try:
        return getattr(device, attr)
    except (usb.core.USBError, ValueError, NotImplementedError):
        return None


def enumerate_devices(
    vid: int = VID_ALTEC, pid: int = PID_ADA305
) -> List[DeviceInfo]:
    """List matching USB devices without opening or configuring them."""

    infos: List[DeviceInfo] = []
    for device in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
        infos.append(
            DeviceInfo(
                bus=int(device.bus),
                address=int(device.address),
                id_vendor=int(device.idVendor),
                id_product=int(device.idProduct),
                product=_safe_string(device, "product"),
                manufacturer=_safe_string(device, "manufacturer"),
            )
        )
    return infos


class PyUsbTransport:
    """pyusb-backed transport for one ADA305."""

    INTERRUPT_ENDPOINT = 0x81
    INTERFACE = 0

    def __init__(
        self,
        device: usb.core.Device,
        *,
        timeout_ms: int = 2000,
    ) -> None:
        self._device = device
        self.timeout_ms = int(timeout_ms)
        self._claimed = False
        self._product = _safe_string(device, "product")
        self._interrupt_packet_size = self._discover_interrupt_packet_size()

    # -- construction ----------------------------------------------------

    @classmethod
    def open(
        cls,
        *,
        vid: int = VID_ALTEC,
        pid: int = PID_ADA305,
        bus: Optional[int] = None,
        address: Optional[int] = None,
        timeout_ms: int = 2000,
    ) -> "PyUsbTransport":
        devices = list(
            usb.core.find(find_all=True, idVendor=vid, idProduct=pid)
        )
        if bus is not None:
            devices = [d for d in devices if int(d.bus) == int(bus)]
        if address is not None:
            devices = [d for d in devices if int(d.address) == int(address)]
        if not devices:
            where = ""
            if bus is not None or address is not None:
                where = f" at bus {bus} address {address}"
            raise TransportError(
                f"no {vid:04x}:{pid:04x} device found{where}; check that it "
                "is plugged in and that you can open /dev/bus/usb/... "
                "(see README for permissions)"
            )
        if len(devices) > 1:
            options = ", ".join(
                f"bus {d.bus:03d} address {d.address:03d}" for d in devices
            )
            raise TransportError(
                f"multiple {vid:04x}:{pid:04x} devices found ({options}); "
                "select one with --bus/--address or use list-devices"
            )
        return cls(devices[0], timeout_ms=timeout_ms)

    # -- description -----------------------------------------------------

    @property
    def bus(self) -> int:
        return int(self._device.bus)

    @property
    def address(self) -> int:
        return int(self._device.address)

    @property
    def description(self) -> str:
        name = self._product or "Altec ADA305"
        return (
            f"{name} ({VID_ALTEC:04x}:{PID_ADA305:04x}, "
            f"bus {self.bus:03d} address {self.address:03d})"
        )

    @property
    def interrupt_packet_size(self) -> int:
        """The interrupt-IN endpoint's maximum packet size (2 on ADA305)."""

        return self._interrupt_packet_size

    def _discover_interrupt_packet_size(self) -> int:
        try:
            for config in self._device:
                for interface in config:
                    for endpoint in interface:
                        if int(endpoint.bEndpointAddress) == self.INTERRUPT_ENDPOINT:
                            size = int(endpoint.wMaxPacketSize) & 0x07FF
                            if size > 0:
                                return size
        except (usb.core.USBError, AttributeError, TypeError, ValueError):
            pass
        # The ADA305 descriptor says 2; this fallback keeps reads safe if the
        # descriptors cannot be walked for some reason.
        return 2

    # -- transfers -------------------------------------------------------

    def _translate(self, exc: usb.core.USBError, operation: str) -> TransportError:
        if getattr(exc, "errno", None) == errno.ETIMEDOUT or "timed out" in str(exc):
            return TransportTimeout(f"{operation} timed out: {exc}")
        if getattr(exc, "errno", None) == errno.ENOENT:
            return TransportError(
                f"{operation} was rejected before reaching the device "
                f"({exc}). This usually means the wIndex/interface check "
                "failed; the library uses wIndex=0 to avoid it."
            )
        if getattr(exc, "errno", None) == errno.EOVERFLOW:
            return TransportOverflow(
                f"{operation} overflowed ({exc}); the device sent more data "
                "than the buffer. Use a read length that is a multiple of the "
                "endpoint's max packet size."
            )
        return TransportError(f"{operation} failed: {exc}")

    def read_control(self, request: int, value: int, length: int) -> bytes:
        try:
            data = self._device.ctrl_transfer(
                BM_CLASS_IFACE_IN,
                request,
                value,
                WINDEX,
                length,
                timeout=self.timeout_ms,
            )
        except usb.core.USBError as exc:
            raise self._translate(exc, "control read") from exc
        return bytes(data)

    def write_control(self, request: int, value: int, data: bytes) -> int:
        try:
            count = self._device.ctrl_transfer(
                BM_CLASS_IFACE_OUT,
                request,
                value,
                WINDEX,
                bytes(data),
                timeout=self.timeout_ms,
            )
        except usb.core.USBError as exc:
            raise self._translate(exc, "control write") from exc
        return int(count)

    def _claim(self) -> None:
        if self._claimed:
            return
        try:
            usb.util.claim_interface(self._device, self.INTERFACE)
        except usb.core.USBError as exc:
            raise TransportError(
                "cannot claim interface 0 for interrupt monitoring: "
                f"{exc}. Another driver may own the device; no detach is "
                "attempted automatically."
            ) from exc
        self._claimed = True

    def read_interrupt(self, length: int, timeout_ms: int) -> bytes:
        length = int(length)
        if length <= 0:
            raise TransportError("interrupt read length must be positive")

        self._claim()
        # On Linux, usbfs aborts an interrupt-IN URB with EOVERFLOW if the
        # device returns a full final packet that would overrun the buffer.
        # A request of 7 bytes on the 2-byte ADA305 endpoint is exactly that
        # trap: the fourth packet would make 8 bytes.  Allocate a whole number
        # of packets and trim afterwards so a button press cannot crash us.
        packet = self.interrupt_packet_size
        buffer_length = length
        if packet > 0 and length % packet:
            buffer_length = ((length // packet) + 1) * packet
        try:
            data = self._device.read(
                self.INTERRUPT_ENDPOINT, buffer_length, timeout=timeout_ms
            )
        except usb.core.USBError as exc:
            raise self._translate(exc, "interrupt read") from exc
        return bytes(data)[:length]

    def close(self) -> None:
        if self._claimed:
            try:
                usb.util.release_interface(self._device, self.INTERFACE)
            except usb.core.USBError:
                pass
            self._claimed = False

    def __enter__(self) -> "PyUsbTransport":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


__all__ = [
    "DeviceInfo",
    "PyUsbTransport",
    "Transport",
    "TransportError",
    "TransportOverflow",
    "TransportTimeout",
    "enumerate_devices",
]
