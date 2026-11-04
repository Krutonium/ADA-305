"""Device-layer tests using the fake transport only."""

from __future__ import annotations

import pytest

from ada305 import protocol as p
from ada305.device import Ada305, DeviceError
from ada305.safety import MirroredChannelError, VeryLoudNotAllowed


def bracketed(register, data):
    return [
        (p.REQ_REGISTER_WRITE, p.REG_CONTROL, b"\x02"),
        (p.REQ_REGISTER_WRITE, register, data),
        (p.REQ_REGISTER_WRITE, p.REG_CONTROL, b"\x03"),
    ]


def test_read_status_is_read_only(device, transport):
    status = device.read_status()
    assert transport.writes == []
    assert status.mute is False
    assert status.mode == 0
    assert status.volume_db("master") == 56
    assert status.volume_level("master") == 56
    assert status.volume_level("surround-left") == 0
    # surround-right mirrors surround-left and never reads 0x206.
    assert status.channels["surround-right"].mirrored is True
    assert status.channels["surround-right"].db == 31
    assert all(value != 0x206 for _req, value, _len in transport.reads)


def test_read_channel_mirror_does_not_read_0x206(device, transport):
    reading = device.read_channel("surround-right")
    assert reading.db == 31
    assert reading.source == "surround-left"
    assert all(value != 0x206 for _req, value, _len in transport.reads)


def test_read_status_rejects_out_of_range_value(transport):
    transport.registers[p.REG_VOLUME_BASE + 1] = b"\x00\xff"
    with pytest.raises(DeviceError):
        Ada305(transport).read_status()


def test_read_status_rejects_malformed_volume(transport):
    transport.registers[p.REG_VOLUME_BASE + 1] = b"\x01\x00"
    with pytest.raises(DeviceError):
        Ada305(transport).read_status()


def test_mute_write_encodes_and_verifies(device, transport):
    assert device.write_mute(True) is True
    assert transport.writes == [(p.REQ_REGISTER_WRITE, p.REG_MUTE, b"\x01")]


def test_mute_noop_write_is_skipped(device, transport):
    assert device.write_mute(False) is False
    assert transport.writes == []


def test_volume_noop_does_not_write(device, transport):
    plan = device.prepare_volume("master", 56)
    assert plan.is_noop
    result = device.execute_volume(plan)
    assert result.changed is False
    assert result.level == 56
    assert result.db == 56
    assert transport.writes == []


def test_quieter_volume_write_encoding(device, transport):
    plan = device.prepare_volume("master", 50)
    assert plan.requested_db == 60
    assert plan.encoded == b"\x00\x3c"
    result = device.execute_volume(plan)
    assert result.level == 50
    assert result.db == 60
    assert result.changed is True
    assert transport.writes == bracketed(p.REG_VOLUME_BASE, b"\x00\x3c")


def test_louder_volume_is_allowed_by_default(device, transport):
    plan = device.prepare_volume("master", 60)
    assert plan.is_louder
    assert plan.encoded == b"\x00\x35"
    result = device.execute_volume(plan)
    assert result.level == 60
    assert result.db == 53
    assert transport.writes[-2][2] == b"\x00\x35"
    assert transport.writes[-1] == (
        p.REQ_REGISTER_WRITE,
        p.REG_CONTROL,
        b"\x03",
    )


def test_level_90_is_allowed_without_opt_in(device, transport):
    plan = device.prepare_volume("master", 90)
    assert plan.requested_db == 32
    assert device.execute_volume(plan).db == 32


def test_level_91_requires_opt_in(device, transport):
    with pytest.raises(VeryLoudNotAllowed):
        device.prepare_volume("master", 91)
    assert transport.writes == []


def test_level_91_with_opt_in_writes(device, transport):
    plan = device.prepare_volume("master", 91, allow_very_loud=True)
    result = device.execute_volume(plan)
    assert result.level == 91
    assert result.db == 31
    assert transport.writes == bracketed(p.REG_VOLUME_BASE, b"\x00\x1f")


def test_level_100_with_opt_in_writes_maximum(device, transport):
    plan = device.prepare_volume("master", 100, allow_very_loud=True)
    result = device.execute_volume(plan)
    assert result.level == 100
    assert result.db == 25
    assert transport.writes == bracketed(p.REG_VOLUME_BASE, b"\x00\x19")


def test_mirrored_channel_refused(device, transport):
    with pytest.raises(MirroredChannelError):
        device.prepare_volume("surround-right", 50)
    assert transport.writes == []


def test_write_refused_if_value_changed_while_planning(device, transport):
    plan = device.prepare_volume("master", 50)
    transport.registers[p.REG_VOLUME_BASE] = b"\x00\x50"
    with pytest.raises(Exception) as excinfo:
        device.execute_volume(plan)
    assert "changed" in str(excinfo.value)
    assert transport.writes == []


def test_read_back_mismatch_is_an_error(device, transport):
    plan = device.prepare_volume("master", 50)

    original_write = transport.write_control

    def lying_write(request, value, data):
        original_write(request, value, data)
        transport.registers[value] = b"\x00\x40"
        return len(data)

    transport.write_control = lying_write
    with pytest.raises(DeviceError):
        device.execute_volume(plan)


def test_return_control_writes_local_value(device, transport):
    device.return_control()
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_CONTROL, b"\x03")
    ]


def test_volume_failure_still_returns_control(device, transport):
    plan = device.prepare_volume("master", 50)

    def failing_write(request, value, data):
        if value == p.REG_VOLUME_BASE:
            raise AssertionError("simulated volume write failure")
        transport.writes.append((request, value, bytes(data)))
        return len(data)

    transport.write_control = failing_write
    with pytest.raises(AssertionError):
        device.execute_volume(plan)
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_CONTROL, b"\x02"),
        (p.REQ_REGISTER_WRITE, p.REG_CONTROL, b"\x03"),
    ]


def test_read_interrupt_defaults_to_packet_size(device, transport):
    transport.interrupt_reports = [b"\x00\x3c"]
    report = device.read_interrupt(timeout_ms=123)
    assert report.volume_delta == 60
    assert transport.reads == [("interrupt", 2, 123)]
