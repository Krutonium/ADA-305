# ADA305 control protocol

Everything in this document was recovered by static analysis of the original
Windows AMS24 driver stack (`al60.sys`, `ALSERV.exe`) and checked with
read-only requests and live physical-control captures against a physical
ADA305 (`04d2:0305`). See [Provenance](#provenance) for file hashes.

The device exposes a vendor-specific USB interface:

| Property | Value |
| --- | --- |
| VID:PID | `04d2:0305` (ADA305), `04d2:ff05` (ADA495) |
| Speed | low speed (reference unit) |
| Interface | 0, class `ff`, subclass `01` |
| Endpoints | one interrupt-IN `0x81`, `wMaxPacketSize=2`, interval 32 |
| Kernel driver | none bound |

All host-to-device control is done with **class/interface control transfers**.
The only endpoint carries physical-control reports.

---

## 1. Control requests

`al60.sys` builds `URB_FUNCTION_CLASS_INTERFACE` requests from a small user
structure:

```text
offset  size  field
+0x00   1     bRequest
+0x02   2     wValue     (little endian) -> register address
+0x04   2     wIndex     (little endian) -> 0xF01 in the Windows driver
+0x06   2     wLength    (little endian)
+0x08   ptr   data buffer (OUT requests)
```

`ALSERV.exe` is the component that actually issues register I/O:

| Operation | `bmRequestType` | `bRequest` | `wValue` | `wIndex` | `wLength` |
| --- | --- | --- | --- | --- | --- |
| Read register | `0xA1` | `8` | register | `0x0000`¹ | register size |
| Write register | `0x21` | `7` | register | `0x0000`¹ | register size |
| Read state | `0xA1` | `6` | `0` | `0x0000`¹ | `1` |
| Write state | `0x21` | `5` | `0` | `0x0A01` | `1` |

¹ The Windows driver used `wIndex = 0xF01` for register I/O. Linux `usbfs`
treats an interface-recipient request's `wIndex` as an interface number, using
only the low byte. `0xF01` therefore selects interface 1, which does not exist,
and the kernel rejects the request with `ENOENT` (`[Errno 2] Entity not found`)
before it reaches the device. The ADA305 ignores `wIndex` for these register
requests: live reads with `0x0000`, `0x0A00`, and `0x0F00` all returned the same
data. This library writes `wIndex = 0x0000`.

`write state` is documented for completeness. The library does **not** send it:
mode changes are not needed for volume control and their side effects are not
fully characterised.

---

## 2. Register map

| Register | Size | Meaning | Access |
| --- | --- | --- | --- |
| `0x0100` | 1 | Mute: `0x00` unmuted, `0x01` muted | R/W |
| `0x0200` | 2 | Master attenuation | R/W |
| `0x0201` | 2 | Left / front-left attenuation | R/W |
| `0x0202` | 2 | Right / front-right attenuation | R/W |
| `0x0203` | 2 | Center attenuation | R/W |
| `0x0204` | 2 | Subwoofer attenuation | R/W |
| `0x0205` | 2 | Surround-left attenuation | R/W |
| `0x0206` | 2 | Surround-right on some models; **not independent on ADA305** | n/a |
| `0x0300` | 1 | Channel-selector write (sub/center/surround); LEDs did not respond on the reference unit | W (not used) |
| `0x0500` | 1 | ALSERV host/local handoff: `2` = host applying, `3` = local; **stalls on the reference unit** | not used |
| `0xA500` | 1 | Mode state byte | R/W (read only here) |

### ADA305 channels

ALSERV enumerates seven logical channels (`0..6`) and maps them to
`0x0200 + index`. For product `0x0305` (and `0x0005`, normalised to `0x0305`)
it special-cases index 6:

```c
if ((product == 5 || product == 0x305) && channel == 6)
    value = read_channel(5);
```

So the ADA305 has six independent controls, and logical channel 6 (surround
right) is an alias of channel 5 (surround left). `0x0206` is not a useful
independent control on this model. Reads of surround-right go through `0x0205`;
writes to it are refused. The same mapping applies when ALSERV writes channel 6.

The order of the ADA305 `AddReg` section in `ALADA.INF` is: Master, Left,
Right, Center, Sub, SurLeft, SurRight.

---

## 3. Volume encoding

The volume registers hold **attenuation**, not gain or a Q8 fraction:

```text
wire bytes: 00 dd          (little endian)
dd          attenuation in dB
```

A larger `dd` is **quieter**. Live dump of the reference unit:

```text
0x0200  00 38   master 56 dB
0x0201  00 00   left 0 dB
0x0202  00 00   right 0 dB
0x0203  00 00   center 0 dB
0x0204  00 00   sub 0 dB
0x0205  00 1f   surround-left 31 dB
0x0206  00 00   (not used by ADA305)
```

`encode_volume(db)` emits `bytes((0x00, db))`, i.e. a write of value `db << 8`.
Any high byte other than `0x00` is rejected rather than interpreted.

### Hardware ranges (ADA305 / ADA495)

From `ALADA.INF` and `acs495.inf`:

```inf
VolRange\Master,Min = 0x60   ; value at minimum volume = 96 dB attenuation
VolRange\Master,Max = 0x19   ; value at maximum volume = 25 dB attenuation
VolRange\Left..SurRight,Min = 0x1F
VolRange\Left..SurRight,Max = 0x00
```

The INF `Min`/`Max` names refer to the *volume* endpoints, not the numeric
direction, so they are inverted relative to attenuation:

| Control | Attenuation range | Quietest | Loudest |
| --- | --- | --- | --- |
| Master | `25..96` dB | `96` | `25` |
| Left/right/center/sub/surround | `0..31` dB | `31` | `0` |

This is confirmed by the legacy `MDSHELL.INI` profile:

```ini
[USB305]
ReportSize=2
MasterMaxDb=96
MasterMinDb=25
FrontLeftMaxDb=31
...
```

### User-facing 0..100 levels

The CLI and library do not expose attenuation directly. They map it to a linear
`0..100` level where **`0` is minimum volume** and **`100` is maximum volume**.
The mapping is per channel because the two hardware ranges differ:

| Channel | Level `0` | Level `50` | Level `100` |
| --- | --- | --- | --- |
| Master | `96` dB | `60` dB | `25` dB |
| Left/right/center/sub/surround | `31` dB | `15` dB | `0` dB |

`level_to_db` rounds half-up, and `db_to_level` floors. The master scale has
`101` levels across `72` attenuation steps, so several levels map to the same
dB value; status output reports the effective level after a write. No value is
ever clamped: a level outside `0..100` is refused.

Changes whose target level is `91..100` require an explicit
`--yes-very-loud` / `allow_very_loud=True`. A request that maps to the current
attenuation is a no-op, sends nothing, and does not require the opt-in.
Volume increases print a warning but need no additional flag. `100` maps to the
hardware maximum volume (`25` dB master attenuation); it is writable only with
the very-loud opt-in. These rules apply to host-initiated volume changes such as
`ada305 volume` and `Ada305.prepare_volume`. Physical wheel reports are direct
user actions and are handled separately in section 9.

---

## 4. Mute

`0x0100` is a one-byte boolean. Live value `0x00` (unmuted). Writes are
`0x00` or `0x01`; any other value read back is rejected. A no-op write of the
current value was used once to validate the OUT path and read back unchanged.

---

## 5. Host/local control handoff (`0x0500`) — not used

ALSERV brackets every host-initiated volume write with a one-byte write to
register `0x0500`:

```c
write_register(0x0500, 2);   // host is applying the change
write_register(0x0200 + channel, encoded_volume);
write_register(0x0500, 3);   // nominal "local control"
```

This is `FUN_00403ee0(2)` / `FUN_00403ee0(3)` in `ALSERV.exe`, called directly
around `FUN_00404250` (the volume write). The same function is only called with
`1` at startup on model `0x60`, where its meaning is not established. No other
register I/O is bracketed this way, including the mute write (`0x0100`).

On the reference ADA305, every `0x0500` write tried during this work stalled
with `EPIPE` (`[Errno 32] Pipe error`). Direct writes to `0x0200` succeeded and
read back exactly, with and without the bracket. The bracket is therefore an
ALSERV implementation detail that this unit does not need, not a usable
"return control" command.

In USB mode the physical controls are not applied on the device at all: the
speaker reports wheel and button events and expects the host to apply them.
The library writes volume registers directly, and `ada305 follow` applies the
reports. `ada305 release` performs no write.

This is distinct from `0xA500`, which is the *mode state byte* read by
`FUN_00403cc0` and written by `FUN_00404000` as part of mode selection.

---

## 6. State and mode

Mode is derived from two reads:

1. `bRequest 6`, `wValue 0`, 1 byte -> state byte.
2. `bRequest 8`, `wValue 0xA500`, 1 byte -> mode byte.

```text
if state_byte != 1:  mode = state_byte
else:                mode = 0 if mode_byte == 0 else 1
```

Live reference unit: state byte `0x01`, mode byte `0x00` -> mode `0`.

`ALADA.INF` documents three ADA305 modes: `0`, `1`, `3`
(`NumberOfModes=3`, entries `000=0`, `001=1`, `002=3`). ADA310/600 document
four modes `0..3`. The interrupt flag `0x02` requests a mode cycle on models
other than ADA305/ADA495; ALSERV ignores it for those two.

Mode is read by `ada305 status` and never written.

---

## 7. Interrupt-IN reports

Endpoint `0x81`, `wMaxPacketSize=2`. ALSERV nevertheless supplies a **7-byte
buffer** to `ReadFileEx` because the same code serves 6-byte remote-control
models. The Windows driver's interrupt URB uses the endpoint's max packet size,
not that 7-byte user buffer. The ADA305 uses only the first two bytes
(`ReportSize=2` in the old USB305 profile).

On Linux, requesting 7 bytes directly from a 2-byte interrupt endpoint can make
usbfs abort the URB with `EOVERFLOW` (errno 75): the fourth full packet would
make 8 bytes and overrun the 7-byte buffer. The transport therefore allocates a
whole number of max-size packets and trims the result, and `monitor` defaults to
`2` bytes. If an overflow still occurs, the monitor drops that report and keeps
listening instead of crashing.

```text
byte 0  flags
byte 1  signed attenuation delta
bytes 2..6  model-specific (zero/unused on ADA305)
```

Flag bits recovered from ALSERV's parser:

| Bit | Meaning | ADA305 | Live value |
| --- | --- | --- | --- |
| `0x01` | Toggle mute | yes | `01 00` (repeats while held) |
| `0x02` | Cycle mode | ignored | not observed |
| `0x08` | Unmute request | non-ADA305 | not observed |
| `0x10` | Select subwoofer | yes | `10 00` (repeats) |
| `0x20` | Select center | yes | `20 00` (repeats) |
| `0x40` | Select surround | yes | `40 00` (repeats) |
| `0x80` | Reconnect / re-initialise | yes | not observed in the capture |

`00 00` is idle / button-release and does not deselect a channel. A selector
press emits several identical packets, so a host-side follower must debounce
them or it will toggle the selection repeatedly.

The delta is signed. ALSERV computes:

```text
new_attenuation = current_attenuation - delta
```

so a **positive delta means louder** (attenuation decreases). Live wheel
captures: `00 01` is one click louder and `00 ff` is one click quieter. One
physical detent emits one to three identical reports in a short burst, so a
host-side follower must coalesce them or one click will make several volume
steps. A read-only capture of five deliberate clockwise clicks, one second
apart, produced bursts of 3, 1, 3, 1, and 3 identical `00 01` reports. The
report is a *request*: the host is expected to apply it with a control write.
The device does not change its own volume register from a button press. This is
why the wheel can click with no audible change while only a read-only monitor
is running.

ALSERV clamps the computed value to the channel's stored minimum/maximum before
writing it. This library deliberately does not: it ignores a click that would
leave the documented range. That matters because a report can carry a large
delta. One report has been observed on the reference unit while idle:

```text
raw=00 3c   flags=0x00   delta=+60
```

If that were applied naively to master `56`, the result would be `56 - 60 = -4`;
ALSERV's clamp would take it to `25` dB — the hardware maximum. `ada305
monitor` therefore only decodes reports and never applies them. `ada305 follow`
accepts only real wheel deltas (`+1` / `-1`) and refuses changes outside the
documented range.

---

## 8. Safety invariants implemented by this library

1. Reads only unless a specific write command is given.
2. Writes are validated against the documented attenuation range; unknown
   channels and malformed register bytes are errors.
3. Volume levels use `0..100` (`0` = minimum, `100` = maximum) and are never
   clamped.
4. Host-initiated target levels above `90` require
   `allow_very_loud=True` / `--yes-very-loud`, unless the request is a no-op
   that sends nothing. Physical wheel increases are not host-initiated volume
   commands and do not need this opt-in.
5. Explicit digital volume commands print a warning for increases. The follower
   applies physical wheel clicks in both directions without an opt-in, but it
   accepts only deltas of `+1` or `-1` and rejects out-of-range steps.
6. `execute_volume` re-reads the current value immediately before the write and
   aborts if it changed after planning.
7. Volume writes are direct writes to `0x0200+n`. Register `0x0500` is never
   written: it stalls on the reference unit and is not needed.
8. Every write is read back and must match exactly.
9. No startup/reset/initialise write is performed.
10. The interrupt monitor never writes. `follow` is the explicit command that
    applies reports, with the range and delta validation above.

---

## 9. Follower semantics

`ada305 follow` tracks the selected channel host-side:

* A `10` / `20` / `40` report selects subwoofer / center / surround-left. The
  same selector again returns to master. Selector packets are debounced (0.35 s
  by default).
* Three seconds after the last volume change on a selected channel, the
  follower returns to master, matching the AMS help text and ALSERV's timer.
* Each wheel report is one attenuation step: `new = current - delta`. Only
  deltas of `+1` / `-1` are accepted; larger deltas (including the spurious
  `00 3c` idle report) are ignored. One physical detent repeats the same report
  two or three times, so wheel packets within 0.4 s are coalesced into one step
  (`--wheel-debounce`). The follower reads the current value, applies clicks in
  either direction without an opt-in, ignores a step outside the channel range,
  writes the new value directly, and verifies it.
* A debounced `01` toggles mute.
* `80` drops the selection, re-reads mute/mode, and returns to master.

Writing `0x0300` with ALSERV's selector values was accepted but did not light
the channel LEDs on the reference unit, so the follower does not write it.

---

## 10. Provenance

Static-analysis inputs, SHA-256:

| File | SHA-256 | Role |
| --- | --- | --- |
| `al60.sys` | `83c0c8a6b5e6d28e793581c3f39189d9bfdcf21f3fbc6e0bfbbb35bdbbbc40cb` | ADA305 kernel driver; control-request construction |
| AMS24 cab `WIFILE00000010` (ALSERV.exe) | `4fa880fd8b77ca4ed9aaa071e455b4c7c5603781df20f64ba34032260b399e2c` | AMS24 service; register I/O, channel mapping, report parser |
| `HUSBST.SYS` | `73d1af4971b3cb4e398aff2db95c3dd033474f12e2ac5e445652db23bc42c5b1` | legacy kernel driver |
| `MIXGHOST.exe` | `dc9d24195d1a9d4e62aff975e83c31c6aa06c20d4de09fbdc1f307724117ac49` | legacy mixer/control mapping |
| `MDSHELL.INI` | `987e40ce178434d1b6a2107cab6f45fb5fcf8550dbc956f71f1f4639cf6e20e4` | legacy model profiles |
| `ALADA.INF` | `5909fddc7aab25fdc19c3806dd5b7b35453ad30625c9725f1cdd756b3869d895` | model PIDs, ranges, modes |
| `acs495.inf` | `502f6486e1f5f7873beae359fe307fb22117c527757d5f330890b9b8c6ffd6bf` | ADA305/ADA495 range cross-check |

The original binaries are not redistributed here; the hashes let you confirm
that a copy you already have is the one this protocol was derived from.
