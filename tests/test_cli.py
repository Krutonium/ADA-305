"""CLI tests.  The USB opener is replaced with FakeTransport; no real writes."""

from __future__ import annotations

import json

from ada305 import cli
from ada305 import protocol as p


def run(monkeypatch, transport, argv):
    monkeypatch.setattr(
        cli.PyUsbTransport,
        "open",
        staticmethod(lambda **_kwargs: transport),
    )
    return cli.main(argv)


def direct(register, data):
    """One direct register write; the reference ADA305 needs no 0x0500 bracket."""

    return [(p.REQ_REGISTER_WRITE, register, data)]


def test_default_command_is_read_only_status(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, []) == 0
    assert transport.writes == []
    out = capsys.readouterr().out
    assert "Master:" in out
    assert "56/100" in out
    assert "56 dB" in out


def test_status_json(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mute"] is False
    assert payload["mode"] == 0
    assert payload["channels"]["master"]["level"] == 56
    assert payload["channels"]["master"]["db"] == 56
    assert payload["channels"]["master"]["very_loud"] is False
    assert payload["channels"]["surround-right"]["mirrored"] is True
    assert transport.writes == []


def test_raw_read_is_read_only(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["raw-read", "0x200", "--length", "2"]) == 0
    assert "00 38" in capsys.readouterr().out
    assert transport.writes == []


def test_volume_increase_writes_without_extra_flag(monkeypatch, transport):
    # 56 -> 60 is louder (60 dB -> 53 dB attenuation); no --yes is needed.
    assert run(monkeypatch, transport, ["volume", "master", "60"]) == 0
    assert transport.writes == direct(p.REG_VOLUME_BASE, b"\x00\x35")


def test_volume_increase_prints_warning(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "master", "60"]) == 0
    assert "INCREASE" in capsys.readouterr().err


def test_level_90_is_allowed_without_opt_in(monkeypatch, transport):
    assert run(monkeypatch, transport, ["volume", "master", "90"]) == 0
    assert transport.writes == direct(p.REG_VOLUME_BASE, b"\x00\x20")


def test_level_91_is_refused_without_opt_in(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "master", "91"]) == 3
    assert transport.writes == []
    err = capsys.readouterr().err
    assert "refusing" in err
    assert "--yes-very-loud" in err


def test_level_91_with_opt_in_writes(monkeypatch, transport):
    assert (
        run(monkeypatch, transport, ["volume", "master", "91", "--yes-very-loud"])
        == 0
    )
    assert transport.writes == direct(p.REG_VOLUME_BASE, b"\x00\x1f")


def test_level_100_with_opt_in_writes_maximum(monkeypatch, transport):
    assert (
        run(monkeypatch, transport, ["volume", "master", "100", "--yes-very-loud"])
        == 0
    )
    assert transport.writes == direct(p.REG_VOLUME_BASE, b"\x00\x19")


def test_level_100_without_opt_in_is_refused(monkeypatch, transport):
    assert run(monkeypatch, transport, ["volume", "master", "100"]) == 3
    assert transport.writes == []


def test_option_before_subcommand_is_honoured(monkeypatch, transport):
    assert (
        run(
            monkeypatch,
            transport,
            ["--yes-very-loud", "volume", "master", "91"],
        )
        == 0
    )
    assert transport.writes == direct(p.REG_VOLUME_BASE, b"\x00\x1f")


def test_option_after_subcommand_is_honoured(monkeypatch, transport):
    assert (
        run(
            monkeypatch,
            transport,
            ["volume", "master", "91", "--yes-very-loud"],
        )
        == 0
    )
    assert transport.writes == direct(p.REG_VOLUME_BASE, b"\x00\x1f")


def test_quieter_volume_writes(monkeypatch, transport):
    assert run(monkeypatch, transport, ["volume", "master", "50"]) == 0
    assert transport.writes == direct(p.REG_VOLUME_BASE, b"\x00\x3c")


