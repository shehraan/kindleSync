# Free Android morning handoff

The user selected Android, currently uses Libby/Hoopla, requires a free solution,
and explicitly delegated the approach, then authorized choosing a single simplest
method. Their device is a Kobo Libra 2, currently using the original reader. The
chosen automated method requires KOReader and sideloaded DRM-free EPUBs, paired
with accessible audiobook files. Protected library loans and PDF automation are
outside this explicitly simplified method. This supersedes the earlier pending
architecture-choice question. Implement inline in the empty project folder;
there is no usable Git repository to isolate or commit to.

Build a Python 3.12 local coordinator and an Android companion. No paid API,
hosted account or subscription is required. Accept matching DRM-free EPUB and
MP3/M4B/M4A files from any source. Provide optional LibriVox catalog search and
track downloads. Libby cannot export audiobooks to a separate player; do not
claim that Libby/Hoopla integration exists. Existing library-app playback stays
available separately. Do not silently replace narration with generated speech.

Register each ebook edition with its Kobo content ID and ordered audio files.
Copy and fingerprint the inputs. Transcribe locally with optional faster-whisper
on CPU, caching timestamped words against those fingerprints. Resolve Kobo
chapter/fragment locations against the exact supplied EPUB. Match contextual
words to the transcript, rejecting absent or ambiguous matches. No duration
percentage fallback. Preserve a matched passage and score rather than claiming
unmeasured exactness. Unsupported Kobo locators must produce a useful error.

Implement a KOReader plugin that exports contextual text, its crengine locator,
actual reading event time and whole-EPUB MD5, authenticating to the local service.
Retain observations offline and clear them only after successful transmission.
Validate the exact EPUB identity on receipt. Capture automatically during reading
and before suspend/close; also offer a selected-text resume marker for precision.
The marker survives suspend until the reader changes page. No wake-up of a
sleeping offline reader is assumed; progress must be sent while connected.

Keep the already implemented read-only stock SQLite collector as a diagnostic,
not as a second supported automated setup. Record reading observations
independently from listening. Do not install changes on a device automatically.

A persistent scheduler targets 06:00 America/Toronto, catches up on restart,
retries failed source reads, and publishes only changed handoff revisions.
Repeated polls never rewind listening for an already consumed revision.

The single-user HTTP service exposes health, latest handoff and immutable audio
assets with byte ranges and bearer-token pairing. Use only registered assets;
keep administrative file access in the CLI. Bind localhost by default. Android
stores connection settings, polls in a native background job, verifies/caches
audio atomically, acknowledges readiness and plays local files with background
media controls. Download the current book ahead of the morning when possible;
OS background scheduling is inexact. Cached books remain usable without a server.

Tests: Kobo schema/observation selection, actual EPUB ZIP/fragment resolution,
transcript ambiguity/mismatch/track transitions, scheduling across DST and
retries, HTTP authentication/ranges and full coordinator-to-download integration.
Compile the Android app and run available JVM tests. Real Kobo and Android
airplane-mode playback remain required before claiming the full goal complete.

The source location may identify a page rather than the user's last-read
sentence. Device precision, model-specific transport, an actual book pair and
the actual phone must be checked during setup; these are remaining acceptance
requirements, not removed scope.
