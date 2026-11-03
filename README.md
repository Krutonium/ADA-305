# ADA305 — safe Linux control for Altec Lansing USB speakers

A small Python library and CLI for the Altec Lansing **ADA305** USB speaker
control link, reconstructed from the original Windows driver stack by static
analysis. It is intentionally conservative: the default command is read-only,
and no volume write can happen without explicit, layered confirmation.

The ADA305 is the hardware reference and the only model implemented. The other
Altec models recognised by the same driver family are documented in
[`docs/MODELS.md`](docs/MODELS.md).

> **Attenuation, not volume percentage.** The registers hold attenuation in
> dB. **A larger number is quieter.** The master range is `25..96` dB
> attenuation; `25` is the hardware maximum volume and this tool never writes
> it.

---

## Safety model

The original Windows software is free to write any value in the hardware range.
This tool is not:

| Rule | Behaviour |
| --- | --- |
| Default action | `ada305` with no subcommand reads status and exits. |
| Every write needs `--yes` | Without it the CLI prints the exact request and refuses. |
| Volume increases need `--allow-louder` | Any decrease in attenuation is refused otherwise. |
| Loud master values need `--allow-loud` | Master attenuation below `36` dB requires it. |
| Hardware maximum is never written | Master `25` dB is refused even with every flag. |
| No clamping | Out-of-range or unknown values raise; they are never coerced. |
| No hidden initialisation | The tool never resets, re-initialises, or writes at startup. |
| Read-back verification | Every write is read back; a mismatch is an error. |
| Interrupt monitor is read-only | Physical-control reports are decoded, never applied. |
| Tests never touch hardware | All tests use `FakeTransport`; there is no opt-in live test. |

The CLI exits with status `3` for any refused write and `1` for a device or
transport error. A dry run (`--dry-run`) still reads the device to build and
print the plan, but sends nothing.

---

## Requirements

