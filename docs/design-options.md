# Morning Kobo-to-phone handoff: proposed approach

Status: historical proposal, superseded by the user's delegated single-method
choice and `superpowers/specs/2026-10-18-android-handoff-design.md`.

## Outcome to preserve

Read on the Kobo at night. In the morning, listen to the same book offline on
the phone, beginning at the actual stopping passage. A daily job targets 06:00
in America/Toronto, with timezone configurable. The full outcome includes real
Kobo collection, accurate narration alignment, scheduled operation, phone
delivery, and offline playback. None is implemented yet.

The phone OS, audiobook source, Kobo reader software and available overnight
host remain unknown. These affect the integration boundaries below; they are
not assumed to be settled by this proposal.

## Approaches

| Approach | Benefit | Condition or tradeoff |
| --- | --- | --- |
| Local coordinator, accessible audiobook files, native phone playback | Can own the schedule, alignment and verified offline delivery | Needs matching EPUB/audio and a reachable host; proposed baseline if files are accessible |
| Bridge to an existing audiobook platform and phone client | Could reuse library, downloads and playback | Must verify that the specific platform/client allows external position updates and unattended downloads; not established |
| Control a commercial audiobook app | Could retain the user's existing subscription workflow | Requires documented access to the specific service's downloads and playback positions; no general integration has been established |

Recommendation: use the first approach if the user's audio source permits file
access, reusing an existing local alignment engine. If the user relies on a
service, investigate its actual supported integration before choosing a route.
Do not replace the user's audiobook with generated speech without agreement.

## Proposed data flow

1. Register a book using the precise ebook edition and matching audio. Prepare
   text-to-audio alignment once, before the morning handoff. Keep content
   fingerprints so that a different edition or narration cannot silently reuse
   an old alignment.
2. Collect the Kobo's saved location and preserve the raw locator, book ID,
   source device, source observation time and receipt time. Maintain daily
   history. Choose the last actual reading event, rather than the furthest
   percentage ever reached, to allow intentional rereading.
3. Resolve the saved location into text in that edition. Map the passage to
   the aligned audio file and timestamp. Preserve the matched passage and
   alignment provenance so the result can be checked.
4. Prefetch the current book to the phone when it becomes available. At 06:00,
   publish a new handoff revision with the latest verified Kobo location. If a
   different book was read overnight, queue that book's download too.
5. The phone verifies the downloaded files, records the handoff revision, and
   offers Resume at the audio timestamp. Playback stores its own position.
   Repeated morning polls must not reset listening progress for a revision
   already consumed.

The next morning can repeat this flow without downloading an unchanged book
again. Ingestion must remain available when the Kobo is awake; the 06:00 job
cannot assume it can wake or reach a sleeping reader.

## Kobo collection choices

- Stock Kobo: read a consistent snapshot of `KoboReader.sqlite` using
  firmware-aware schema inspection and read-only access. A mounted USB device
  is a useful development path, but does not satisfy wireless automation.
  An unattended route requires an explicitly configured on-device exporter
  or another verified mechanism that can obtain the current database. No
  exporter is assumed installed, and none should be installed without first
  confirming the model and supported setup.
- KOReader: use its progress-sync interface or a small export plugin to send
  locators and book identity while online. KOReader's locator is a crengine
  xpointer, not a standard EPUB fragment. Resolve it through a verified mapping
  or export a contextual text anchor from the reader; do not treat it as an
  ordinary XPath into the original EPUB. The actual selected route depends on
  which reader the user uses.

Ordinary Kobo cloud sync has not been shown to expose a usable third-party
reading-position API. Do not implement a fictional cloud endpoint.

## Accuracy contract that still needs verification

The intended requirement remains the user's actual stopping passage. Two
independent sources of uncertainty must be measured:

1. Reading precision: the device may store a viewport/page position rather
   than the exact sentence last read. Exporting that locator accurately does
   not establish where the user's eyes stopped. Test this with known sentences
   on the actual device. If the saved state is insufficient, explain the gap
   and discuss an explicit end-of-reading marker; do not silently redefine
   success as a page or chapter handoff.
2. Alignment precision: text-to-audio mapping must be checked against the
   actual narration. Repeated passages, introductions, abridgments and edition
   differences can produce false matches. An ambiguous mapping stays in a
   needs-review state. Percentage multiplied by duration is not an accurate
   fallback.

An aligned EPUB's media overlays are a candidate source for segment timestamps.
Reusing an aligner is proposed; neither its output nor a claimed precision is
accepted without representative playback checks.

## Scheduling and delivery

Use one durable local coordinator and a persistent job store. The daily job is
keyed by local date and timezone and supports retry, catch-up after host restart,
and an explicit manual run. A singleton lock and atomic publication prevent
overlapping runs and partially published handoffs. Retain the last good result
when a source is unavailable, clearly identified by its real timestamp.

The current development kernel is WSL2. This observation does not establish
that this machine is available overnight or that any scheduler is installed.
Deployment must verify the selected host actually runs the morning job.

Use the selected phone OS's native download and media facilities. Android
WorkManager supports constrained, retryable periodic work; its execution time
is inexact. iOS background URLSession supports transfers while suspended;
the system controls timing. A 06:00 coordinator run and a completed phone
download are distinct events and must be reported separately.

Prefetching reduces morning transfer latency but does not prove that a newly
selected overnight book will be ready by a fixed deadline. Verify the actual
phone workflow and agreed timing expectations before calling delivery complete.

## Reliability and scope

- Use a single-user local service and durable state; finalize connection and
  pairing protection for the selected deployment before exposing device data.
- Only serve registered book assets. Downloads need stable file identities,
  checksums, resumable transfer and atomic completion before showing Offline.
- Distinguish no new reading, device unavailable, missing audio, alignment
  pending, ambiguous match, download pending and ready. Show a useful next step.
- Phone playback events cannot overwrite nightly Kobo source observations.
  Cross-format round-trip sync back to the Kobo is not requested.
- Do not add billing, metadata-provider subscriptions, a new reading frontend
  or automatic audiobook purchasing to achieve this handoff.

## Verification before completion

Automated tests should cover real-format Kobo database snapshots, precise
locator resolution, repeated and missing text, audio track boundaries, edition
mismatch, local-time scheduling across daylight-saving changes, retry and
handoff revision behavior. HTTP transfer and offline playback need integration
checks rather than mocks alone.

Final acceptance still requires a real reading session, an observed scheduled
morning run, correct book/position on the user's phone, and playback in airplane
mode. Record the matched passage and measured timing error. The checklist in
`discovery.md` remains authoritative for what has not yet been demonstrated.

## Evidence for platform constraints

- Kobo database fields and firmware variation:
  <https://github.com/janlarres/kobo-utilities/blob/main/koboutilities/features/locations.py>
- KOReader progress sync and xpointer source:
  <https://github.com/koreader/koreader/blob/master/plugins/kosync.koplugin/main.lua>
  and <https://github.com/koreader/koreader/blob/master/frontend/apps/reader/modules/readerrolling.lua>
- Existing alignment candidate:
  <https://storyteller-platform.dev/docs/managing/aligning/>
- EPUB format and media-overlay definition:
  <https://www.w3.org/TR/epub-33/>
- Android periodic execution constraints:
  <https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work>
- Apple background transfer behavior:
  <https://developer.apple.com/documentation/foundation/downloading-files-in-the-background>

The optional Stripe Directory lookup could not run: its CLI is absent. Its
setup documentation was read, but no provider result was obtained and no
subscription, account or infrastructure was created. No provider selection is
claimed from that lookup.
