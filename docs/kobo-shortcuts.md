# Installed Kobo quick access

Configured on the connected Libra 2 on 2026-10-21 UTC:

- Press and hold the bottom-left corner to open KOReader's native frontlight
  panel directly. This is configured for both reading and the file browser.
- The first reader menu tab starts with **Send audio to phone**, followed by
  **Frontlight**, before the normal navigation entries. Both fit on page one.

The changes use KOReader's supported `settings/reader_menu_order.lua` override
and `settings/gestures.lua` mappings. The existing `kobosync` menu ID and its
handoff behavior are unchanged; its visible label is shorter. Double-tap
recognition was not enabled, so these shortcuts add no double-tap delay to
normal reading taps. All unrelated gesture assignments were preserved.

The generated order was exercised through this KOReader release's actual
MenuSorter: the first tab's first two entries are the handoff and frontlight,
and frontlight occurs exactly once. All 33 plugin tests passed. Device copies
passed SHA-256 verification; pairing, queued positions and global settings
were unchanged. Originals were backed up under private `data/backups/`.

Safely eject, unplug and fully restart KOReader to load the settings and
plugin label. The actual gesture and on-screen layout still need user confirmation.
The private installation record is `data/setup/kobo-shortcuts-install.json`.
