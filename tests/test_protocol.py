"""Codec and channel-map tests.  No device access at all."""

from __future__ import annotations

import pytest

from ada305 import protocol as p


def test_volume_round_trip():
    for db in (0, 1, 25, 56, 31, 96, 255):
        assert p.encode_volume(db) == bytes((0x00, db))
        assert p.decode_volume(p.encode_volume(db)) == db


def test_volume_wire_format_is_attenuation_not_q8():
    # Live master read was 00 38 = 56 dB attenuation, not 0x0038/256.
    assert p.encode_volume(56) == b"\x00\x38"
    assert p.decode_volume(b"\x00\x38") == 56


@pytest.mark.parametrize("bad", [-1, 256, 1000])
def test_encode_volume_refuses_out_of_byte(bad):
    with pytest.raises(p.ProtocolError):
        p.encode_volume(bad)


@pytest.mark.parametrize("bad", [True, False, 1.5, "56", None])
def test_encode_volume_refuses_non_int(bad):
    with pytest.raises(TypeError):
        p.encode_volume(bad)


def test_decode_volume_rejects_size_and_high_byte():
    with pytest.raises(p.ProtocolError):
        p.decode_volume(b"\x38")
    with pytest.raises(p.ProtocolError):
        p.decode_volume(b"\x01\x38")


def test_mute_codec():
    assert p.encode_mute(True) == b"\x01"
    assert p.encode_mute(False) == b"\x00"
    assert p.decode_mute(b"\x00") is False
    assert p.decode_mute(b"\x01") is True
    for raw in (b"\x02", b"\xff"):
        with pytest.raises(p.ProtocolError):
            p.decode_mute(raw)
    with pytest.raises(TypeError):
        p.encode_mute(1)


def test_decode_mode():
    assert p.decode_mode(0, 0) == 0
    assert p.decode_mode(1, 0) == 0
    assert p.decode_mode(1, 1) == 1
    assert p.decode_mode(3, 0) == 3
    assert p.decode_mode(2, 1) == 2


def test_channel_registers():
    regs = {channel.key: channel.register for channel in p.CHANNELS}
    assert regs == {
        "master": 0x200,
        "left": 0x201,
        "right": 0x202,
        "center": 0x203,
        "sub": 0x204,
        "surround-left": 0x205,
        "surround-right": 0x206,
    }


def test_surround_right_is_a_mirror_on_ada305():
    channel = p.resolve_channel("surround-right")
    assert channel.writable is False
    assert channel.mirror_of == "surround-left"


@pytest.mark.parametrize(
    "name,key",
    [
        ("master", "master"),
        ("MAIN", "master"),
        ("m", "master"),
        ("front_left", "left"),
        ("FL", "left"),
        ("right", "right"),
        ("r", "right"),
        ("center", "center"),
        ("subwoofer", "sub"),
        ("LFE", "sub"),
        ("surround_left", "surround-left"),
        ("Sur Left", "surround-left"),
        ("surround-right", "surround-right"),
    ],
)
def test_resolve_channel_aliases(name, key):
    assert p.resolve_channel(name).key == key


def test_resolve_channel_unknown():
    with pytest.raises(KeyError):
        p.resolve_channel("rear")


def test_interrupt_report_decode():
    report = p.decode_interrupt_report(bytes((0x01, 0x05, 0, 0, 0, 0, 0)))
    assert report.flags == 0x01
    assert report.mute_toggle is True
    assert report.volume_delta == 5
    assert report.raw == bytes((0x01, 0x05, 0, 0, 0, 0, 0))
    assert report.extra == bytes((0, 0, 0, 0, 0))


def test_interrupt_report_signed_delta():
    # 0xFF = -1: attenuation drops by one, i.e. one step louder.
    report = p.decode_interrupt_report(b"\x00\xff")
    assert report.volume_delta == -1
    assert report.flags == 0


def test_interrupt_report_too_short():
    with pytest.raises(p.ProtocolError):
        p.decode_interrupt_report(b"\x00")
