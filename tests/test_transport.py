"""Transport tests without opening a real USB device."""

from __future__ import annotations

import errno

import pytest
import usb.core

from ada305.transport import PyUsbTransport, TransportOverflow


class FakeEndpoint:
    bEndpointAddress = 0x81
    wMaxPacketSize = 2


class FakeInterface:
    def __iter__(self):
        return iter((FakeEndpoint(),))


class FakeConfig:
    def __iter__(self):
        return iter((FakeInterface(),))


class FakeUsbDevice:
    bus = 1
    address = 11
    product = "fake ADA305"

    def __init__(self, data=b"\x00\x3c", packet_size=2):
        self.data = bytes(data)
        self.packet_size = packet_size
        self.reads = []
        self.fail = None

    def __iter__(self):
        return iter((FakeConfig(),))

    def read(self, endpoint, size, timeout):
        self.reads.append((endpoint, size, timeout))
        if self.fail is not None:
            raise self.fail
        return self.data[:size]


def transport_for(device):
    transport = PyUsbTransport(device, timeout_ms=100)
    # Skip the real libusb claim; this fake has no interface to claim.
    transport._claimed = True
    return transport


def test_discovers_interrupt_packet_size():
    transport = transport_for(FakeUsbDevice())
    assert transport.interrupt_packet_size == 2


def test_read_interrupt_rounds_buffer_to_whole_packets():
    device = FakeUsbDevice(data=b"\x00\x3c\xaa\xbb\xcc\xdd\xee\xff")
    transport = transport_for(device)

    data = transport.read_interrupt(7, 123)

    # 7 rounds up to 8 bytes so usbfs cannot report EOVERFLOW on the final
    # 2-byte packet; the caller still receives only the 7 bytes it asked for.
    assert device.reads == [(0x81, 8, 123)]
    assert data == b"\x00\x3c\xaa\xbb\xcc\xdd\xee"


def test_read_interrupt_exact_packet_size_is_not_rounded():
    device = FakeUsbDevice()
    transport = transport_for(device)

    assert transport.read_interrupt(2, 50) == b"\x00\x3c"
    assert device.reads == [(0x81, 2, 50)]


def test_read_interrupt_rejects_zero_length():
    transport = transport_for(FakeUsbDevice())
    with pytest.raises(Exception):
        transport.read_interrupt(0, 50)


def test_overflow_is_translated_without_crashing():
    device = FakeUsbDevice()
    device.fail = usb.core.USBError("overflow", errno=errno.EOVERFLOW)
    transport = transport_for(device)

    with pytest.raises(TransportOverflow):
        transport.read_interrupt(2, 50)