def test_volume_dry_run_never_writes(monkeypatch, transport, capsys):
    assert (
        run(monkeypatch, transport, ["volume", "master", "50", "--dry-run"]) == 0
    )
    assert transport.writes == []
    assert "Dry run" in capsys.readouterr().out


def test_very_loud_dry_run_needs_no_opt_in(monkeypatch, transport, capsys):
    assert (
        run(monkeypatch, transport, ["volume", "master", "100", "--dry-run"]) == 3
    )
    assert transport.writes == []
    assert "--yes-very-loud" in capsys.readouterr().err


def test_volume_noop_does_not_write(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "master", "56"]) == 0
    assert transport.writes == []
    assert "No change" in capsys.readouterr().out


def test_very_loud_noop_does_not_need_opt_in(monkeypatch, transport):
    transport.registers[p.REG_VOLUME_BASE] = b"\x00\x19"  # master already 100
    assert run(monkeypatch, transport, ["volume", "master", "100"]) == 0
    assert transport.writes == []


def test_out_of_range_level_is_refused(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "master", "101"]) == 3
    assert transport.writes == []
    assert "0..100" in capsys.readouterr().err


def test_mute_writes_without_confirmation(monkeypatch, transport):
    assert run(monkeypatch, transport, ["mute", "on"]) == 0
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_MUTE, b"\x01")
    ]


def test_mute_off_when_already_unmuted_is_noop(monkeypatch, transport):
    assert run(monkeypatch, transport, ["mute", "off"]) == 0
    assert transport.writes == []


def test_timeout_before_subcommand_is_not_overwritten():
    parser = cli.build_parser()
    args = parser.parse_args(["--timeout", "1234", "monitor"])
    cli._apply_defaults(args)
    assert args.timeout == 1234
    assert args.func is cli.cmd_monitor


def test_common_defaults_are_applied():
    parser = cli.build_parser()
    args = parser.parse_args(["status"])
    cli._apply_defaults(args)
    assert args.bus is None
    assert args.address is None
    assert args.timeout == 2000
    assert args.yes_very_loud is False
    assert args.dry_run is False
    assert not hasattr(args, "yes")
    assert not hasattr(args, "allow_louder")
    assert not hasattr(args, "allow_loud")


def test_removed_flags_are_rejected():
    parser = cli.build_parser()
    for flag in ("--yes", "--allow-louder", "--allow-loud"):
        try:
            parser.parse_args(["volume", "master", "50", flag])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"{flag} should no longer be accepted")


def test_transport_is_closed(monkeypatch, transport):
    assert run(monkeypatch, transport, ["status"]) == 0
    assert transport.closed is True


def test_unknown_channel_is_a_refusal(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "rear", "40"]) == 3
    assert transport.writes == []
    assert "unknown channel" in capsys.readouterr().err


def test_surround_right_is_refused(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "surround-right", "50"]) == 3
    assert transport.writes == []
    assert "mirrors" in capsys.readouterr().err


def test_list_devices_does_not_open_a_device(monkeypatch, transport, capsys):
    calls = []

    def fake_enumerate():
        calls.append(True)
        from ada305.transport import DeviceInfo

        return [DeviceInfo(1, 11, 0x04D2, 0x0305, "ADA305 Speakers")]

    monkeypatch.setattr(cli, "enumerate_devices", fake_enumerate)
    assert run(monkeypatch, transport, ["list-devices"]) == 0
    assert calls == [True]
    assert "04d2:0305" in capsys.readouterr().out
    assert transport.writes == []


def test_describe_report_never_applies_anything():
    report = p.decode_interrupt_report(b"\x01\xff\x00\x00\x00\x00\x00")
    text = cli._describe_report(report)
    assert "mute-toggle" in text
    assert "mode-cycle" not in text
    assert report.volume_delta == -1


