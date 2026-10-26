# KoboSync reader performance changes

This update reduces KoboSync work during page changes and scrolling. It does
not change KOReader's renderer, font sizes, screen refresh settings, firmware,
Android app or server. No dependencies or configuration fields were added.

- Passage capture waits for two seconds without another page-update event.
  The observation retains the original reading-event timestamp, including when
  the delayed capture crosses the morning cutoff.
- Ordinary captures share a checkpoint write approximately every 30 seconds.
  Checkpoints wait while scrolling is active. Explicit resume markers, suspend
  and document close save immediately.
- Offline records retain the latest observation for each book and UTC hour,
  plus separate exact-hour observations. This preserves the configured 06:00
  America/Toronto cutoff in both standard and daylight time. Older days and
  different books remain available; redundant pages within a bucket are replaced.
  Non-hour-aligned schedules would need different compaction boundaries.
- EPUB fingerprints use incremental MD5 with 32 KiB reads. The digest is cached
  for the open book, recomputed for a different file, and not reused across a
  new reader session based on potentially stale file metadata.
- Server discovery and HTTP run in one low-priority subprocess at a time using
  KOReader's existing process helper. Each batch contains at most 32 records;
  pipe output stays below 3.5 KiB. The parent polls without waiting and stops a
  hung worker after about 20 seconds. Child garbage collection is disabled to
  reduce writes to the reader's copy-on-write heap.
- Only acknowledged observation IDs are removed. A newer capture made during
  sync stays queued. Closing the document cancels/reaps its worker and releases
  references to the old reader; callbacks cannot overwrite a later reader's
  settings. An incomplete sync remains queued for the next active-reader retry.

Like other batched saves, ordinary progress since the last checkpoint can be
lost on an abrupt process crash. Normal suspend, close and explicit markers
force a save. Wait for the sent confirmation before closing immediately after
an explicit handoff if delivery must finish before the next reader session.

## Verification

The plugin suite has 33 tests. These include real streaming MD5 against
KOReader's SHA2 implementation, an actual fork/pipe test using its process
helper with a deliberately slow HTTP callback, worker timeout/cleanup,
concurrent captures, compaction, timestamp preservation and original handoff
regressions. The native checks use the locally prepared KOReader runtime;
without that optional runtime they are skipped.

[Operation counts](koreader-performance-results.json) compare the original and
optimized code with the same 1,000-event scrolling burst. The original did
65,000 word lookups and 1,000 forced saves. The optimized code did neither
while scrolling, followed by 65 lookups and one checkpoint save after settling.
These are simulated reader/storage operation counts, not measured Kobo
latency or RAM use. Physical-device responsiveness still needs checking.

## Update package

`dist/kobosync-koreader-performance.zip` contains the standard plugin files
with the optimized `main.lua` and an example configuration only. Existing
`config.lua`, pairing details and queued reading state must be preserved.
The worker is included in `main.lua`, so no additional runtime file can be
missed by an existing plugin updater. The optimized plugin was installed on the physical Libra 2 at
`2026-10-21T01:48:37.7813947Z`. On-device hashes matched the verified update; the
old plugin was backed up and pairing/queued reading state were unchanged.
KOReader restart and the user's responsiveness check remain pending.
