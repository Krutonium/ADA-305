"""Volume-level policy tests.  These never touch a device."""

from __future__ import annotations

import pytest

from ada305 import safety
from ada305.protocol import resolve_channel


MASTER = resolve_channel("master")
LEFT = resolve_channel("left")


def plan(level, current=56, channel="master", **kwargs):
    return safety.plan_volume(channel, level, current, **kwargs)


def test_master_level_endpoints():
    assert safety.level_to_db(MASTER, 0) == 96
    assert safety.level_to_db(MASTER, 100) == 25
    assert safety.level_to_db(MASTER, 50) == 60
    assert safety.level_to_db(MASTER, 56) == 56
    assert safety.level_to_db(MASTER, 90) == 32
    assert safety.level_to_db(MASTER, 91) == 31


def test_channel_level_endpoints():
    assert safety.level_to_db(LEFT, 0) == 31
    assert safety.level_to_db(LEFT, 100) == 0
    assert safety.level_to_db(LEFT, 50) == 15
    assert safety.level_to_db(LEFT, 90) == 3


def test_level_is_monotonic_in_both_directions():
    for channel in (MASTER, LEFT):
        values = [safety.level_to_db(channel, n) for n in range(101)]
        assert values == sorted(values, reverse=True)
        assert set(values) <= set(range(channel.min_db, channel.max_db + 1))


def test_db_to_level_endpoints():
    assert safety.db_to_level(MASTER, 96) == 0
    assert safety.db_to_level(MASTER, 25) == 100
    assert safety.db_to_level(MASTER, 56) == 56
    assert safety.db_to_level(MASTER, 60) == 50
    assert safety.db_to_level(LEFT, 31) == 0
    assert safety.db_to_level(LEFT, 0) == 100


@pytest.mark.parametrize("bad", [-1, 101, 1000])
def test_level_out_of_range_is_refused(bad):
    with pytest.raises(safety.RangeError):
        safety.level_to_db(MASTER, bad)
    with pytest.raises(safety.RangeError):
        plan(bad)


@pytest.mark.parametrize("bad", [True, False, 50.0, "50", None])
def test_level_must_be_an_int(bad):
    with pytest.raises(safety.RangeError):
        plan(bad)


def test_level_90_is_allowed_without_opt_in():
    result = plan(90)
    assert result.requested_level == 90
    assert result.requested_db == 32
    assert result.is_very_loud is False


def test_level_91_requires_very_loud_opt_in():
    with pytest.raises(safety.VeryLoudNotAllowed):
        plan(91)
    result = plan(91, allow_very_loud=True)
    assert result.requested_db == 31
    assert result.is_very_loud is True


def test_level_100_requires_very_loud_opt_in():
    with pytest.raises(safety.VeryLoudNotAllowed):
        plan(100)
    result = plan(100, allow_very_loud=True)
    assert result.requested_db == 25
    assert result.is_very_loud is True


def test_very_loud_noop_is_allowed_without_opt_in():
    result = plan(100, current=25)
    assert result.is_noop is True
    assert result.is_very_loud is True


def test_very_loud_guard_applies_to_channels_too():
    with pytest.raises(safety.VeryLoudNotAllowed):
        plan(91, current=15, channel="left")
    result = plan(91, current=15, channel="left", allow_very_loud=True)
    assert result.requested_db == 3


def test_quieter_and_louder_directions():
    quieter = plan(50)
    assert quieter.is_quieter is True
    assert quieter.is_louder is False
    assert quieter.delta_db == 4  # 56 -> 60 dB attenuation

    louder = plan(60)
    assert louder.is_louder is True
    assert louder.is_quieter is False
    assert louder.delta_db == -3  # 56 -> 53 dB attenuation


def test_current_value_outside_range_is_refused():
    with pytest.raises(safety.RangeError):
        plan(50, current=200, channel="left")


def test_surround_right_is_refused_as_independently_writable():
    with pytest.raises(safety.MirroredChannelError):
        plan(50, current=15, channel="surround-right")


def test_unknown_channel():
    with pytest.raises(safety.UnknownChannelError):
        plan(50, channel="rear")


def test_noop_plan():
    result = plan(56)
    assert result.is_noop is True
    assert result.describe().startswith("no change")


def test_describe_limits():
    text = safety.describe_limits(MASTER)
    assert "0..100" in text
    assert "minimum" in text
    assert "maximum" in text