def test_release_never_writes_and_explains_follow(
    monkeypatch, transport, capsys
):
    assert run(monkeypatch, transport, ["release"]) == 0
    assert transport.writes == []
    out = capsys.readouterr().out
    assert "0x0500" in out
    assert "ada305 follow" in out
    assert "No write" in out


def test_return_control_alias_is_harmless(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["return-control"]) == 0
    assert transport.writes == []
    assert "ada305 follow" in capsys.readouterr().out


def test_release_dry_run_never_writes(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["release", "--dry-run"]) == 0
    assert transport.writes == []
    assert "No write" in capsys.readouterr().out


def test_monitor_defaults_to_endpoint_packet_size(monkeypatch, transport, capsys):
    transport.interrupt_reports = [b"\x00\x3c"]
    assert run(monkeypatch, transport, ["monitor", "--count", "1"]) == 0
    assert ("interrupt", 2, 2000) in transport.reads
    out = capsys.readouterr().out
    assert "2-byte reads" in out
    assert "raw=00 3c" in out


def test_monitor_survives_overflow_and_keeps_listening(
    monkeypatch, transport, capsys
):
    from ada305.transport import TransportOverflow

    transport.interrupt_reports = [
        TransportOverflow("simulated overflow"),
        b"\x01\x02",
    ]
    assert run(monkeypatch, transport, ["monitor", "--count", "1"]) == 0
    captured = capsys.readouterr()
    assert "overflow" in captured.err
    assert "raw=01 02" in captured.out


def test_help_lists_volume_and_mute_examples(capsys):
    parser = cli.build_parser()
    try:
        parser.parse_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "ada305 volume master 50" in out
    assert "ada305 mute on" in out
    assert "ada305 follow" in out
    assert "ada305 release" in out


def test_follow_applies_selector_and_wheel(monkeypatch, transport, capsys):
    transport.interrupt_reports = [b"\x10\x00", b"\x00\xff"]
    assert (
        run(
            monkeypatch,
            transport,
            ["follow", "--count", "2", "--yes-very-loud"],
        )
        == 0
    )
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE + 4, b"\x00\x01")
    ]
    out = capsys.readouterr().out
    assert "Subwoofer selected" in out


def test_follow_ignores_louder_click_by_default(
    monkeypatch, transport, capsys
):
    transport.interrupt_reports = [b"\x00\x01"]
    assert run(monkeypatch, transport, ["follow", "--count", "1"]) == 0
    assert transport.writes == []
    assert "--allow-increase" in capsys.readouterr().err


def test_follow_allows_louder_click_with_flag(monkeypatch, transport):
    transport.interrupt_reports = [b"\x00\x01"]
    assert (
        run(
            monkeypatch,
            transport,
            ["follow", "--count", "1", "--allow-increase"],
        )
        == 0
    )
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x37")
    ]


def test_follow_dry_run_never_writes(monkeypatch, transport, capsys):
    transport.interrupt_reports = [b"\x10\x00", b"\x00\xff", b"\x01\x00"]
    assert (
        run(
            monkeypatch,
            transport,
            [
                "follow",
                "--count",
                "3",
                "--allow-increase",
                "--yes-very-loud",
                "--dry-run",
            ],
        )
        == 0
    )
    assert transport.writes == []
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "[dry-run]" in out


def test_follow_mute_packet_toggles(monkeypatch, transport):
    transport.interrupt_reports = [b"\x01\x00"]
    assert run(monkeypatch, transport, ["follow", "--count", "1"]) == 0
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_MUTE, b"\x01")
    ]


def test_follow_rejects_bad_options(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["follow", "--count", "-1"]) == 2
    assert transport.writes == []
    assert "--count" in capsys.readouterr().err
    assert (
        run(monkeypatch, transport, ["follow", "--wheel-debounce", "-1"]) == 2
    )
    assert transport.writes == []
    assert "--wheel-debounce" in capsys.readouterr().err
