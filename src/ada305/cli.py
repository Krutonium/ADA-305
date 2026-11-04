"""Command-line interface for the Altec Lansing ADA305.

Volumes use a 0..100 level: 0 is minimum volume, 100 is maximum.  The default
command is read-only; levels above 90 require ``--yes-very-loud``.  Volume
increases print a warning, and every write is verified by reading it back.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence

from . import __version__, protocol as p
from .device import Ada305, ChannelReading, DeviceError
from .safety import (
    LEVEL_MAX,
    LEVEL_MIN,
    VERY_LOUD_LEVEL,
    SafetyError,
    db_to_level,
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
    "dry_run": False,
    "yes_very_loud": False,
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
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    # SUPPRESS keeps a value given before the subcommand from being clobbered
    # by the subparser's defaults.
    common.add_argument(
        "--bus",
        type=int,
        default=argparse.SUPPRESS,
        help="USB bus number (see list-devices)",
    )
    common.add_argument(
        "--address",
        type=int,
        default=argparse.SUPPRESS,
        help="USB device address (see list-devices)",
    )
    common.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        metavar="MS",
        help="USB timeout in milliseconds (default 2000)",
    )
    common.add_argument(
        "--yes-very-loud",
        action="store_true",
        default=argparse.SUPPRESS,
        help=f"permit volume levels above {VERY_LOUD_LEVEL} (91-100)",
    )
    common.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show the encoded request but do not write",
    )
    common.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show raw register bytes",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    parser = argparse.ArgumentParser(
        prog="ada305",
        description="Control Altec Lansing ADA305 USB speakers. "
        "Volume is 0-100 (0 = minimum, 100 = maximum). "
        "Read-only unless a write command is given.",
        parents=[common],
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version", action="version", version=f"ada305 {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser(
        "status",
        aliases=["info"],
        parents=[common],
        allow_abbrev=False,
        help="read mute, volume, and mode (default)",
    )
    status.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    status.set_defaults(func=cmd_status)

    mute = sub.add_parser(
        "mute", parents=[common], allow_abbrev=False,
        help="read or set the hardware mute bit"
    )
    mute.add_argument(
        "state", choices=("on", "off", "toggle"), help="mute state to apply"
    )
    mute.set_defaults(func=cmd_mute)

    volume = sub.add_parser(
        "volume",
        parents=[common],
        allow_abbrev=False,
        help="set a channel's volume level (0-100)",
    )
    volume.add_argument(
        "channel",
        help="master, left, right, center, sub, surround-left "
        "(aliases accepted)",
    )
    volume.add_argument(
        "level",
        type=int,
        metavar="LEVEL",
        help=f"volume level {LEVEL_MIN}-{LEVEL_MAX} "
        f"(0 = minimum, {LEVEL_MAX} = maximum)",
    )
    volume.set_defaults(func=cmd_volume)

    raw = sub.add_parser(
        "raw-read",
        parents=[common],
        allow_abbrev=False,
        help="read an arbitrary register (read-only)",
    )
    raw.add_argument(
        "register",
        type=_parse_int,
        help="register address, decimal or 0x-prefixed",
    )
    raw.add_argument(
        "--length", type=int, default=1, help="number of bytes to read (default 1)"
    )
    raw.set_defaults(func=cmd_raw_read)

    monitor = sub.add_parser(
        "monitor",
        parents=[common],
        allow_abbrev=False,
        help="decode interrupt-IN reports (read-only; never applies them)",
    )
    monitor.add_argument(
        "--count",
        type=int,
        default=0,
        help="stop after N reports (default: run until Ctrl-C)",
    )
    monitor.add_argument(
        "--length",
        type=int,
        default=7,
        help="interrupt read length (default 7, matching ALSERV; "
        "ADA305 uses the first 2 bytes)",
    )
    # Do not give this parser its own timeout default: argparse copies every
    # attribute of the subparser namespace back over the parent namespace, so
    # a default here would overwrite an earlier `--timeout`.
    monitor.set_defaults(func=cmd_monitor)

    listing = sub.add_parser(
        "list-devices",
        parents=[common],
        allow_abbrev=False,
        help="list matching USB devices without opening them",
    )
    listing.set_defaults(func=None)

    parser.set_defaults(command="status", func=cmd_status)
    return parser


def _apply_defaults(args: argparse.Namespace) -> None:
    for name, value in DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)


def _device_description(device: Ada305) -> str:
    return getattr(device.transport, "description", "ADA305")


def _level_for(reading: ChannelReading) -> int:
    return db_to_level(reading.channel, reading.db)


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
                    "level": _level_for(reading),
                    "db": reading.db,
                    "very_loud": _level_for(reading) > VERY_LOUD_LEVEL,
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
    print(
        f"{'Mode:':<17}{status.mode} "
        f"(state byte {status.mode_state}, mode byte {status.mode_byte})"
    )
    print(f"{'Volume:':<17}0 = minimum, {LEVEL_MAX} = maximum")
    for channel in p.CHANNELS:
        reading = status.channels[channel.key]
        level = _level_for(reading)
        if channel.mirror_of is not None:
            print(
                f"{channel.label + ':':<17}mirrors {channel.mirror_of} "
                f"(level {level}/{LEVEL_MAX}, {reading.db} dB); register "
                f"0x{channel.register:03X} is not an independent ADA305 control"
            )
            continue
        line = (
            f"{channel.label + ':':<17}{level:3d}/{LEVEL_MAX}  "
            f"({reading.db:2d} dB attenuation)"
        )
        if level > VERY_LOUD_LEVEL:
            line += "  [very loud]"
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

    actual = device.write_mute(target)
    print(
        f"Wrote mute {'on' if target else 'off'}; "
        f"read-back {'on' if actual else 'off'}."
    )
    return 0


def cmd_volume(device: Ada305, args: argparse.Namespace) -> int:
    plan = device.prepare_volume(
        args.channel,
        args.level,
        allow_very_loud=args.yes_very_loud,
    )

    print(
        f"Current {plan.channel.label}: level {plan.current_level}/{LEVEL_MAX} "
        f"({plan.current_db} dB attenuation)"
    )
    print(
        f"Requested:         level {plan.requested_level}/{LEVEL_MAX} "
        f"({plan.requested_db} dB attenuation)"
    )
    print(f"Change:            {plan.describe()}")
    print(
        f"Register:          0x{plan.channel.register:03X} <- "
        f"{plan.encoded.hex(' ')}"
    )

    if plan.is_noop:
        print("No change; nothing to write.")
        return 0
    if plan.is_louder:
        print(
            "WARNING: this is a volume INCREASE (more loudness).",
            file=sys.stderr,
        )
    if plan.is_very_loud:
        print(
            f"WARNING: level {plan.requested_level} is above "
            f"{VERY_LOUD_LEVEL} (very loud).",
            file=sys.stderr,
        )

    if args.dry_run:
        print("Dry run: not writing.")
        return 0

    result = device.execute_volume(plan)
    print(
        f"Wrote {len(plan.encoded)} byte(s); read-back level "
        f"{result.level}/{LEVEL_MAX} ({result.db} dB attenuation)."
    )
    return 0


def cmd_raw_read(device: Ada305, args: argparse.Namespace) -> int:
    if args.length < 0:
        print("length must be non-negative", file=sys.stderr)
        return 2
    raw = device.read_register(args.register, args.length)
    print(
        f"register 0x{args.register:04X} length {args.length}: "
        f"{raw.hex(' ') if raw else '(empty)'}"
    )
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
        names.append(f"input-select=0x{report.input_select:02X} (non-ADA305)")
    if report.reconnect:
        names.append("reconnect/reinit")
    return ", ".join(names) if names else "none"


def cmd_monitor(device: Ada305, args: argparse.Namespace) -> int:
    print(
        "Listening on interrupt-IN endpoint 0x81. Read-only: reports are "
        "decoded but never applied."
    )
    print("Press a speaker button or turn the knob; Ctrl-C stops.")
    count = 0
    try:
        while args.count == 0 or count < args.count:
            try:
                report = device.read_interrupt(args.length, args.timeout)
            except TransportTimeout:
                print("(timeout; still listening)")
                continue
            print(
                f"raw={report.raw.hex(' ') if report.raw else '(none)':<24} "
                f"flags=0x{report.flags:02X} delta={report.volume_delta:+d} "
                f"dB  {_describe_report(report)}"
            )
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
