# KoboSync

A free, local reading-to-audiobook handoff for a Kobo Libra 2 and Android.
KOReader sends your saved EPUB passage over Wi-Fi. A local service matches it
to word timestamps from your audiobook, publishes a handoff around **6 a.m.
America/Toronto**, and the Android app downloads and plays the audio offline.

The supported automated method is **KOReader + a sideloaded English DRM-free EPUB**.
For a new book, KOReader uploads the EPUB once and the PC automatically generates
free local narration and captions. Previously imported matching audiobook files
are reused. It requires installing KOReader on the Libra 2; it
does not collect wirelessly from Kobo's original reader. Protected Libby/Hoopla
loans cannot be imported through this method. PDFs are outside this initial
single-method workflow. MP3, M4B and M4A audio files are supported for import;
actual playback depends on the audio codec supported by the phone.

No subscription, paid transcription API, cloud account or Play Store developer
account is required. New books use the free local Piper English voice. The voice
and transcription models are downloaded once; the book text is processed on the
PC. You can also import matching narration or choose a recording from LibriVox.
The program does not find commercial audiobooks or borrow titles on your behalf.

**Current verification:** 213 project tests pass, including automatic
uploads, edition matching, interrupted registration recovery and narration caching.
Local Piper has generated a playable MP3 sample. The physical Kobo has
sent real reading markers; the user confirmed that the OnePlus resumes at the
selected passage and plays offline with its screen locked. The scheduled
morning run completed at 06:00:21 on October 20 and the phone acknowledged that
handoff. The updated clients have reconnected after the PC address changed, and the
phone shows changing full-audio captions. Version 0.3.1 passes all seven Android runtime scenarios, including a
six-line caption area at the original font size, context through pauses,
seeking and offline restart. The final APK is installed and hash-verified on the OnePlus. Its actual
caption view shows eight lines at the original font size, with saved pairing
and offline-ready audio intact.
See [verification](docs/verification.md) for evidence and remaining checks.

## Web browser

The KOReader Web Browser now uses the paired PC's Chromium engine for ordinary
DuckDuckGo/Google search, images, JavaScript and forms. Complete questions are
preserved; the old RSS search is no longer used by normal browser/search entries.
The PC must stay awake and reachable on home Wi-Fi while browsing. Its browser
profile is separate from your personal Chrome profile. See the [browser guide](docs/web-browser.md)
for controls, setup, verification and limits.

## Set up the service

Use a Linux computer, Raspberry Pi or server reachable by both devices and
available overnight. Python 3.12+ is required. The service keeps the daily
schedule while it runs and catches up after restarting. A sleeping or powered
off host cannot serve downloads or receive Kobo progress.

```bash
uv sync --extra transcribe --extra speech
uv run kobosync init
```

`init` prints a random pairing token. Enter that same token in the KOReader
plugin and Android app. App data, books, transcripts and model files stay under
`./data`, excluded from version control. To put them elsewhere, use the global
option before the command: `kobosync --data /path/to/data ...`.

For automatic narration, open a supported EPUB in KOReader and send progress on
home Wi-Fi. No `add` or `transcribe` command is needed. The PC accepts the reading
position, receives the exact EPUB (up to 256 MiB), and prepares it in a background
worker while the service keeps running. First preparation of a long book can take
a substantial time; keep the PC awake. Completed chapters and transcribed tracks
are cached, and interrupted preparation resumes after a service restart. The
morning scheduler retries when a book becomes ready after its scheduled time.

To use your own narration instead, register the **same EPUB bytes** as on the Kobo.
The filename identifies a preferred recording; renamed files can reuse a matching
registered edition. List multiple audio files in playback order.

```bash
uv run kobosync add my-book \
  --content-id 'My Book.epub' \
  --epub '/path/to/My Book.epub' \
  --audio '/path/to/01.mp3' '/path/to/02.mp3' \
  --title 'My Book' --author 'Its Author'

uv run kobosync transcribe my-book --model base.en --language en
uv run kobosync serve --host 0.0.0.0
```

