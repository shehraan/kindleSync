# KOReader Automatic Startup Implementation Plan

> Execute locally in this side conversation; subagents and worktrees are unavailable.

**Goal:** Automatically launch KOReader on the inspected Libra 2, in one separate commit.

**Architecture:** Explicit NickelMenu installation adds a bounded background hook
to the existing animator script. Root-side backups and user-visible flags support
disable/recovery without changing browser or reading configuration.

**Tech Stack:** POSIX shell, Python standard-library ZIP packaging/unittest.

**Spec:** `docs/superpowers/specs/2026-11-16-kobo-autoboot-design.md`.

## Global constraints

- Libra 2 model suffix 0388 and firmware 4.38.23828 only.
- No browser/audio edits, firmware binaries, new daemon, PC dependency or automatic reboot.
- Preserve the exact original animation script and avoid overwriting others' changes.

## Review focus

- Disabled or broken KOReader must leave Nickel available.
- A cable at boot must prevent automatic launch, including a full battery.
- A second Nickel startup in the same Linux boot must not relaunch KOReader.
- Interrupted/failed startup must stop future automatic retries.
- Install/disable must preserve permissions and intervening firmware/mod changes.

## Task 1: Install, boot and package

**Files:** Create `deploy/kobo-autoboot/{manage.sh,autostart.sh,nickelmenu,build.py}`,
`tests/test_kobo_autoboot.py`, `docs/kobo-autoboot.md`.

**Interfaces:** `manage.sh enable|disable|status`; `autostart.sh` takes no arguments;
`build.py --output PATH` emits an archive with only this component's files.
`KOBOSYNC_BOOT_ROOT` prefixes filesystem paths for local validation without a Kobo.

- [x] Write and run behavioral shell/ZIP tests; verify failure while the feature is absent.
- [x] Implement guarded installation, bounded startup and the minimal ZIP builder.
- [x] Verify tests, shell syntax and ShellCheck; inspect the package's complete file list.
- [x] Run the project suite and document measured validation and physical acceptance limits.
- [x] Inspect the final diff and include only this feature in its separate commit.

## Execution record

The first run failed while the scripts and package builder were absent. Two
additional regressions then reproduced restoration of a corrupt backup and
acceptance of a malformed payload; checksum and syntax guards fixed both.
All 25 component checks pass. ShellCheck and shell syntax checks pass. The full
working-tree suite passed 241 tests in 180.013 seconds, including separate
discovery changes present in the shared workspace; those changes are excluded
from this commit. The ZIP contains exactly the four documented component files.

Side-chat constraints require inline execution, no worktree, no subagents, and
no continuation of the main browser task. Only new feature-specific paths are
included. Review was performed locally; physical installation, cable recovery,
return from KOReader and reboot timing remain unverified on hardware.
