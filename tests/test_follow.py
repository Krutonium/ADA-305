"""Follower policy tests.  All device access uses FakeTransport."""

from __future__ import annotations

import pytest

from ada305 import protocol as p
from ada305.follow import Follower, FollowerSettings


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def report(flags: int = 0, delta: int = 0) -> p.InterruptReport:
    return p.decode_interrupt_report(bytes((flags, delta & 0xFF)))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def logs():
    return []


@pytest.fixture
def warnings():
    return []


@pytest.fixture
def follower(device, clock, logs, warnings) -> Follower:
    return Follower(
        device,
        FollowerSettings(allow_increase=False, allow_very_loud=True),
        clock=clock,
        log=logs.append,
        warn=warnings.append,
    )


def make_follower(device, clock, logs, warnings, **settings) -> Follower:
    return Follower(
        device,
        FollowerSettings(**settings),
        clock=clock,
        log=logs.append,
        warn=warnings.append,
    )


# -- wheel ----------------------------------------------------------------


def test_master_quieter_click_writes_one_db(device, transport, follower):
    follower.handle_report(report(delta=-1))
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x39")
    ]
    assert follower.active_key == "master"


def test_louder_click_is_ignored_without_opt_in(
    device, transport, follower, warnings, logs
):
    follower.handle_report(report(delta=1))
    assert transport.writes == []
    assert any("allow-increase" in message for message in warnings)
    assert logs == []


def test_louder_click_applies_with_opt_in(
    device, transport, clock, logs, warnings
):
    follower = make_follower(
        device, clock, logs, warnings, allow_increase=True
    )
    follower.handle_report(report(delta=1))
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x37")
    ]


def test_very_loud_target_needs_its_own_opt_in(
    device, transport, clock, logs, warnings
):
    transport.registers[p.REG_VOLUME_BASE] = b"\x00\x20"  # level 90
    follower = make_follower(
        device, clock, logs, warnings, allow_increase=True
    )
    follower.handle_report(report(delta=1))
    assert transport.writes == []
    assert any("--yes-very-loud" in message for message in warnings)

    follower = make_follower(
        device,
        clock,
        logs,
        warnings,
        allow_increase=True,
        allow_very_loud=True,
    )
    follower.handle_report(report(delta=1))
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x1f")
    ]


def test_click_past_hardware_limit_is_ignored(
    device, transport, clock, logs, warnings
):
    follower = make_follower(
        device, clock, logs, warnings, allow_increase=True
    )
    # Center is at 0 dB: already the loudest documented value.
    follower.handle_report(report(p.FLAG_SELECT_CENTER))
    followed = transport.writes[:]
    follower.handle_report(report(delta=1))
    assert transport.writes == followed
    assert any("maximum" in message for message in warnings)


def test_dry_run_never_writes(device, transport, clock, logs, warnings):
    follower = make_follower(
        device,
        clock,
        logs,
        warnings,
        allow_increase=True,
        dry_run=True,
    )
    follower.handle_report(report(delta=-1))
    follower.handle_report(report(delta=1))
    assert transport.writes == []
    assert any("[dry-run]" in message for message in logs)


def test_repeated_wheel_packets_from_one_detent_are_coalesced(
    device, transport, clock, logs, warnings
):
    follower = make_follower(device, clock, logs, warnings)
    for _ in range(3):
        follower.handle_report(report(delta=-1))
        clock.advance(0.1)
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x39")
    ]

    # A genuine click after the window still applies.
    clock.advance(0.2)
    follower.handle_report(report(delta=-1))
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x39"),
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x3a"),
    ]


def test_opposite_packet_in_a_wheel_burst_is_ignored(
    device, transport, clock, logs, warnings
):
    follower = make_follower(
        device, clock, logs, warnings, allow_increase=True
    )
    follower.handle_report(report(delta=-1))
    clock.advance(0.1)
    follower.handle_report(report(delta=1))
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x39")
    ]