* Linux with `usbfs` (any normal distribution).
* Python 3.9+ and [pyusb](https://pypi.org/project/pyusb/).
* Permission to open the device node (`/dev/bus/usb/BBB/DDD`).

With Nix, you can run everything without installing anything:

```sh
nix-shell -p 'python3.withPackages (ps: [ ps.pyusb ])' --run \
  'PYTHONPATH=src python3 -m ada305 status'
```

Or install into a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
ada305 status
```

### Device permissions

The raw device node is usually `root:root 0664`, so a normal user cannot open
it. Either run the read-only commands with `sudo`, or install a udev rule:

```udev
# /etc/udev/rules.d/60-altec-ada305.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="04d2", ATTR{idProduct}=="0305", MODE="0660", TAG+="uaccess"
```

Reload with `sudo udevadm control --reload-rules && sudo udevadm trigger`.
No kernel driver is bound to the vendor interface; the tool does not detach or
rebind anything (except claiming interface 0 for the interrupt monitor).

---

## CLI

```text
ada305 [options] [command]

commands:
  status        read mute, volume, and mode (default)
  mute on|off|toggle
  volume CHANNEL DB
  raw-read REGISTER [--length N]
  monitor       decode interrupt-IN reports (read-only)
  list-devices  list matching USB devices
```

Common options: `--bus N`, `--address N`, `--timeout MS`, `--yes`,
`--dry-run`, `--allow-louder`, `--allow-loud`, `--verbose`.

Options work before or after the subcommand.

### Read status

```sh
sudo env PYTHONPATH=src python3 -m ada305 status
sudo env PYTHONPATH=src python3 -m ada305 status --json
```

Example output from the reference device:

```text
ADA305 Speakers (04d2:0305, bus 001 address 011)
Mute:            off
Mode:            0 (state byte 1, mode byte 0)
Master:           56 dB attenuation  [25..96 dB attenuation (loudest allowed write: 26 dB)]
Left:              0 dB attenuation  [0..31 dB attenuation]
...
Surround right:  mirrors surround-left (31 dB); register 0x206 is not an independent ADA305 control
```

### Preview a volume change

```sh
# Quieter: attenuation goes UP from 56 to 60 dB. Still needs --yes to write.
python3 -m ada305 volume master 60 --dry-run

# Then, if the printed request is what you want:
python3 -m ada305 volume master 60 --yes
```

A louder change needs the extra flag and prints a warning first:

```sh
python3 -m ada305 volume master 40 --allow-louder --dry-run
python3 -m ada305 volume master 40 --allow-louder --yes
```

The loud zone and the hardware maximum are refused:

```sh
# 30 dB is louder than the 36 dB guard: needs --allow-loud as well.
python3 -m ada305 volume master 30 --allow-louder --allow-loud --yes

# 25 dB is the hardware maximum: always refused, no flag can override it.
python3 -m ada305 volume master 25 --allow-louder --allow-loud --yes
# -> refusing: 25 dB is the hardware maximum volume ...
```

### Mute

```sh
python3 -m ada305 mute on --yes
python3 -m ada305 mute off --yes    # warns before unmuting
python3 -m ada305 mute toggle --dry-run
```

### Raw reads and monitoring

```sh
python3 -m ada305 raw-read 0x200 --length 2
python3 -m ada305 monitor --count 5
```

`monitor` listens on interrupt-IN endpoint `0x81`, decodes the 7-byte report
layout used by ALSERV, and never applies the requested change. Reports are
only generated when a physical control is used; idle reads time out, which the
monitor reports and then continues.

### List devices

```sh
python3 -m ada305 list-devices
```

---

## Library

```python
from ada305 import Ada305, PyUsbTransport, SafetyError

with PyUsbTransport.open() as transport:
    device = Ada305(transport)

    status = device.read_status()
    print(status.volume_db("master"))

    # Prepare = read current value + validate policy. Sends nothing.
    plan = device.prepare_volume("master", 60)
    print(plan.describe(), plan.encoded.hex(" "))

    # Execute only after explicit approval. confirm=True is mandatory.
    device.execute_volume(plan, confirm=True)
```

`prepare_volume` refuses volume increases unless `allow_louder=True`, refuses
master values below `36` dB unless `allow_loud=True`, and refuses the hardware
maximum unconditionally. `execute_volume` re-reads the register immediately
before writing and aborts if it changed while planning. All writes verify the
read-back.

The transport is a small protocol (`read_control`, `write_control`,
`read_interrupt`), so the device layer is easy to test with a fake; see
`tests/conftest.py`.

---

## Testing

```sh
nix-shell -p 'python3.withPackages (ps: [ ps.pytest ps.pyusb ])' --run \
  'PYTHONPATH=src pytest'
```

The suite covers codecs, channel aliases, every range and loudness refusal,
no-op handling, exact write encodings, read-back mismatches, and CLI option
placement. No test opens a real USB device, and there is no test that performs a
real volume write.

---

## Documentation

* [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — control transfers, register map,
  volume encoding, mode/state logic, interrupt reports, and evidence.
* [`docs/MODELS.md`](docs/MODELS.md) — ADA310/600/315/355/495 and legacy
  profiles, with ranges and what is (not) implemented.

## Limitations

* Only the ADA305 (`04d2:0305`) is implemented and tested on hardware.
* `ADA495` shares the ADA305 protocol and ranges and should work, but has not
  been tested here.
* The interrupt report layout is recovered from ALSERV. One report has been
  observed (`00 3c`: no flags, delta `+60`), but its trigger is not understood;
  `monitor` remains read-only and experimental. Applying that report naively
  would have clamped master to the maximum volume.
* Mode switching is read but deliberately not writable: the exact side effects
  of writing `0xA500`/state are not needed for safe volume control.
* Input routing (`0x300`) and model-specific setup (`0x500`) registers are
  documented but not written.
