"""Shared test fixtures.

Every test uses :class:`FakeTransport`; no test can reach real hardware.
Volume-changing operations are recorded, never performed.
"""

from __future__ import annotations

import pytest

from ada305 import protocol as p
from ada305.device import Ada305


class FakeTransport:
    """In-memory stand-in for :class:`ada305.transport.PyUsbTransport`."""

    description = "fake ADA305 (04d2:0305)"
    interrupt_packet_size = 2

    def __init__(self, registers=None, state_byte: int = 1) -> None:
        self.registers = {
            p.REG_MUTE: b"\x00",
            p.REG_VOLUME_BASE + 0: b"\x00\x38",  # master 56 dB
            p.REG_VOLUME_BASE + 1: b"\x00\x00",
            p.REG_VOLUME_BASE + 2: b"\x00\x00",
            p.REG_VOLUME_BASE + 3: b"\x00\x00",
            p.REG_VOLUME_BASE + 4: b"\x00\x00",
            p.REG_VOLUME_BASE + 5: b"\x00\x1f",  # surround-left 31 dB
            p.REG_VOLUME_BASE + 6: b"\x00\x00",
            p.REG_MODE_STATE: b"\x00",
        }
        if registers:
            self.registers.update(registers)
        self.state_byte = state_byte
        self.reads = []
        self.writes = []
        self.interrupt_reports = []
        self.closed = False

    # -- Transport protocol ------------------------------------------------

    def read_control(self, request: int, value: int, length: int) -> bytes:
        self.reads.append((request, value, length))
        if request == p.REQ_STATE_READ:
            return bytes((self.state_byte,))
        if request == p.REQ_REGISTER_READ:
            raw = self.registers.get(value)
            if raw is None:
                raise AssertionError(f"unexpected register read 0x{value:04X}")
            return raw[:length]
        raise AssertionError(f"unexpected read request {request}")

    def write_control(self, request: int, value: int, data: bytes) -> int:
        data = bytes(data)
        self.writes.append((request, value, data))
        if request == p.REQ_REGISTER_WRITE:
            self.registers[value] = data
        return len(data)

    def read_interrupt(self, length: int, timeout_ms: int) -> bytes:
        self.reads.append(("interrupt", length, timeout_ms))
        if not self.interrupt_reports:
            raise AssertionError("no interrupt report configured")
        item = self.interrupt_reports.pop(0)
        if isinstance(item, Exception):
            raise item
        return bytes(item)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def device(transport: FakeTransport) -> Ada305:
    return Ada305(transport)
