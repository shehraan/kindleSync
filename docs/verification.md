# Verification record

This record separates implementation checks from actual device acceptance.
Real-world acceptance and final phone deployment have passed; evidence is recorded below.

## Implementation evidence

- Latest local run: `.venv/bin/python -m unittest discover -s tests -q` — **145 tests pass**.
- `bash android/build.sh` — JVM transfer test passes, Java/Dex/resource packaging
  succeeds, and APK v2/v3 signatures verify. `aapt dump badging` confirms package
  `dev.kobosync`, minimum API 26 and target API 35.
- `uv build --wheel` produces the installable Python wheel. The Android APK and
  configured-example-only plugin ZIP are packaged under `dist/`.
- Python tests exercise actual EPUB ZIPs, SQLite snapshots, narration matching,
  edition changes, scheduler DST/catch-up/late uploads, and paired HTTP downloads.
- KOReader plugin lifecycle tests execute Lua under Lupa with stubbed reader and
  network APIs. They verify offline queue retention, success-only queue removal,
  selected-marker retention through suspend and unchanged reading timestamps.
- JVM transfer test uses a real loopback HTTP server to exercise authenticated
  partial-file resumption and corrupt-file rejection.
- Native Android sources compile against API 35 and the APK verifies using
  Android signature schemes v2 and v3.
- Real LibriVox catalog project 52 and its ordered track endpoint were read.
  Search for the exact title Letters of Two Brides returned that project.
- A 100-second excerpt of that recording was decoded and transcribed locally
  with faster-whisper 1.2.1, base.en on CPU, PyAV 16.1.0. Production transcription
  produced 243 timestamped words. An independently sourced Project Gutenberg
  passage beginning "Raise those great black eyes" matched at 40.46 seconds,
  score 1.0 over the matching window, through the production coordinator.
  This was a constructed sample observation, not a real Kobo capture; the
  timestamp has not been independently measured by a listener.
  Sources: https://librivox.org/api/feed/audiobooks/?id=52&format=json
  and https://www.gutenberg.org/cache/epub/1941/pg1941.txt
- PyAV 19 was incompatible with faster-whisper's `metadata_errors` argument.
  The optional transcription dependency is constrained below 17, and the lock
  selects the successfully exercised 16.1.0 version.
- A fresh reviewer identified four plugin/storage defects. Regressions now
  cover deferred page capture, retained overnight observations, manual-send
  marker/time preservation, and same-second capture ordering in either upload
  order. Repeated heartbeats with a new capture sequence retain the handoff
  revision so listening does not rewind.

See `implementation-ledger.md` for checks/fixes recorded during construction.

## Automatic EPUB import and speech, October 21

The user approved free generated narration for new books. New English DRM-free
EPUBs no longer require manual ebook/audio registration. The updated KOReader
plugin sends an unknown reading position, uploads the exact EPUB once, and
reports queued/preparing status while the coordinator synthesizes MP3 chapters,
transcribes actual audio, and builds the existing morning handoff and captions.

The 120-test suite includes unknown-position persistence, upload validation,
renamed editions and multiple recordings, worker retry/backoff/locking, migration,
background Lua streaming, cancellation and upload failure retention. Independent
review found three material issues; regression tests now cover recovery after
asset rename but before database commit, safe XHTML DOCTYPE declarations (without
entity expansion, including UTF-16), and uploads running longer than a minute.
Upload-capable workers allow fifteen minutes while individual socket operations
retain short inactivity timeouts. Existing playback and pairing are preserved.

A separate smoke library exercised the real Piper engine, ffmpeg MP3 encoding,
local faster-whisper recognition, passage matching, captions and HTTP downloads.
The resulting MP3 is 153,330 bytes, with 72 timestamped words and a 678-byte VTT;
the source passage matched at 0.0 seconds with score 1.0. Audio and caption HTTP
responses matched their advertised lengths and SHA256 hashes. This constructed
smoke observation was never inserted into the live reading history.

