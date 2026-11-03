"""Command-line interface for the Altec Lansing ADA305.

Default behaviour is read-only.  Any write requires ``--yes`` on the command
line; volume increases additionally require ``--allow-louder`` and the loud
end of the master range requires ``--allow-loud``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence

from . import protocol as p
from .device import Ada305, DeviceError
from .safety import (
    SafetyError,
    describe_limits,
    MASTER_LOUD_THRESHOLD_DB,
)
from .transport import (
    PyUsbTransport,
    TransportError,
    TransportTimeout,
    enumerate_devices,
)

DEFAULTS = {
    "bus": None,
    "address": None,
    "timeout": 2000,
    "yes": False,
    "dry_run": False,
    "allow_louder": False,
    "allow_loud": False,
    "verbose": False,
}


def _parse_int(text: str) -> int:
    try:
        return int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an integer (use decimal or 0x-prefixed hex)"
        ) from None


def _common_options() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS keeps a value given before the subcommand from being clobbered
    # by the subparser's defaults.
    common.add_argument("--bus", type=int, default=argparse.SUPPRESS,
                        help="USB bus number (see list-devices)")
    common.add_argument("--address", type=int, default=argparse.SUPPRESS,
                        help="USB device address (see list-devices)")
    common.add_argument("--timeout", type=int, default=argparse.SUPPRESS,
                        metavar="MS", help="USB timeout in milliseconds "
                                           "(default 2000)")
    common.add_argument("--yes", action="store_true", default=argparse.SUPPRESS,
                        help="actually perform the requested write")
    common.add_argument("--dry-run", action="store_true",
                        default=argparse.SUPPRESS,
                        help="show the encoded request but do not write")
    common.add_argument("--allow-louder", action="store_true",
                        default=argparse.SUPPRESS,
                        help="permit a volume change that increases loudness")
    common.add_argument("--allow-loud", action="store_true",
                        default=argparse.SUPPRESS,
                        help=f"permit master attenuation below "
                             f"{MASTER_LOUD_THRESHOLD_DB} dB (loud zone)")
    common.add_argument("--verbose", action="store_true",
                        default=argparse.SUPPRESS,
                        help="show raw register bytes")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    parser = argparse.ArgumentParser(
        prog="ada305",
        description="Safe control for Altec Lansing ADA305 USB speakers. "
                    "Read-only unless a write command is given with --yes.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version="ada305 0.1.0")
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser(
        "status", aliases=["info"], parents=[common],
        help="read mute, volume, and mode (default)",
    )
    status.add_argument("--json", action="store_true",
                        help="machine-readable output")
    status.set_defaults(func=cmd_status)

    mute = sub.add_parser("mute", parents=[common],
                          help="read or set the hardware mute bit")
    mute.add_argument("state", choices=("on", "off", "toggle"),
                      help="mute state to apply")
    mute.set_defaults(func=cmd_mute)

    volume = sub.add_parser("volume", parents=[common],
                            help="read or set a channel's attenuation in dB")
    volume.add_argument("channel", help="master, left, right, center, sub, "
                                        "surround-left (aliases accepted)")
    volume.add_argument("db", type=int,
                        help="attenuation in dB (higher = quieter); an "
                             "integer, never a percentage")
    volume.set_defaults(func=cmd_volume)

    raw = sub.add_parser("raw-read", parents=[common],
                         help="read an arbitrary register (read-only)")
    raw.add_argument("register", type=_parse_int,
                     help="register address, decimal or 0x-prefixed")
    raw.add_argument("--length", type=int, default=1,
                     help="number of bytes to read (default 1)")
    raw.set_defaults(func=cmd_raw_read)

    monitor = sub.add_parser("monitor", parents=[common],
                             help="decode interrupt-IN reports (read-only; "
                                  "never applies them)")
    monitor.add_argument("--count", type=int, default=0,
                         help="stop after N reports (default: run until "
                              "Ctrl-C)")
    monitor.add_argument("--length", type=int, default=7,
                         help="interrupt read length (default 7, matching "
                              "ALSERV; ADA305 uses the first 2 bytes)")
    # Do not give this parser its own timeout default: argparse copies every
    # attribute of the subparser namespace back over the parent namespace, so
    # a default here would overwrite an earlier `--timeout`.
    monitor.set_defaults(func=cmd_monitor)

    listing = sub.add_parser("list-devices", parents=[common],
                             help="list matching USB devices without opening "
                                  "them")
    listing.set_defaults(func=None)

    parser.set_defaults(command="status", func=cmd_status)
    return parser


def _apply_defaults(args: argparse.Namespace) -> None:
    for name, value in DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)


def _device_description(device: Ada305) -> str:
    return getattr(device.transport, "description", "ADA305")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_status(device: Ada305, args: argparse.Namespace) -> int:
    status = device.read_status()
    if getattr(args, "json", False):
        payload = {
            "device": _device_description(device),
            "mute": status.mute,
            "mode": status.mode,
            "mode_state": status.mode_state,
            "mode_byte": status.mode_byte,
            "channels": {
                key: {
                    "db": reading.db,
                    "register": f"0x{reading.channel.register:03X}",
                    "mirrored": reading.mirrored,
                    "source": reading.source,
                }
                for key, reading in status.channels.items()
            },
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(_device_description(device))
    print(f"{'Mute:':<17}{'on' if status.mute else 'off'}")
    print(f"{'Mode:':<17}{status.mode} "
          f"(state byte {status.mode_state}, mode byte {status.mode_byte})")
    for channel in p.CHANNELS:
        reading = status.channels[channel.key]
        if channel.mirror_of is not None:
            print(f"{channel.label + ':':<17}mirrors {channel.mirror_of} "
                  f"({reading.db} dB); register 0x{channel.register:03X} is "
                  f"not an independent ADA305 control")
            continue
        line = (f"{channel.label + ':':<17}{reading.db:3d} dB attenuation  "
                f"[{describe_limits(channel)}]")
        if getattr(args, "verbose", False):
            line += f"  raw={reading.raw.hex(' ')}"
        print(line)
    return 0


def cmd_mute(device: Ada305, args: argparse.Namespace) -> int:
    current = device.read_mute()
    if args.state == "toggle":
        target = not current
    else:
        target = args.state == "on"

    print(f"Current mute:  {'on' if current else 'off'}")
    print(f"Requested:     {'on' if target else 'off'}")

    if current == target:
        print("No change; nothing to write.")
        return 0
    if target is False:
        print("WARNING: unmuting may make audio audible.", file=sys.stderr)

    if args.dry_run:
        print("Dry run: not writing.")
        return 0
    if not args.yes:
        print("Refusing to write without --yes. Nothing sent.", file=sys.stderr)
        return 3

    actual = device.write_mute(target, confirm=True)
    print(f"Wrote mute {'on' if target else 'off'}; "
          f"read-back {'on' if actual else 'off'}.")
    return 0


def cmd_volume(device: Ada305, args: argparse.Namespace) -> int:
    plan = device.prepare_volume(
        args.channel,
        args.db,
        allow_louder=args.allow_louder,
        allow_loud=args.allow_loud,
    )

    print(f"Current {plan.channel.label}: {plan.current_db} dB attenuation")
    print(f"Requested:         {plan.requested_db} dB attenuation "
          f"({plan.describe()})")
    print(f"Register:          0x{plan.channel.register:03X} <- "
          f"{plan.encoded.hex(' ')}")

    if plan.is_noop:
        print("No change; nothing to write.")
        return 0
    if plan.is_louder:
        print("WARNING: this is a volume INCREASE (more loudness).",
              file=sys.stderr)

    if args.dry_run:
        print("Dry run: not writing.")
        return 0
    if not args.yes:
        print("Refusing to write without --yes. Nothing sent.",
              file=sys.stderr)
        return 3

    actual = device.execute_volume(plan, confirm=True)
    print(f"Wrote {len(plan.encoded)} byte(s); read-back {actual} dB "
          f"attenuation.")
    return 0


def cmd_raw_read(device: Ada305, args: argparse.Namespace) -> int:
    if args.length < 0:
        print("length must be non-negative", file=sys.stderr)
        return 2
    raw = device.read_register(args.register, args.length)
    print(f"register 0x{args.register:04X} length {args.length}: "
          f"{raw.hex(' ') if raw else '(empty)'}")
    return 0


def _describe_report(report: p.InterruptReport) -> str:
    names: List[str] = []
    if report.mute_toggle:
        names.append("mute-toggle")
    if report.mode_cycle:
        names.append("mode-cycle (ignored on ADA305)")
    if report.unmute_request:
        names.append("unmute-request (non-ADA305)")
    if report.input_select:
        names.append(f"input-select=0x{report.input_select:02X} "
                     f"(non-ADA305)")
    if report.reconnect:
        names.append("reconnect/reinit")
    return ", ".join(names) if names else "none"


def cmd_monitor(device: Ada305, args: argparse.Namespace) -> int:
    print("Listening on interrupt-IN endpoint 0x81. Read-only: reports are "
          "decoded but never applied.")
    print("Press a speaker button or turn the knob; Ctrl-C stops.")
    count = 0
    try:
        while args.count == 0 or count < args.count:
            try:
                report = device.read_interrupt(args.length, args.timeout)
            except TransportTimeout:
                print("(timeout; still listening)")
                continue
            print(f"raw={report.raw.hex(' ') if report.raw else '(none)':<24} "
                  f"flags=0x{report.flags:02X} delta={report.volume_delta:+d} "
                  f"dB  {_describe_report(report)}")
            count += 1
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_list_devices(_device: Optional[Ada305], _args: argparse.Namespace) -> int:
    infos = enumerate_devices()
    if not infos:
        print(f"no {p.VID_ALTEC:04x}:{p.PID_ADA305:04x} devices found")
        return 1
    for info in infos:
        print(info.label())
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_defaults(args)

    if args.command == "list-devices":
        return cmd_list_devices(None, args)

    try:
        transport = PyUsbTransport.open(
            bus=args.bus, address=args.address, timeout_ms=args.timeout
        )
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        device = Ada305(transport)
        return int(args.func(device, args) or 0)
    except TransportTimeout as exc:
        print(f"timeout: {exc}", file=sys.stderr)
        return 1
    except TransportError as exc:
        print(f"transport error: {exc}", file=sys.stderr)
        return 1
    except SafetyError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 3
    except DeviceError as exc:
        print(f"device error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
