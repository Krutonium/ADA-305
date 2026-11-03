"""Loudness-policy tests.  These never touch a device."""

from __future__ import annotations

import pytest

from ada305 import safety
from ada305.protocol import HW_MASTER_MAX_DB, HW_MASTER_MIN_DB


def plan(requested, current=56, channel="master", **kwargs):
    return safety.plan_volume(channel, requested, current, **kwargs)


def test_master_hardware_maximum_is_never_writable():
    # 25 dB attenuation is the documented maximum volume.
    assert HW_MASTER_MIN_DB == 25
    with pytest.raises(safety.RangeError):
        plan(25, current=56, allow_louder=True, allow_loud=True)
    # The very next step is allowed only with both flags.
    assert plan(26, current=56, allow_louder=True, allow_loud=True).requested_db == 26


def test_master_loud_zone_requires_allow_loud():
    with pytest.raises(safety.LoudNotAllowed):
        plan(30, current=56, allow_louder=True)
    assert plan(30, current=56, allow_louder=True, allow_loud=True).requested_db == 30


def test_master_loud_threshold_boundary():
    assert plan(36, current=56, allow_louder=True).requested_db == 36
    with pytest.raises(safety.LoudNotAllowed):
        plan(35, current=56, allow_louder=True)


def test_master_quieter_is_allowed_without_extra_flags():
    result = plan(96, current=56)
    assert result.is_quieter is True
    assert result.delta_db == 40


def test_louder_requires_allow_louder():
    with pytest.raises(safety.LouderNotAllowed):
        plan(40, current=56)
    result = plan(40, current=56, allow_louder=True)
    assert result.is_louder is True
    assert result.delta_db == -16


def test_channel_range_is_not_clamped():
    with pytest.raises(safety.RangeError):
        plan(32, current=0, channel="left")
    with pytest.raises(safety.RangeError):
        plan(-1, current=0, channel="left")


def test_current_value_outside_range_is_refused():
    with pytest.raises(safety.RangeError):
        plan(50, current=200, channel="left")


def test_master_range_is_documented_range():
    with pytest.raises(safety.RangeError):
        plan(HW_MASTER_MAX_DB + 1, current=56)
    with pytest.raises(safety.RangeError):
        plan(HW_MASTER_MIN_DB - 1, current=56)


def test_surround_right_is_refused_as_independently_writable():
    with pytest.raises(safety.MirroredChannelError):
        plan(10, current=10, channel="surround-right")


def test_unknown_channel():
    with pytest.raises(safety.UnknownChannelError):
        plan(40, current=56, channel="rear")


def test_non_int_values_are_refused():
    with pytest.raises(safety.RangeError):
        plan("40", current=56)
    with pytest.raises(safety.RangeError):
        plan(40, current="56")
    with pytest.raises(safety.RangeError):
        plan(True, current=56)


def test_noop_plan():
    result = plan(56, current=56)
    assert result.is_noop is True
    assert result.describe().startswith("no change")
