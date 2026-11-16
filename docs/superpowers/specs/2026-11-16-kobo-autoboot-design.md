# KOReader automatic startup

The user requested automatic KOReader startup in a separate commit. Keep this
change independent of the existing browser, audio pipeline and paired state.

Use the Kobo firmware's existing `/etc/init.d/on-animator.sh` entry point to
start one bounded background launcher. The foreground animation script keeps
its original name and content, with a small marked insertion after its shebang.
Never replace `nickel`, `rcS`, libraries or firmware binaries. Preserve the exact
original animation script and refuse to overwrite later changes from firmware
or another mod. Initial support is the inspected Libra 2 (model suffix 0388),
firmware 4.38.23828; other versions must fail without changing their startup.

## Approaches

NickelMenu provides manual launch but no supported autostart action. A custom
NickelHook library introduces a compiler/Qt dependency for a shell-only task.
Replacing the main firmware startup chain would require reproducing hardware
initialization. The existing boot-animation hook is the implementation choice:
it is also used by KFMon, preserves Kobo's hardware setup, and needs no binary.
This means normal Kobo boot runs first, then KOReader launches automatically;
instant boot and an absent Kobo splash/home screen are not promised.

## Behavior and recovery

Install through an explicit NickelMenu enable action. Keep a positive enabled
file in `.adds/kobosync-autoboot/`, an exact animation-script backup on the root
filesystem, and the background launcher there too. Disable restores the original
only if the installed script is still ours; otherwise leave others' changes
intact and turn off the enabled file. No automatic reboot during installation.

Launch once per Linux boot, after onboard storage is mounted read/write and
Nickel has had eight seconds to settle, with a ninety-second readiness bound.
Skip when external power/USB is detected, the enabled file is absent, the exact
device/firmware check fails, or KOReader files are incomplete. Exit uses the
unmodified upstream `koreader.sh`, which returns to Nickel. A persistent pending
marker pauses future automatic launches if power is lost during startup or the
launcher fails. Clear it after a clean early exit or sixty seconds of survival;
the enable action also clears it to allow an intentional retry.

The worker ends after startup monitoring. It must not poll throughout reading,
retain an open descriptor on onboard storage, change reader settings or contact
the PC. USB-cable boot and deleting the enabled file provide recovery.

## Delivery and validation

Ship source shell scripts, a uniquely named NickelMenu drop-in and a standard
library ZIP builder. Test real shell processes against a temporary root tree,
including loss of readiness, USB recovery, crash guard, repeated startup and
restoration/conflict handling. Run shell syntax/lint checks and the project
suite. Commit only these new files. Physical installation and a real reboot
remain separate acceptance steps; this side-chat request is for the commit.

References: [NickelMenu autostart issue](https://github.com/pgaskin/NickelMenu/issues/124),
[KFMon boot hook](https://github.com/NiLuJe/KFMon/blob/master/scripts/on-animator.sh),
[KOReader launch/exit behavior](https://github.com/koreader/koreader/blob/master/platform/kobo/koreader.sh).
