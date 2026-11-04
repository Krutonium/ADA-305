"""Command-line interface for the Altec Lansing ADA305.

Volumes use a 0..100 level: 0 is minimum volume, 100 is maximum.  The default
command is read-only; digital changes to levels above 90 require
``--yes-very-loud``.  Every write is verified by reading it back.  The physical
follower applies wheel clicks in either direction without extra opt-ins.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence

from . import __version__, protocol as p
from .device import Ada305, ChannelReading, DeviceError
from .follow import Follower, FollowerSettings
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
    TransportOverflow,
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
    examples = """\
examples:
  ada305 status                       read mute, volume, and mode
  ada305 status --json                machine-readable status
  ada305 volume master 50             set master to 50/100
  ada305 volume left 80 --dry-run     show the request without writing
  ada305 volume master 95 --yes-very-loud
  ada305 mute on                      mute the speakers
  ada305 mute off                     unmute (prints a warning)
  ada305 mute toggle --dry-run        preview a mute toggle
  ada305 follow                       apply the speaker's wheel and buttons
  ada305 release                      explain why 0x0500 is not used
  ada305 monitor --count 5            decode five physical-control reports
"""
    parser = argparse.ArgumentParser(
        prog="ada305",
        description="Control Altec Lansing ADA305 USB speakers. "
        "Volume is 0-100 (0 = minimum, 100 = maximum). "
        "Read-only unless a write command is given.",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "mute",
        parents=[common],
        allow_abbrev=False,
        help="read or set the hardware mute bit",
        epilog="""\
