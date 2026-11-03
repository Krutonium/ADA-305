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


def test_default_command_is_read_only_status(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, []) == 0
    assert transport.writes == []
    out = capsys.readouterr().out
    assert "Master:" in out
    assert "56 dB" in out


def test_status_json(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mute"] is False
    assert payload["mode"] == 0
    assert payload["channels"]["master"]["db"] == 56
    assert payload["channels"]["surround-right"]["mirrored"] is True
    assert transport.writes == []


def test_raw_read_is_read_only(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["raw-read", "0x200", "--length", "2"]) == 0
    assert "00 38" in capsys.readouterr().out
    assert transport.writes == []


def test_volume_louder_without_flag_is_refused(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "master", "40"]) == 3
    assert transport.writes == []
    assert "refusing" in capsys.readouterr().err


def test_volume_louder_with_flag_but_without_yes_is_refused(
    monkeypatch, transport, capsys
):
    assert (
        run(monkeypatch, transport, ["volume", "master", "40", "--allow-louder"])
        == 3
    )
    assert transport.writes == []
    assert "--yes" in capsys.readouterr().err


def test_volume_louder_with_both_flags_writes_once(monkeypatch, transport):
    assert (
        run(
            monkeypatch,
            transport,
            ["volume", "master", "40", "--allow-louder", "--yes"],
        )
        == 0
    )
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x28")
    ]


def test_option_before_subcommand_is_honoured(monkeypatch, transport):
    assert (
        run(
            monkeypatch,
            transport,
            ["--yes", "volume", "master", "40", "--allow-louder"],
        )
        == 0
    )
    assert len(transport.writes) == 1


def test_option_after_subcommand_is_honoured(monkeypatch, transport):
    assert (
        run(
            monkeypatch,
            transport,
            ["volume", "master", "40", "--allow-louder", "--yes"],
        )
        == 0
    )
    assert len(transport.writes) == 1


def test_quieter_volume_with_yes_writes(monkeypatch, transport):
    assert run(monkeypatch, transport, ["volume", "master", "60", "--yes"]) == 0
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_VOLUME_BASE, b"\x00\x3c")
    ]


def test_volume_dry_run_never_writes(monkeypatch, transport, capsys):
    assert (
        run(
            monkeypatch,
            transport,
            ["volume", "master", "60", "--yes", "--dry-run"],
        )
        == 0
    )
    assert transport.writes == []
    assert "Dry run" in capsys.readouterr().out


def test_maximum_volume_refused_with_every_flag(monkeypatch, transport, capsys):
    assert (
        run(
            monkeypatch,
            transport,
            [
                "volume",
                "master",
                "25",
                "--allow-louder",
                "--allow-loud",
                "--yes",
            ],
        )
        == 3
    )
    assert transport.writes == []
    assert "maximum" in capsys.readouterr().err


def test_volume_noop_does_not_write(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "master", "56", "--yes"]) == 0
    assert transport.writes == []
    assert "No change" in capsys.readouterr().out


def test_mute_without_yes_is_refused(monkeypatch, transport):
    assert run(monkeypatch, transport, ["mute", "on"]) == 3
    assert transport.writes == []


def test_mute_with_yes_writes(monkeypatch, transport):
    assert run(monkeypatch, transport, ["mute", "on", "--yes"]) == 0
    assert transport.writes == [
        (p.REQ_REGISTER_WRITE, p.REG_MUTE, b"\x01")
    ]


def test_mute_off_when_already_unmuted_is_noop(monkeypatch, transport):
    assert run(monkeypatch, transport, ["mute", "off", "--yes"]) == 0
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
    assert args.yes is False
    assert args.dry_run is False
    assert args.allow_louder is False
    assert args.allow_loud is False


def test_transport_is_closed(monkeypatch, transport):
    assert run(monkeypatch, transport, ["status"]) == 0
    assert transport.closed is True


def test_unknown_channel_is_a_refusal(monkeypatch, transport, capsys):
    assert run(monkeypatch, transport, ["volume", "rear", "40", "--yes"]) == 3
    assert transport.writes == []
    assert "unknown channel" in capsys.readouterr().err


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
