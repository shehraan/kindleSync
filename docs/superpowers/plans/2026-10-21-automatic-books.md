# Automatic Books Implementation Plan

**Goal:** Open and sync a new English EPUB without manual registration or audio setup.
**Architecture:** Durable edition imports receive exact EPUBs from KOReader;
a local Piper worker creates and transcribes narration, then reuses existing handoffs.
**Tech stack:** Python, SQLite, Piper/ONNX, ffmpeg, faster-whisper, KOReader Lua.
**Spec:** `docs/superpowers/specs/2026-10-21-automatic-books.md`.

## Constraints and review focus

256 MiB authenticated uploads; preserve paired settings and old playback; English
speech initially; no filename-only edition guesses. Test interrupted uploads,
duplicates/renames, identical filenames with different bytes, restart during
generation, and reader navigation during uploads. No usable Git repository exists.
The user delegated implementation choices; execute without further approval gates.

### Task 1: Import storage and paired API

- [x] Reproduce unknown-position rejection and add failing integration tests.
- [x] Add `Store.find_edition(md5)`, resolve `find_book(content_id, epub_md5=None)`
  by edition when supplied, and accept valid unknown KOReader observations durably.
- [x] Add `kobosync.ingest`: `progress_status(store, observation) -> dict`,
  `receive_epub(store, md5, stream, length) -> dict`, `jobs(store) -> list[dict]`,
  `process_pending(store, prepare=None)`, `loop(store, stop)`.
  `prepare(store, md5)` defaults to `narration.prepare_book` and returns a book dict.
- [x] Wire upload/progress/status API, pipeline edition lookup and CLI worker.
- [x] Verify invalid/duplicate/renamed/changed-edition cases and durable retries.

### Task 2: EPUB extraction and local narration

- [x] Add failing ordered-spine, hidden-markup, bounds and generation cache tests.
- [x] Implement `narration.read_epub(path) -> {title,author,language,chapters}`
  with bounded XML/text extraction in spine order, excluding non-linear resources.
- [x] Implement `narration.prepare_book(store, md5) -> book dict`, reading
  `data/inbox/<md5>/book.epub`, writing reusable MP3s beneath `data/generated/<md5>`,
  registering `auto-<md5>`, and calling existing cached local transcription.
- [x] Install an optional local speech extra and cache a free voice under data/models.
- [x] Verify real synthesis, decoding, full captions and passage handoff.

### Task 3: KOReader ingestion

- [x] Add failing Lua tests for unknown-book upload, errors and retry/cancellation.
- [x] Persist the local ebook path with queued observations, keep it out of API JSON,
  parse progress status and stream an unknown EPUB in the existing background worker.
- [x] Acknowledge local queues only when upload is durably accepted; show preparation
  rather than raw JSON or an inaccurate ready message. Preserve old pending records.
- [x] Verify actual Lua behavior and package the updated plugin with preserved config.

### Task 4: Integration and deployment

- [x] Run the Python/Lua suite and real generation smoke test; obtain an independent
  review of changed interfaces and failure paths, addressing material findings.
- [x] Update README/setup/status documentation and artifacts. Reload only the verified
  service; inspect device connection and deploy plugin if attached.
- [x] Verify live API acceptance without fabricating reading events in the user's store.
  Report any remaining device check or first-book preparation in progress explicitly.

## Execution record

145 project tests pass after independent review and large-upload fixes. Real
local speech, transcription, captions and HTTP downloads were checked. The
Windows task was reloaded and the final plugin was installed and hash-verified
on the Kobo, preserving pairing and reading history. Pathless Path prepared
successfully and the phone acknowledged its full download. The physical Kobo
also uploaded The Innovator's Dilemma and The Character of Physical Law without
manual registration; both are prepared. Deep Work uploaded after the final fix
and is preparing in the background. The 256 MiB limit and bounded upload memory
have regression coverage. One obsolete retry for the user-deleted book was
removed after verifying its reading observation already existed on the PC.
No implementation or deployment work remains for automatic registration.