examples:
  ada305 mute on
  ada305 mute off
  ada305 mute toggle --dry-run
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mute.add_argument(
        "state", choices=("on", "off", "toggle"), help="mute state to apply"
    )
    mute.set_defaults(func=cmd_mute)

    release = sub.add_parser(
        "release",
        aliases=["return-control"],
        parents=[common],
        allow_abbrev=False,
        help="explain how control works in USB mode (does not write)",
        description="This command does not write register 0x0500. That write "
        "stalls with EPIPE on the reference ADA305 and does not restore the "
        "physical controls. In USB mode the speaker reports wheel/button "
        "events but does not apply them; run 'ada305 follow' to apply them.",
    )
    release.set_defaults(func=cmd_release)

    volume = sub.add_parser(
        "volume",
        parents=[common],
        allow_abbrev=False,
        help="set a channel's volume level (0-100)",
        epilog="""\
examples:
  ada305 volume master 50
  ada305 volume left 80 --dry-run
  ada305 volume master 95 --yes-very-loud
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        default=None,
        help="interrupt read length (default: the endpoint max packet size, "
        "2 on ADA305; ALSERV's 7-byte layout is a Windows buffer, not the "
        "USB packet size)",
    )
    # Do not give this parser its own timeout default: argparse copies every
    # attribute of the subparser namespace back over the parent namespace, so
    # a default here would overwrite an earlier `--timeout`.
    monitor.set_defaults(func=cmd_monitor)

    follow = sub.add_parser(
        "follow",
        parents=[common],
        allow_abbrev=False,
        help="apply physical wheel/button reports (writes volume and mute)",
        description="In USB mode the speaker reports its wheel and buttons but "
        "does not apply them itself. This command applies them in both "
        "directions. Physical wheel clicks are direct user actions, so no "
        "extra opt-in is needed; only the digital volume command gates "
        "very loud levels.",
        epilog="""\
examples:
  ada305 follow                       apply wheel clicks and buttons
  ada305 follow --dry-run             decode events without writing
  ada305 follow --wheel-debounce 0.6  tolerate a slower wheel
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    follow.add_argument(
        "--allow-increase",
        action="store_true",
        help="deprecated and ignored; physical increases are always applied",
    )
    follow.add_argument(
        "--channel-timeout",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="return to master after this much idle time on a channel "
        "(default 3)",
    )
    follow.add_argument(
        "--debounce",
        type=float,
        default=0.35,
        metavar="SECONDS",
        help="ignore repeated selector/mute packets within this window "
        "(default 0.35)",
    )
    follow.add_argument(
        "--wheel-debounce",
        type=float,
        default=0.4,
        metavar="SECONDS",
        help="coalesce repeated wheel packets from one detent within this "
        "window (default 0.4)",
    )
    follow.add_argument(
        "--count",
        type=int,
        default=0,
        help="stop after N reports (default: run until Ctrl-C)",
    )
    follow.add_argument(
        "--length",
        type=int,
        default=None,
        help="interrupt read length (default: endpoint max packet size)",
    )
    follow.set_defaults(func=cmd_follow)

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


def cmd_release(_device: Ada305, _args: argparse.Namespace) -> int:
    print("No write performed.")
    print(
        "Register 0x0500 is not a usable control handoff on this ADA305: the "
        "write stalls with EPIPE, and direct volume writes work without it."
    )
    print(
        "In USB mode the speaker reports its wheel and buttons but does not "
        "apply them itself. Run 'ada305 follow' to have the host apply them."
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
    if report.selector_flags:
        channel = report.selector_channel
        if channel is None:
            names.append(
                f"channel-select=multiple(0x{report.selector_flags:02X})"
            )
        else:
            names.append(f"channel-select={channel.label}")
    if report.reconnect:
        names.append("reconnect/reinit")
    return ", ".join(names) if names else "none"


def cmd_follow(device: Ada305, args: argparse.Namespace) -> int:
    length = (
        args.length
        if args.length is not None
        else device.interrupt_packet_size
    )
    if length <= 0:
        print("--length must be positive", file=sys.stderr)
        return 2
    if args.count < 0:
        print("--count must not be negative", file=sys.stderr)
        return 2
    if args.channel_timeout < 0:
        print("--channel-timeout must not be negative", file=sys.stderr)
        return 2
    if args.debounce < 0:
        print("--debounce must not be negative", file=sys.stderr)
        return 2
    if args.wheel_debounce < 0:
        print("--wheel-debounce must not be negative", file=sys.stderr)
        return 2

    settings = FollowerSettings(
        debounce_seconds=args.debounce,
        wheel_debounce_seconds=args.wheel_debounce,
        channel_timeout_seconds=args.channel_timeout,
        dry_run=args.dry_run,
    )
    follower = Follower(
        device,
        settings,
        warn=lambda message: print(f"WARNING: {message}", file=sys.stderr),
    )

    print(
        "Following interrupt-IN endpoint 0x81. The speaker reports physical "
        "controls; this process applies them in both directions."
    )
    if args.dry_run:
        print("Dry run: reports are decoded but nothing is written.")
    print("Press a channel selector or turn the wheel; Ctrl-C stops.")

    count = 0
    try:
        while args.count == 0 or count < args.count:
            timeout_ms = follower.read_timeout_ms(args.timeout)
            try:
                report = device.read_interrupt(length, timeout_ms)
            except TransportTimeout:
                follower.tick()
                continue
            except TransportOverflow:
                print(
                    "(overflow; report dropped. Use --length "
                    f"{device.interrupt_packet_size} or a multiple of it.)",
                    file=sys.stderr,
                )
                follower.tick()
                continue
            follower.handle_report(report)
            count += 1
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_monitor(device: Ada305, args: argparse.Namespace) -> int:
    length = (
        args.length
        if args.length is not None
        else device.interrupt_packet_size
    )
    if length <= 0:
        print("--length must be positive", file=sys.stderr)
        return 2

    print(
        "Listening on interrupt-IN endpoint 0x81 with "
        f"{length}-byte reads. Read-only: reports are decoded but never applied."
    )
    print("Press a speaker button or turn the wheel; Ctrl-C stops.")
    count = 0
    try:
        while args.count == 0 or count < args.count:
            try:
                report = device.read_interrupt(length, args.timeout)
            except TransportTimeout:
                print("(timeout; still listening)")
                continue
            except TransportOverflow:
                print(
                    "(overflow; report dropped. Use --length "
                    f"{device.interrupt_packet_size} or a multiple of it.)",
                    file=sys.stderr,
                )
                continue
            print(
                f"raw={report.raw.hex(' ') if report.raw else '(none)':<24} "
                f"flags=0x{report.flags:02X} delta={report.volume_delta:+d} "
                f"dB  {_describe_report(report)}"
            )
            count += 1
    except KeyboardInterrupt:
        print("\nStopped.")
        print(
            "Monitoring is read-only. If the wheel/buttons appear dead, that "
            "is expected while no follower is running; use 'ada305 follow'."
        )
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
