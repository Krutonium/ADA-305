# Original Altec Lansing drivers (archival copy)

This directory preserves the original Windows driver packages used to recover
the ADA305 protocol. They are kept for interoperability, repair, and historical
reference. The copyright and any licence terms remain with Altec Lansing and
the original authors; nothing here is covered by the MIT licence that applies
to this project's own source code.

## Contents of `ada305-original-drivers.tar.gz`

```text
AMS24XP.exe                    AMS24 / Audio Management Software installer
AnotherCopyFromFloppies/       Original two-disk ADA305 driver distribution
  floppy1.img                  1.44 MB floppy image, disk 1
  floppy2.img                  1.44 MB floppy image, disk 2
  app/                         extracted application files
  drivers/                     extracted drivers, INF files, and MDSHELL.INI
  him/                         extracted installer/help files
  ...                          other extracted files from the floppy set
```

The floppy set includes `HUSBST.SYS`, `MIXGHOST.exe`, `MDSHELL.INI`, the
`USB*.INF` files, and the original help/readme text. `AMS24XP.exe` is the
self-extracting AMS24 package; its internal `ALSERV.exe` and other cab files
are left inside the installer rather than duplicated here.

## Hashes

Archive:

```text
aa9d701eaa7db97751e5c8fc9d8ecb8af0a0281b96d6c981b68a051036a86595  ada305-original-drivers.tar.gz
```

Source files at the time the archive was created:

```text
b0ba9a24b78260915a2612da5e9cd69d11a0d58e0e40d104eef5e1b1b14d7505  AMS24XP.exe
b2d490559fc2d808996fc4004428f803f2abb30f45996d1febdfcccc8b4a42c3  AnotherCopyFromFloppies/floppy1.img
b2e6c27329570f90c1d4961f48a76df3cf6ee06675cddb2a065a2d3beb840ee8  AnotherCopyFromFloppies/floppy2.img
f86e017a22e5ef2e8a0853ec937d595e1a1c547befcbeaa6e2257e0586da7387  AnotherCopyFromFloppies/drivers.zip
```

The driver binaries themselves are not needed to build or run this project.
They are included only so the reverse-engineered protocol can be checked against
the original material.