The live Windows task was verified and reloaded; backend and Windows proxy health
checks passed. The exact 3,061,200-byte Pathless Path EPUB and six original saved
KOReader observations were copied from the connected Libra 2. All six fingerprints
matched, and the updated paired API accepted their original reading times and
queued the real book. No invented reading position was used for that import.
Full-book preparation subsequently completed and the morning scheduler published
the genuine saved position at 06:33:06 Toronto time. All 19 audio tracks total
133,789,442 bytes; the actual local transcription contains 60,011 timestamped
words. All 19 HTTP caption assets passed advertised length and SHA256 checks,
totaling 504,089 bytes. The saved passage matched track index 7 at 970.44 seconds,
score 1.0. This is the aligner's result, not an independently measured physical
seek error. The phone acknowledged this exact handoff by the October 21
14:18:07 UTC checkpoint, confirming its complete verified offline download.
Private receipts and smoke output are under `data/setup/automatic-import-check/`.

The upload-capable plugin was installed on the physical Libra 2 at 14:27:04 UTC
on October 21. Destination hashes matched the tested source; the existing pairing
and saved-reading queue retained their hashes, and an exact backup was saved.
The physical device subsequently uploaded unseen EPUBs over Wi-Fi, including
The Innovator's Dilemma, The Character of Physical Law and Deep Work. The first
two completed local narration and transcription automatically. Deep Work arrived
after the final upload-limit fix and is preparing. Fresh Pathless Path reading
observations continue to arrive, and the coordinator reports no processing error.
Android APIs and APK are unchanged; no phone update is required.

### Larger EPUBs and queue reporting

The user's illustrated Harry Potter edition was indexed at 85,239,102 bytes
(81.29 MiB), exceeding the initial 64 MiB cap. The upload cap is now 256 MiB on
both clients and coordinator. Narration bounds cumulative decompressed XML to
128,000,000 bytes and does not charge unopened artwork against that budget.
A production ingest test writes and validates an 85 MiB EPUB; the Lua worker
streams an 81 MiB file in 32 KiB chunks. Oversized inputs still remain queued.

A regression also reproduces a current book's successful sync being hidden by
an older queued upload failure. The popup now preserves the current success and
identifies the other book, including actual size or a missing/empty-file reason.
All 145 current project tests passed after these fixes. The physical device has
successfully uploaded two other unseen books, The Innovator's Dilemma and The
Character of Physical Law; both are now prepared.
The Harry Potter EPUB is absent from its saved path on the currently mounted
drive, while its reading observation remains durable on the coordinator. The
user confirmed deleting or renaming it. One obsolete local retry was removed
only after its canonical observation ID was verified in the coordinator database;
the original Kobo settings were backed up and other settings verified unchanged.

Review identified disabled child garbage collection retaining distinct upload
chunks despite bounded reads. Binary transfers now run child GC. A regression
with distinct 32 KiB chunks measured 81.4 MiB retained before the fix and 1.45 MiB
extra Lua heap after the fix for an 81 MiB stream. All 145 project tests pass.
An isolated real HTTP request streamed an 85,197,525-byte EPUB successfully;
requests above 256 MiB return 413 before their body is read. The live coordinator
limit was verified and the final plugin was installed/hash-checked on the Libra 2
at 17:02:37 UTC on October 21. KOReader must restart to activate the plugin update.
Private deployment and cleanup receipts are in `data/setup/upload-size-check/`.

At the October 21 19:55 UTC follow-up, the service remained healthy and the
deleted book had no replacement EPUB or generation attempt. Deep Work's genuine
KOReader observation and successful EPUB upload after the fix are recorded in
`post-fix-status.json` in that private directory. Prepared-book asset signatures,
actual transcript words and caption manifests were checked again; first-time
preparation continues as a background job without further manual registration.

## Android runtime verification, 2026-10-19

The production APK was installed in an AOSP Android 11 / API 30 emulator. Its
installed SHA-256 was checked against the current build and the published APK
in `dist/`. The separate instrumentation APK calls the actual app and Android
media stack; it does not substitute a fake player or downloader.

All four scenarios passed. [Raw results and APK identity](android-runtime-results.json)
are retained alongside the test sources.

- Authenticated MP3/M4B downloads passed checksum verification and reached the
  server's readiness acknowledgment. A 10,000 ms target began at a reported
  10,109 ms, then media controls sought near the end and playback automatically
  continued into the second track.
