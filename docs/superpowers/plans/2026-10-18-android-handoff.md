# Android Handoff Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, task by task.

**Goal:** Deliver the free local Kobo-to-audio coordinator and Android companion.
**Architecture:** Fingerprinted book inputs, cached local transcription, precise
passage matching, durable daily scheduling and versioned phone handoffs.
**Tech Stack:** Python 3.12, SQLite, optional faster-whisper; Android Java, native
JobScheduler, MediaPlayer/MediaSession, no hosted infrastructure.
**Spec:** ../specs/2026-10-18-android-handoff-design.md

## Global Constraints

- No paid API, hosted account or subscription is required.
- No duration percentage fallback.
- Bind localhost by default.
- Do not claim Libby/Hoopla integration or unverified exactness.
- Work in the supplied empty folder; its managed .git is not a usable repository.

## Review Focus

- Unknown Kobo schema/locator: diagnose, never fabricate a passage.
- Duplicate passages: reject ambiguity rather than skip narration.
- Stale inputs: reject transcripts from another audio/ebook edition.
- Interrupted downloads and repeated polls: preserve local playback progress.
- Restart/timezone transitions: catch up once, allow retry without losing state.

### Task 1: Book positions and matching

Files: `kobosync/kobo.py`, `epub.py`, `alignment.py`, `tests/test_core.py`.
Interfaces: `read_latest(path) -> dict`, `passage(epub, locator) -> str`,
`match_passage(text, words) -> dict` (track, seconds, score, passage).

- [ ] Write tests with a real-format SQLite fixture and EPUB ZIP, exact
  fragment, missing fragment, multiple tracks and duplicate narration.
- [ ] Run `python -m unittest discover -s tests -v`; observe missing behavior.
- [ ] Implement read-only snapshot, fragment resolution and contextual matching.
- [ ] Run the full suite; require all tests pass.

### Task 2: Durable coordinator, acquisition and scheduling

Files: `store.py`, `pipeline.py`, `audio.py`, `schedule.py`, `cli.py`,
`tests/test_pipeline.py`; consumes Task 1 interfaces.
Produces book registration, transcription, handoff and date-keyed scheduling.

- [ ] Test fingerprint mismatch, failed source retry, unchanged revisions, DST
  and a complete registered-book/database-to-handoff workflow.
- [ ] Observe failures; implement managed storage, CLI, transcription caching,
  free catalog fetch and scheduler. Prefer one reusable lock for publication.
- [ ] Run full suite and CLI smoke checks.

### Task 3: Phone transfer API

Files: `server.py`, `tests/test_server.py`; consumes Store and handoff JSON.
Produces `/health`, `/v1/handoff`, `/v1/audio/{book}/{track}`, `/v1/ready`.

- [ ] Test loopback HTTP auth, complete and ranged downloads, traversal rejection
  and actual downloaded-byte checksum versus the published manifest.
- [ ] Observe failures; implement the server with registered assets only.
- [ ] Run all tests, including the socket-level integration workflow.

### Task 4: Android companion

Files: `android/` app, build scripts and Java tests.
Consumes the exact Task 3 manifest schema, including revision and track hashes.

- [ ] Write JVM tests for revision consumption and file checksum verification.
- [ ] Implement settings, native retryable jobs, cached verified downloads,
  readiness acknowledgment, offline queue playback and background media controls.
- [ ] Build APK and run tests if SDK/JDK installation is possible; otherwise
  preserve the actual build error and continue unaffected coordinator work.

### Task 5: Runbook and review

- [ ] Document runnable setup, real book preparation, Kobo transport, Android
  installation, limitations and real-device acceptance in README.
- [ ] Review the whole implementation with a fresh reviewer, fix substantive
  findings with reproductions, then rerun affected checks.
- [ ] Keep the goal active until actual device-to-phone acceptance is verified.