# -- channel selectors ----------------------------------------------------


def test_selector_routes_wheel_to_selected_channel(
    device, transport, follower
):
    follower.handle_report(report(p.FLAG_SELECT_SUBWOOFER))
    assert follower.active_key == "sub"
    follower.handle_report(report(delta=-1))
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE + 4, b"\x00\x01")
    ]


def test_selector_switches_between_channels(device, transport, follower):
    follower.handle_report(report(p.FLAG_SELECT_SUBWOOFER))
    follower.handle_report(report(p.FLAG_SELECT_CENTER))
    assert follower.active_key == "center"


def test_repeated_selector_packet_is_debounced(
    device, clock, logs, warnings
):
    follower = make_follower(device, clock, logs, warnings)
    follower.handle_report(report(p.FLAG_SELECT_SUBWOOFER))
    clock.advance(0.1)
    follower.handle_report(report(p.FLAG_SELECT_SUBWOOFER))
    assert follower.active_key == "sub"

    clock.advance(0.5)
    follower.handle_report(report(p.FLAG_SELECT_SUBWOOFER))
    assert follower.active_key == "master"


def test_channel_selection_times_out(device, clock, logs, warnings):
    follower = make_follower(device, clock, logs, warnings)
    follower.handle_report(report(p.FLAG_SELECT_CENTER))
    assert follower.active_key == "center"

    clock.advance(2.9)
    follower.tick()
    assert follower.active_key == "center"

    clock.advance(0.2)
    follower.tick()
    assert follower.active_key == "master"
    assert any("timed out" in message for message in logs)


def test_volume_change_resets_channel_timeout(device, clock, logs, warnings):
    follower = make_follower(
        device, clock, logs, warnings, allow_very_loud=True
    )
    follower.handle_report(report(p.FLAG_SELECT_CENTER))
    clock.advance(2.0)
    follower.handle_report(report(delta=-1))
    clock.advance(2.0)
    follower.tick()
    assert follower.active_key == "center"


def test_read_timeout_is_shortened_near_deadline(device, clock, logs, warnings):
    follower = make_follower(device, clock, logs, warnings)
    follower.handle_report(report(p.FLAG_SELECT_CENTER))
    clock.advance(2.5)
    assert follower.read_timeout_ms(2000) == 510
    clock.advance(1.0)
    assert follower.read_timeout_ms(2000) == 1


# -- mute and reconnect ---------------------------------------------------


def test_repeated_mute_packets_toggle_once(
    device, transport, clock, logs, warnings
):
    follower = make_follower(device, clock, logs, warnings)
    follower.handle_report(report(p.FLAG_MUTE_TOGGLE))
    clock.advance(0.1)
    follower.handle_report(report(p.FLAG_MUTE_TOGGLE))
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_MUTE, b"\x01")
    ]

    clock.advance(0.5)
    follower.handle_report(report(p.FLAG_MUTE_TOGGLE))
    assert transport.registers[p.REG_MUTE] == b"\x00"
    assert any("unmuting" in message for message in warnings)


def test_reconnect_resets_to_master(device, follower):
    follower.handle_report(report(p.FLAG_SELECT_SUBWOOFER))
    assert follower.active_key == "sub"
    follower.handle_report(report(p.FLAG_RECONNECT))
    assert follower.active_key == "master"


def test_unknown_multiple_selectors_are_ignored(
    device, transport, follower, warnings
):
    follower.handle_report(
        report(p.FLAG_SELECT_SUBWOOFER | p.FLAG_SELECT_CENTER)
    )
    assert follower.active_key == "master"
    assert transport.writes == []
    assert any("more than one selector" in message for message in warnings)


def test_idle_and_mode_reports_do_not_write(device, transport, follower):
    follower.handle_report(report(0, 0))
    follower.handle_report(report(p.FLAG_MODE_CYCLE))
    assert transport.writes == []