- After the app process restarted, with the server stopped and Wi-Fi/mobile
  data disabled, playback resumed at 2,263 ms against the saved 2,178 ms target.
  The test confirmed the screen was off, then required a further decoded-player
  position checkpoint while the screen remained off. Repeated sync had already
  been checked not to reset the saved cursor.
- A new handoff requested 20,000 ms and playback began at a reported 20,236 ms.
- Android's actual JobScheduler invoked the production JobService to download
  a different, uncached book. Its files and server acknowledgment were verified;
  fetching it did not consume the new playback position or start playback.

These timestamps include observation/startup latency and describe generated
audio fixtures, not measured errors in the narration alignment algorithm.
The native job was explicitly forced, so the result proves that callback's
download path, not Android's choice of wall-clock execution time. This was an
emulator test, not the user's physical Android phone or Kobo.

## Physical device and morning acceptance, updated October 20

| Requirement | Current evidence |
| --- | --- |
| Real wireless collection | Physical Libra 2 sent exact-edition Peter Rabbit reading observations and the selected “But Peter” anchor over Wi-Fi |
| Matching stopping point | Real selection matched at 61.32 seconds, score 1.0; user confirmed the phone starts at that passage. No independent millisecond error measurement was made |
| Real morning schedule | Durable audit records scheduled time 06:00 and completion 06:00:21 on October 20, America/Toronto, from the actual prior-evening observation. That manifest resolves the saved viewport at 30.94 seconds; the selected “But Peter” test at 61.32 seconds is separate |
| Phone delivery | OnePlus installed, paired and preloaded audio; the morning revision was acknowledged by the 09:45:37 local checkpoint, then again at 19:14 during the caption update. Neither establishes the first-delivery timestamp |
| Offline continuation | User confirmed real phone playback with Wi-Fi off and screen locked; emulator separately verifies process restart and track transitions |
| Selected book | Complete matching Peter Rabbit EPUB/narration registered and all 1,021 narration words transcribed locally |
| Full-audio captions | OnePlus 0.3 shows changing captions from all 108 test-book cues; user requested a larger six-line passage and no blanks between cues; final 0.3.1 installed and visually verified on OnePlus: 53 words, eight visible lines, original20sp font |
| Changed PC address | Both physical clients reconnected after updates. The Kobo retained its old .213 configuration and sent new observations through discovery; OnePlus acknowledged at 19:14 local via the new PC address |

The user's expanded-caption request and final phone update are verified. The original physical handoff and offline playback have
been confirmed; earlier pending entries below are historical construction notes.

## Windows and complete-book preparation, 2026-10-19

Windows definition tests, actual native WSL launch through a path with spaces,
and real loopback listener ownership tests pass. Generic `{ok:true}` and a
different KoboSync library are rejected. Python HTTP checks verify persistent,
distinct store identities without exposing the pairing secret. The installer
was reviewed again after fixing this backend ownership issue.

The first real task launch exposed a WSL parsing quirk: quoted option flags
fell through to Bash and exited 127. A harmless native launch reproduced it;
the corrected arguments pass the native launch regression. That failed
installation removed its task and added no port proxy or firewall rule. The
setup wrapper now uses distinct attempt logs instead of a shared console
transcript that can retain a file handle or block on console selection.

After correction the actual task is Running, Limited, Interactive and has no
execution limit. The next daily recovery is 05:50 local. Backend, forwarded
localhost and Windows Wi-Fi health checks all identify the expected library;
authenticated full narration transfer through the Wi-Fi address matches its
registered SHA-256. Firewall filters were read back as TCP 8787 / LocalSubnet.
See [host details](windows-setup.md). No physical-device network check has run.

