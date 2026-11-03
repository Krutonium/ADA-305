# Altec Lansing models and profiles

The AMS24 Windows stack supports several Altec USB speaker models. Only the
**ADA305** is implemented and tested here. This page records what the driver
files say about the others so they can be added safely later; it is not an
invitation to write to them with this tool.

> Numeric ranges below are **attenuation in dB**. A larger value is quieter.
> The INF files call the value at minimum volume `Min` and the value at maximum
> volume `Max`, which is inverted relative to attenuation. Both are shown
> explicitly to avoid that trap.

---

## 1. Models recognised by `ALADA.INF`

`ALADA.INF` (Win98/Osr2) maps these USB IDs:

| Model | USB ID | Master attenuation | Channel attenuation | Modes |
| --- | --- | --- | --- | --- |
| ADA310 | `04d2:ff61` | `0..54` (INF `Min=0x36`, `Max=0x00`) | `0..32` (INF `Min=0x20`, `Max=0x00`) | `0,1,2,3` |
| ADA600 | `04d2:ff60` | `0..54` (`0x36` / `0x00`) | `0..32` (`0x20` / `0x00`) | `0,1,2,3` |
| ADA305 | `04d2:0305` | `25..96` (`0x19` / `0x60`) | `0..31` (`0x00` / `0x1F`) | `0,1,3` |
| ADA495 | `04d2:ff05` | `25..96` (shares ADA305 AddReg) | `0..31` | `0,1,3` |
| ADA315 / ADA355 | `04d2:ff33` | `0..54` (`0x36` / `0x00`) | `0..32` (`0x20` / `0x00`) | `0,1,2,3` |

Channels are the same logical order (master, left, right, center, sub,
surround-left, surround-right) and use registers `0x0200 + index`.

`acs495.inf` only lists `04d2:ff05` but writes the `ADA305` registry key and the
same ranges, which is why ADA495 is treated as protocol-compatible with the
ADA305. It has not been tested on hardware.

### Model-specific behaviour in ALSERV

Recovered from `ALSERV.exe`:

* Product IDs reported as `0xff61`, `0xff60`, `0xff05`, `0xff33` are normalised
  by masking `0xff00`. Product `0x0005` is normalised to `0x0305`.
* **ADA305 and ADA495** (`0x305` / `0x5`): logical channel 6 is read and written
  through channel 5, so surround-right is not independent. The mode-cycle
  interrupt flag is ignored.
* **ADA600** (`0x60`): performs an extra model-specific initialisation call
  (`FUN_00403ee0(1)`) that the other models do not. Its `0x500` setup register
  must not be guessed.
* **ADA315/355** (`0x33`): ALSERV uses register base `0x406` instead of the
  usual `product + 0x100`, and the legacy profile describes a separate interrupt
  pipe (`PIPE01`). Do not assume the ADA305 register addressing applies.
* Other models with a non-ADA305 product ID accept the `0x08` unmute flag and
  the `0x10/0x20/0x40` input-selection flags; ADA305/ADA495 ignore those.

---

## 2. Legacy `MDSHELL.INI` profiles

Older `HUSBST.SYS` / `MIXGHOST.exe` builds select a profile by name. The values
below are copied verbatim from `MDSHELL.INI`; `MasterMaxDb`/`MasterMinDb` are
the attenuation at the quietest/loudest ends.

| Profile | Report size | Master attenuation | Channel attenuation | Notes |
| --- | --- | --- | --- | --- |
| `USB46` | 2 | `0..62` | sub `0..31` | interrupt enabled |
| `USB47` | 3 | `0..31` | sub `0..31` | no `Interrupt=` entry |
| `USB305` | 2 | `25..96` | `0..31` (all six) | matches ADA305 |
| `USB400` | 2 | `24..80` | `0..31` (all six) | |
| `USB503` | 6 | `0..54` | `0..32` (all six) | |
| `USB5033` | 6 | `0..54` | `0..32` (all six) | remote control `KIE`, stream + interrupt pipes |
| `ACS600` | 6 | `0..54` | `0..32` (all six) | |

These profiles do not carry USB PIDs in the INI, and the mapping from a profile
name to a specific retail model is not always explicit. Do not infer a PID from
a matching range alone.

Channel names in the legacy files are `FrontLeft`, `FrontRight`, `Center`,
`SubWoffer`, `SurroundLeft`, `SurroundRight`, plus `Master`.

---

## 3. Interrupt report sizes

ALSERV always reads 7 bytes. The per-model meaningful size comes from the
profiles:

* ADA305: `ReportSize=2` (USB305), endpoint `wMaxPacketSize=2`.
* Legacy `USB46`/`USB400`: 2 bytes.
* Legacy `USB47`: 3 bytes.
* Legacy `USB503`/`USB5033`/`ACS600`: 6 bytes.
* ADA310/600/315 report size was not independently confirmed from the INF
  files; the same 7-byte ALSERV path is used.

The parser accepts any report of at least 2 bytes and ignores the trailing
model-specific bytes.

---

## 4. What this project implements

| Model | Status |
| --- | --- |
| ADA305 (`0305`) | **Implemented, tested on hardware (read-only + no-op write)** |
| ADA495 (`ff05`) | Protocol-compatible on paper; not enumerated by the CLI and not tested |
| ADA310 (`ff61`) | Documented only |
| ADA600 (`ff60`) | Documented only; has an extra init write |
| ADA315/355 (`ff33`) | Documented only; different register base and pipe |

Adding a model should mean adding a profile object (PID, ranges, channel map,
mode set, report size, init requirements) and then validating it with **read-only**
requests first. The safety policy and confirmation layers are model-independent
and should not be bypassed.
