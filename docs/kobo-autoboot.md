# Automatic KOReader startup on the Libra 2

This optional component opens KOReader automatically after a normal Kobo boot.
It uses the existing boot-animation entry point, waits for Nickel's hardware
setup, then runs the unmodified KOReader launcher once. KOReader's normal
**Exit** action returns to Kobo's interface and stays there until a real reboot.
Reading and automatic startup do not need the PC. Browser use still does.

Initial support is restricted to the inspected **Kobo Libra 2, firmware
4.38.23828**. Installation and each automatic launch check the model and firmware.
A different firmware version keeps ordinary Kobo startup and needs a fresh
compatibility check. This is an automatic launch after stock boot; it does not
remove the Kobo boot screen or promise a shorter power-on time.

## Build and enable

```bash
python deploy/kobo-autoboot/build.py
```

The output is `dist/kobosync-autoboot.zip`. It contains only the component's two
shell scripts, SHA256 manifest and a uniquely named NickelMenu configuration.
It contains no books, pairing credentials, firmware binaries or `KoboRoot.tgz`.

1. Connect the Kobo by USB, choose **Connect**, and extract the ZIP into the
   Kobo drive's top level. Merge its `.adds` directory with the existing one.
2. Safely eject/unplug and return to Kobo's original interface. If KOReader is
   open, use **Exit**, then open NickelMenu.
3. Choose **Enable KOReader at power-on**. It checks the device/firmware and
   installed KOReader, backs up the original animation script, adds its small
   background hook, and verifies the installed copy. It does not reboot.
4. Restart the Kobo **without a USB/power cable connected**. After Nickel starts,
   the launcher allows eight seconds for hardware setup, then starts KOReader.

The two scripts are interpreted with `/bin/sh`; FAT/Windows extraction does not
need to preserve executable permissions. The enable action copies the runtime
launcher onto the root filesystem before using it at boot.

## Return to normal startup

Use KOReader's **Exit**, then NickelMenu → **Disable KOReader at power-on**.
This disables the control file first. It restores the exact original animation
script if the current file still matches this install, checking the backup's
checksum before restoring it. If firmware or another mod changed the animation
script, disabling preserves that newer file and leaves the hook inert.

Alternatively, connect by USB and delete:

```text
.adds/kobosync-autoboot/enabled
```

To remove the optional menu entries, delete `.adds/nm/kobosync-autoboot` too.
Disable the feature before deleting its onboard directory. The original backup
and inert root-side launcher can remain in `/usr/local/kobosync-autoboot/`;
they consume no background CPU and are not used once the hook is removed.

## Recovery behavior

- A USB/power cable at boot keeps Kobo's original interface available. Charging,
  full or unknown battery status is treated conservatively; a fully charged
  device can therefore also stay in Kobo's interface until its status reports
  discharging. Reboot with a cable is the first recovery step if KOReader fails.
- Removing the `enabled` file prevents automatic launch, including when another
  mod changed the boot script and automatic restoration is inappropriate.
- The launcher gives up if Nickel or read/write onboard storage does not become
  ready within ninety seconds. Incomplete KOReader files leave Nickel running.
- An error or interruption during startup leaves
  `.adds/kobosync-autoboot/pending`. The next boot stays in Nickel. Use **Enable**
  again for an intentional retry. This includes powering off within the first
  sixty seconds; it avoids repeatedly launching a failing installation.
- The pending marker is cleared after a clean early exit or sixty seconds of
  launcher survival. The worker then exits. It does not poll throughout reading
  or keep an open file descriptor on onboard storage during its waits.

Logs are in `/tmp/kobosync-autoboot.log`, which is cleared by a real reboot.
**KOReader startup status** in NickelMenu reports the control/hook state. Startup
and restoration never edit KOReader settings, books, KoboSync pairing or queues.

## Verification

```bash
python -m unittest discover -s tests -p test_kobo_autoboot.py -v
shellcheck -s sh deploy/kobo-autoboot/manage.sh deploy/kobo-autoboot/autostart.sh
```

Tests run real shell scripts/processes against a temporary filesystem, including
original-script backup/restoration, permissions, firmware refusal, another mod's
changes, malformed payloads, corrupt backups, missing storage/Nickel, USB power,
single launch per boot, successful startup and failed-start recovery. Filesystem
and power/process fixtures replace the Kobo operating system; these checks do
not establish physical boot time, hardware readiness or USB recovery on a real
device. Installation and a physical reboot are still required for acceptance.

Local verification on November 16 passed **25 component tests**, ShellCheck and
shell syntax checks. The full working-tree project suite passed **241 tests** in
180.013 seconds. This includes the concurrent discovery work; the automatic
startup commit contains only this component's files.

The `KOBOSYNC_BOOT_ROOT` environment variable prefixes paths for local filesystem
validation. Readiness/settle/stability intervals have bounded environment
overrides for such checks; ordinary installation uses the defaults above.

The hook follows the existing [KFMon boot entry point](https://github.com/NiLuJe/KFMon/blob/master/scripts/on-animator.sh).
The launch/exit path remains [KOReader's upstream script](https://github.com/koreader/koreader/blob/master/platform/kobo/koreader.sh).
NickelMenu's [autostart request](https://github.com/pgaskin/NickelMenu/issues/124)
documents why a menu setting alone cannot enable this feature.