The first transcription downloads a free model and processes audio locally on
CPU. It may take a long time for a whole book; prepare it before the night you
want to use it. Each completed track is cached, so rerunning resumes work.
`base.en` is an English starting point. If matching fails, use `small.en` for
more accuracy, or a multilingual model such as `small` with the appropriate
`--language`. Transcript timing is estimated, not guaranteed word-perfect.

The server defaults to localhost; `--host 0.0.0.0` makes it reachable on your
home network. Set clients to the host's LAN address, for example
`http://192.168.1.100:8787`. Use the trusted home LAN for this HTTP setup; do
not expose its port directly to the internet. For private IPv4 addresses, updated clients verify the paired server and
search the same `/24` subnet at the configured port if its address changes.
No router configuration is required. A different subnet, port, hostname or
HTTPS setup requires updating the configured address. Outside that LAN, use a protected
HTTPS connection. Neither a VPN nor hosting subscription is required at home.

For Windows/WSL, the reviewed installer in `deploy/windows/` creates a limited
startup task, a loopback port proxy and a LocalSubnet-only firewall exception.
See [Windows setup](docs/windows-setup.md) for the installed configuration,
startup requirements, logs and removal.

## Install the KOReader plugin

1. Install KOReader using its [Kobo installation guide](https://github.com/koreader/koreader/wiki/Installation-on-Kobo-devices).
   This is a one-time device setup; KoboSync does not install or replace firmware.
2. Copy `koreader/kobosync.koplugin` into the Kobo's
   `.adds/koreader/plugins/` folder.
3. Copy `config.example.lua` to `config.lua` in that plugin folder and set your
   server address and pairing token. A packaged plugin ZIP is in `dist/` when
   the release artifacts have been built.
4. Open your EPUB **in KOReader**. Connect Wi-Fi and use
   **Send audio to phone** in the tools menu to verify the connection. After
   installing a plugin update, restart KOReader so it loads the new code.

The plugin records passage text and its exact EPUB fingerprint. New books upload
in a background process without blocking the reader. A queued/preparing message
means the PC is generating narration and captions; it does not mean audio is ready.
Unknown positions are saved on the PC even before their EPUB arrives, and failed
uploads stay queued on the Kobo. Existing saved positions survive the update.
The plugin sends changes
after reading pauses and before suspend/close when already online. Offline
observations are kept in `settings/kobosync.lua` and retried on resume, Wi-Fi
reconnection or manual send. It does not wake a sleeping Kobo or silently turn Wi-Fi on. Progress must
reach the server while connected before the morning handoff; a late upload with
a pre-6 a.m. reading timestamp is picked up when it arrives.

**Precision:** automatic capture knows the reader's saved viewport, not the
sentence your eyes stopped on. To mark a precise point, long-press the first
word you want to hear next and drag to select a few words, then choose
**Resume audio here**. Selecting multiple words opens KOReader's selection
menu; a single-word hold normally opens its dictionary. The plugin sends
that anchor with following context; it survives suspend until you turn the
page. Without this marker, some repeated audio from the saved page is possible.
The program refuses ambiguous text matches instead of guessing from percentage.

## Install and use Android

Version 0.3.1 shows a longer passage in a caption area at least six lines tall,
using the original font size. The previous passage stays visible between timed
cues. Captions are available throughout each transcribed track. Captions follow the
actual player position through pause, seeks and track changes, and are cached
with the audio for offline use. The text comes from local narration
transcription, so recognition and timing can contain errors. Install updates
over the existing app to preserve pairing, downloaded audio and listening position.

Android 8.0+ is required. Install `dist/kobosync.apk` (or
`android/build/kobosync.apk`) on your phone using Android's package installer.
Allow installation from the file source when Android asks. This is a locally
signed development build, not a Play Store release.

Enter the server address and pairing token and tap **Save and sync** on home
Wi-Fi. The app preloads the current book when its reading observation arrives.
It checks for handoffs with an Android job on unmetered networks, retries
interrupted downloads and verifies file lengths and SHA-256 checksums before
showing an offline handoff. Background timing is controlled by Android and is
not an exact 6 a.m. alarm; tap Save and sync to fetch immediately when needed.

Tap **Resume offline audio**. Playback has notification/lock-screen controls,
continues with the screen off and saves position locally. Repeated syncs of the
same handoff do not reset listening. A new handoff applies its reading position
when you press Resume. All tracks are downloaded for an offline handoff, so a
long download can postpone readiness. Cached files use app-private storage and
remain until app data is cleared; there is no automatic storage eviction yet.

To prove offline playback, complete a sync, turn on airplane mode, then resume
and listen past an audio track boundary. The service distinguishes publishing
a handoff from the phone acknowledging that the whole download is ready.

## Optional free narration download

```bash
uv run kobosync search-audio 'Letters of Two Brides'
uv run kobosync fetch-audio 52 ./downloads/letters
```

Review the title, language and recording edition before choosing a LibriVox
project ID. The downloader orders tracks by section number. Register those
files with the matching EPUB using `add`, then transcribe them. Free narration
will not exist for every ebook. No torrents are needed for this integration;
the file importer is independent of where you obtained your audio files.

## Scheduling, diagnostics and tests

Keep `kobosync serve` running for the 6 a.m. job; `--zone` and `--hour` change
the schedule. [The service template](deploy/kobosync.service.example) can be
adapted for a Linux user service. This PC uses the installed Windows `KoboSync`
task to keep the WSL process running; it does not use that systemd template.

```bash
uv run kobosync status
uv run kobosync books
uv run kobosync run  # process the latest observation now for a setup test
```

`status` reports the last handoff, processing error, and import jobs (awaiting EPUB,
queued, preparing, error/retry, or ready). If a new book is awaiting EPUB, open it
on the Kobo and send again on home Wi-Fi. Generated narration currently supports
English EPUBs; other languages need matching imported narration. If automatic
generation reports a missing dependency, install the `speech` and `transcribe`
extras and ensure `ffmpeg` is available. Changed EPUB bytes create a separate
edition. Changed imported audio requires a new registration and transcription.
Missing or ambiguous passages require checking
the edition or transcription; no estimate is substituted.

The original-reader database tool remains diagnostic only:
`kobosync inspect-kobo /path/to/.kobo/KoboReader.sqlite`. Its fragment resolver
does not support every stock Kobo locator. It is not the wireless setup above.

```bash
uv sync --extra dev --extra transcribe --extra speech --extra browser
uv run --extra dev python -m unittest discover -s tests -v
bash android/build.sh
```

The Android build uses a JDK 17 and Android SDK platform 35/build-tools 35.0.0,
without Gradle or external Android libraries. Set `JAVA_HOME` and
`ANDROID_SDK_ROOT` or use installations under `.tools/jdk` and
`.tools/android-sdk`. It runs a JVM transfer test, compiles Java, creates DEX,
aligns and signs the APK, and verifies its signature. Keep the generated local
debug keystore to install future builds over the same app.

For Android runtime checks, start an Android 11+ emulator and install `ffmpeg`
on the development host to generate the test audio. Then run:

```bash
bash android/build-tests.sh
uv run python tests/android_runtime.py --adb-port 5037 --serial emulator-5554
```

Use the port/serial of your emulator. The driver rejects physical devices and
clears the app's test data. It generates MP3/M4B tracks, uses the production
server and app, checks actual media-session timestamps, crosses a track boundary,
stops the server/disables network radios for an offline screen-off restart, and
checks a new handoff. It also forces Android's native download job to fetch a
new book and verifies the server acknowledgment. A forced job verifies its code
path; it does not prove Android will choose a particular wall-clock execution time.

If APKs are already installed, `--skip-install` first checks their SHA-256 hashes
against the current builds. The test APK is separate from the APK you install
for normal use and requires no extra permissions in the production app.
