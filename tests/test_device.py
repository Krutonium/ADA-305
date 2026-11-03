"""Device-layer tests using the fake transport only."""

from __future__ import annotations

import pytest

from ada305 import protocol as p
from ada305.device import DeviceError, WriteNotConfirmed
from ada305.safety import LouderNotAllowed, MirroredChannelError, RangeError


def test_read_status_is_read_only(device, transport):
    status = device.read_status()
    assert transport.writes == []
    assert status.mute is False
    assert status.mode == 0
    assert status.volume_db("master") == 56
    assert status.volume_db("surround-left") == 31
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
    from ada305.device import Ada305

    transport.registers[p.REG_VOLUME_BASE + 1] = b"\x00\xff"
    with pytest.raises(DeviceError):
        Ada305(transport).read_status()


def test_read_status_rejects_malformed_volume(transport):
    from ada305.device import Ada305

    transport.registers[p.REG_VOLUME_BASE + 1] = b"\x01\x00"
    with pytest.raises(DeviceError):
        Ada305(transport).read_status()


def test_mute_write_requires_confirmation(device, transport):
    with pytest.raises(WriteNotConfirmed):
        device.write_mute(True)
    assert transport.writes == []


def test_mute_write_encodes_and_verifies(device, transport):
    assert device.write_mute(True, confirm=True) is True
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_MUTE, b"\x01")
    ]


def test_mute_noop_write_is_skipped(device, transport):
    assert device.write_mute(False, confirm=True) is False
    assert transport.writes == []


def test_volume_noop_does_not_write(device, transport):
    plan = device.prepare_volume("master", 56)
    assert plan.is_noop
    assert device.execute_volume(plan, confirm=True) == 56
    assert transport.writes == []


def test_volume_write_requires_confirmation(device, transport):
    plan = device.prepare_volume("master", 60)
    with pytest.raises(WriteNotConfirmed):
        device.execute_volume(plan)
    assert transport.writes == []


def test_quieter_volume_write_encoding(device, transport):
    plan = device.prepare_volume("master", 60)
    assert plan.encoded == b"\x00\x3c"
    assert device.execute_volume(plan, confirm=True) == 60
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x3c")
    ]


def test_louder_volume_refused_without_flag(device, transport):
    with pytest.raises(LouderNotAllowed):
        device.prepare_volume("master", 40)
    assert transport.writes == []


def test_louder_volume_allowed_with_flag(device, transport):
    plan = device.prepare_volume("master", 40, allow_louder=True)
    assert plan.is_louder
    assert device.execute_volume(plan, confirm=True) == 40
    assert transport.writes[-1][2] == b"\x00\x28"


def test_maximum_volume_refused_even_with_every_flag(device, transport):
    with pytest.raises(RangeError):
        device.prepare_volume(
            "master", 25, allow_louder=True, allow_loud=True
        )
    assert transport.writes == []


def test_mirrored_channel_refused(device, transport):
    with pytest.raises(MirroredChannelError):
        device.prepare_volume("surround-right", 10, allow_louder=True)


def test_write_refused_if_value_changed_while_planning(device, transport):
    plan = device.prepare_volume("master", 60)
    transport.registers[p.REG_VOLUME_BASE] = b"\x00\x50"
    with pytest.raises(Exception) as excinfo:
        device.execute_volume(plan, confirm=True)
    assert "changed" in str(excinfo.value)
    assert transport.writes == []


def test_read_back_mismatch_is_an_error(device, transport):
    plan = device.prepare_volume("master", 60)

    original_write = transport.write_control

    def lying_write(request, value, data):
        original_write(request, value, data)
        transport.registers[value] = b"\x00\x40"
        return len(data)

    transport.write_control = lying_write
    with pytest.raises(DeviceError):
        device.execute_volume(plan, confirm=True)
