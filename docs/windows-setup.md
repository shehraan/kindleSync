# Installed Windows/WSL setup

The Windows `KoboSync` task starts WSL Ubuntu-24.04 and the project launcher
`deploy/run-server.sh` under the original user's limited, interactive token.
It starts at logon and daily at 05:50 local time, requests wake, has no run time
limit, and ignores overlapping launches. No Windows password is stored. The
coordinator processes reading at **06:00 America/Toronto**.

The October 21 coordinator also runs a separate automatic EPUB preparation worker.
New English EPUBs uploaded by the updated Kobo plugin are narrated locally with
Piper and transcribed for captions. Keep the PC awake during first preparation;
completed chapter and transcription caches are reused after a restart. Inspect
`kobosync status` for import progress/errors. No new ports, scheduled tasks or
firewall rules are needed for this worker.

The backend listens on WSL port 8788. Windows port 8787 forwards to localhost
8788, which follows WSL's localhost forwarding rather than a changing VM IP.
The `KoboSync-Local` firewall rule permits inbound TCP 8787 only from
`LocalSubnet`. Global firewall policy, router configuration and WSL network
mode were not changed. The installed task is Running and all three endpoint
checks (backend, forwarded localhost, Windows Wi-Fi address) identify this
store. [Verification metadata](windows-runtime-results.json) records this check.
A complete authenticated 5,157,705-byte audiobook transfer through the
Wi-Fi address passed SHA-256 verification from WSL. Both physical devices subsequently reached the service over home Wi-Fi.

Keep Windows signed in, connected to home Wi-Fi and available overnight; a
locked session is fine. Startup requests wake but does not override the PC's
sleep/wake power policy. Current idle sleep is two hours on AC and fifteen
minutes on battery; wake timers are enabled. Keep the PC awake for the first
overnight test so it can receive reading progress before the Kobo sleeps.
This setup does not promise an exact Android download
time: Android schedules the app's unmetered background job.

On October 20, the PC server address was `http://10.0.0.49:8787`. It changed
from `10.0.0.213`; the router also returned that stale address for the host name.
Updated Android 0.3 and Kobo plugin clients verify the paired server and search
its previous private `/24` subnet at the same port. Router access is unnecessary.
Both updated physical clients have since reconnected. The Kobo retained its
original .213 setting and sent a new observation; the OnePlus synced and
showed changing captions. The final0.3.1 caption-layout update is installed and visually checked on the OnePlus.
Pairing details
are private in `data/setup/phone-pairing.json`; do not share that file or the
configured plugin ZIP. Installation metadata, scripts and attempt logs are in
`%LOCALAPPDATA%\KoboSync`; service output is in `data/server.log`. Initial
"No reading progress received" messages mean the service is waiting for the
first actual Kobo observation.

As of November 16, the Wi-Fi address is `http://192.168.68.55:8787`. The Kobo's
saved address and shared discovery module were updated after a network change
made both audio handoff and browsing search the old `10.0.0.x` subnet. The Kobo
now checks its current private `/24` as well as the saved one. Android 0.3.1
needs its server address updated manually when moving to a different subnet.
See [connection diagnosis and verification](network-troubleshooting.md).

Prepared under `data/setup/`:

- `kobosync.apk` for the OnePlus 13.
- `kobo/Books/Peter Rabbit.epub`, identical to the registered server edition.
- `kobo/.adds/koreader/`, the checksum-verified official v2026.07.1 runtime with
  the configured KoboSync plugin.
- `kobosync-configured-plugin.zip`, for an already-installed KOReader.
- `peter-rabbit-alignment.json`, four matches against the full narration.

The actual Libra 2 was inspected on USB as D:, firmware 4.38.23828, with no
existing KOReader or NickelMenu. Its configuration and 10 MB reader database
were backed up with verified checksums; the database also passes SQLite's
integrity check. All 1,049 runtime/plugin/menu files and the registered EPUB
were copied and verified on the device. The indexing exclusion was added to
the backed-up configuration, and the verified NickelMenu package was staged
last as `.kobo/KoboRoot.tgz`. The user confirmed KOReader opens, the physical Kobo sent edition-verified
reading updates over Wi-Fi, and the OnePlus resumes the selected narrated
passage offline with the screen locked. The real morning coordinator ran at
06:00:21 local on October 20; the phone had acknowledged that
revision by the 09:45:37 local checkpoint and synced again at 19:14 during
the caption update. These checkpoints do not establish the first delivery time. The private record is `data/setup/kobo-device-install.json`.

The official
[Kobo installation guide](https://github.com/koreader/koreader/wiki/Installation-on-Kobo-devices)
describes the installation options. NickelMenu's current
[source documentation](https://github.com/pgaskin/NickelMenu) excludes firmware
5.x; this connected device runs supported firmware 4.x.

The complete test pair is Gutenberg's
[The Tale of Peter Rabbit](https://www.gutenberg.org/ebooks/14838) and the first
recording in the matching
[LibriVox collection](https://librivox.org/the-tale-of-peter-rabbit-and-others-by-beatrix-potter/).
All 1,021 transcript words were produced locally from the full 5m22s track.
Independent EPUB passages match at 18.72, 61.32, 89.08 and 176.60 seconds.
These are matcher outputs, not measured physical-device seek accuracy. No
constructed reading observation or handoff was inserted in the live library.

To remove this host setup, run the staged `Uninstall-Windows.ps1` with
administrator approval and its `config.json`. It removes only the matching
KoboSync task/proxy/firewall rule, preserving books and data. Installation
verifies a persistent store identity before opening the LAN port and rolls
back newly created rules/task if setup fails.
