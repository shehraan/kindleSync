# Kobo-to-audio handoff: discovery

Initially inspected 2026-10-18. This is the historical research and acceptance
checklist. The workspace was empty at initial inspection. An implementation now
exists; see `verification.md` and the README for its current scope and evidence.

## Intended outcome

After reading on a Kobo at night, the user should have the same book available
as offline audio on their phone, positioned at their stopping passage, following
a daily morning job around 06:00. The environment timezone is America/Toronto;
using it for the job is an assumption pending confirmation.

## Evidence

- Kobo Utilities reads `ChapterIDBookmarked`, `adobe_location`, `DateLastRead`,
  title, author, content ID and percentage from Kobo's `content` table. Its
  firmware-dependent queries demonstrate that a single universal query must
  not be assumed. This establishes a local database integration candidate;
  it does not establish wireless access to this user's device.
  Source: https://github.com/janlarres/kobo-utilities/blob/main/koboutilities/features/locations.py
- KOReader's KOSync plugin sends a document digest, progress locator,
  percentage and device identity. It supports a custom sync server. Its
  close-document handler pushes online or queues progress offline. This is
  a wireless integration candidate only if the user reads in KOReader and
  the device eventually connects. It does not establish access to positions
  from Kobo's stock reading software.
  Source: https://github.com/koreader/koreader/blob/master/plugins/kosync.koplugin/main.lua
- Storyteller aligns an EPUB and one or more audiobook files into a readaloud
  EPUB. It requires matching, DRM-free inputs and supports MP4, M4A, M4B and
  MP3 audio. It cannot process DRM-locked inputs. It is a candidate alignment
  component, not yet selected or integrated.
  Sources: https://storyteller-platform.dev/docs/managing/aligning/
  and https://storyteller-platform.dev/docs/the-algorithm/

## Decisions requiring user context

1. Resolved: Android; currently Libby/Hoopla; free-only; delegated approach.
2. Resolved: Libra 2 with stock reader. User then authorized one simplest method;
   selected KOReader + sideloaded EPUB + accessible audio. Reader setup remains.
3. Pending: host available at 06:00 and actual LAN reachability.
4. Existing narration is selected; no generated speech substitution.

## Acceptance evidence required before completion

| Requirement | Required verification | Current evidence |
| --- | --- | --- |
| Read actual nightly Kobo progress | Capture a real device position after a known reading session and resolve the book/edition and locator | Missing |
| Daily morning schedule | Installed schedule with local timezone, observed run, retry and missed-run behavior | Missing |
| Same book's audio | Verify matching edition/narration and actual accessible download source | Missing |
| Accurate stopping point | Compare resolved passage with real audio playback; define measured tolerance and ambiguous-match behavior | Missing |
| Phone sync | Observe new book/position reaching the user's phone | Missing |
| Offline audio | Complete download and play at the handoff point with network disabled | Missing |
| End-to-end handoff | Real Kobo reading session followed by morning processing and phone playback | Missing |

An ebook percentage multiplied by audiobook duration does not prove a matching
passage. Synthetic tests alone cannot prove device integration or offline phone
delivery. A web player alone cannot prove unattended phone downloads. Alignment
must be prepared before the morning deadline or its processing time measured.

## Next action

The single-method implementation is in place. Complete review and actual
book/device setup. The host question is pending. Keep the full goal active
until the device acceptance evidence in `verification.md` exists.