The full Peter Rabbit recording produced 1,021 timestamped words. Four EPUB
passages match at 18.72, 61.32, 89.08 and 176.60 seconds, with scores .90, 1.0,
1.0 and .95. Testing the opening found ASR's internal apostrophe in `dear's`
split into two tokens; normalizing internal apostrophes on both inputs fixes
the real regression without lowering confidence or ambiguity requirements.
The official KOReader Kobo ZIP and NickelMenu package match their GitHub
release SHA-256 digests. They are prepared locally, not installed on the Kobo.

## Physical Kobo staging, 2026-10-19

USB inspection identifies a Libra 2 on D:, firmware 4.38.23828. Its original
configuration and 10,053,632-byte reader database were backed up with matching
checksums; the database passes a read-only SQLite integrity check. KOReader,
the configured plugin and NickelMenu menu entry were copied, with all 1,049
files verified on the actual device. The Peter Rabbit EPUB matches the exact
registered edition. The indexing exclusion was added to the backed-up config;
the verified NickelMenu v0.6.0 package was staged last for installation after
ejecting. This proves file placement, not device startup or live capture.

The plugin's seven lifecycle regressions also pass under LuaJIT 2.1, matching
KOReader's Lua engine family. The actual release's `ffi/sha2.lua` computes the
same whole-EPUB MD5 as the server on the real test book. Source inspection
confirms the release's progress, xpointer text, word-end and highlight-menu APIs.
These checks still do not substitute for launching KOReader on the Libra 2.

The user subsequently confirmed KOReader opens on the Libra 2. The actual
device sent three accepted observations for `Peter Rabbit.epub` over Wi-Fi,
with valid exact-edition MD5, timestamps and crengine locators. These captures
are from Gutenberg's front matter, which the recording does not narrate;
the matcher correctly refuses to publish a guessed position. A story-text
selection has been requested before generating the first physical test handoff.
The OnePlus installation/pairing is now in progress; it is not yet verified.

The actual OnePlus 13 subsequently appeared in Windows MTP file-transfer mode.
The APK, server address and pairing-token text files were copied into Download;
copying back only those named files verified their SHA-256 checksums. The user
confirmed the app is installed and open, then confirmed "Audio preloaded"
after pairing over home Wi-Fi. Offline playback from a real Kobo marker is
still being tested.

During marker testing the user reported that tapping "Resume audio here"
showed nothing, while manual send reported "Position saved locally / connect
Wi-Fi". A regression reproduces silent offline button handling. The plugin's
network gate also called KOReader's `isOnline()`, which resolves Microsoft's
public DNS hostname even for this LAN service. Regressions now cover the actual
registered highlight-button callback, offline feedback, online sending and
local connection without public DNS. The plugin uses `isConnected()` and
shows interactive offline confirmation. All 41 tests pass, including 10 plugin
checks also run under LuaJIT 2.1. Updated ZIPs are prepared; deployment of this
fix to the physical Kobo is still pending.

## Caption and address-recovery implementation, October 20

The API adds immutable caption assets to response copies without changing the
handoff revision or playback cursor. Full narration transcripts produce WebVTT;
the Android activity follows the actual player clock and caches captions with
verified audio. JVM parser checks cover boundaries, gaps, entities and malformed
input. Playback controls now also apply a 30-second rewind on a cold start.

Address recovery sends token-free probes only to the configured private IPv4
subnet at the configured port. A fresh nonce and HMAC-SHA256 prove the paired
server before authenticated traffic. The Java implementation has fifteen standalone
network/concurrency/cancellation test groups; Lua 5.1 with real LuaSocket and
KOReader's actual SHA2 module passed changed-address recovery and proof checks.
All fourteen plugin lifecycle tests also pass under LuaJIT 2.1. Public archives
contain only source and example configuration; paired archives stay private.

A review found URL-normalization and mutable-pairing issues; both were fixed
and rereviewed. Android now retains a verified endpoint/token snapshot through
the whole download and checks for settings changes before publishing a handoff.
The earlier four-case runtime record above describes version 0.1. Final
version 0.3.1 verification is recorded below.

## Android 0.3.1 runtime verification, October 20 (Toronto)

All seven scenarios pass against the exact signed APK now in `dist/`. Both
installed APKs were verified by SHA-256 before testing. [Raw results and
APK identity](android-captions-runtime-results.json) preserve the final run.

The actual TextView was checked for at least six laid-out lines with the
original 20sp text size. Its longer passage follows player time, remains
visible inside a narration gap, stays stable when paused, and follows seeks.
Caption metadata can be added to a playing legacy handoff without rewinding.

The checks also cover complete verified MP3/M4B and caption downloads, track
transitions, offline process restart with the screen locked, a new handoff,
Android's actual forced background job, the playing book's captions while a
different book downloads, cold-start rewind, and a settings change during a
gated download. That last case retains the verified endpoint/token, preserves
the previous handoff, and publishes no readiness acknowledgment.

A scheduled job initially ran before the settings-change fixture began. The
final fixture cancels it while still offline, asserts the starting revision,
and passes the server-side no-acknowledgment check. Production APK bytes did
not change during this fixture correction.

These are emulator checks with synthetic audio. The user separately confirmed
real Peter Rabbit resume, offline locked-screen playback, changing captions
in version 0.3, and successful Kobo reconnection after its plugin update.
The final expanded-caption APK is installed on the actual OnePlus 13, with
matching SHA-256, unchanged app identity, and unchanged first-install timestamp.
After the user unlocked the phone, an app-scoped UI check and screenshot
verified 53 words across eight visible lines, at the original 20sp size. The
saved server remained configured and the app displayed offline audio ready.
The whole-track text remains visible across caption gaps in the exact installed
APK, as verified by the seven-case runtime suite. Temporary debugging
connections and UI-dump scratch files were cleaned up after verification.


## PC-assisted browser, October 26

The user explicitly chose an awake PC rendering real websites for the Kobo.
The normal browser menu and search entry points now use a private Chromium
profile through the existing paired coordinator API. Complete questions go to
ordinary DuckDuckGo/Google web search; the previous RSS feed is not used by this
UI. Images, styling, JavaScript, forms and browser history remain in Chromium.

Fresh combined verification passed **213 tests**, including 31 real managed
Chromium tests and 26 Lua client tests. Review found five reproducible defects;
regressions verify identity-bound input/password fields, legacy-page tap mapping,
large dropdowns bounded to 32 KiB metadata, cancelled-startup cleanup and rejection
of pending requests after server shutdown. HTTP caching is retained. Wide-page
horizontal panning, correct zoom and full initial search input also have coverage.

The native Linux KOReader runtime at 1264×1680/DPI 300 passed 13 end-to-end interface
scenarios with actual Chromium → authenticated HTTP → PNG → native widgets,
including image pixels, JavaScript, keyboards, password masking, dropdowns,
links, Back/Forward and scrolling. The runner verified a rendered image pixel;
its final run took 8.65 s. [Controlled native results](browser-pc-runtime-results.json)
and [live coordinator results](browser-pc-server-results.json) are retained.
Neither the desktop timing nor that pixel check establishes physical-device
latency or a universal speed improvement over the stock browser.

The running coordinator was checked with a separate controlled page, without
modifying live reading observations: real sandboxed Chromium rendered it, the
PNG matched its length/SHA256 and expected dimensions, and input was rejected
when the page stole focus. First browser open took 0.796 s. The existing Windows
proxy health endpoint also passed; no new public port or firewall rule was added.

Live ordinary search pages displayed normal human-verification challenges.
Those pages rendered as interactive sites, not dictionary/search results; no
challenge was solved or bypassed. Actual post-verification search relevance is
still an acceptance check for the user on the device. The plugin update is
packaged, but the Kobo is not mounted for USB installation at this checkpoint.
See [browser usage and limits](web-browser.md).

## Physical browser update, November 16

Build **0.9.0-kobosync3** was installed on the connected Kobo Libra 2 at
20:26:22 UTC. The guarded updater backed up the previous browser plugin and
verified SHA256 hashes for all six installed files. Reader settings, browser
settings/bookmarks and KoboSync plugin files, pairing and queued state were
unchanged. The existing Windows proxy returned HTTP 200 from its health endpoint.

The device's NickelMenu entry still launches `koreader.sh` from its main menu.
Automatic boot into KOReader is not configured; this browser update does not
change the device's startup sequence. No firmware update package was staged.

The user must safely eject/unplug the Kobo and restart KOReader to load the
update. Physical browsing, post-verification search relevance and perceived
speed remain pending user acceptance; installation hashes and desktop tests do
not establish those results.
