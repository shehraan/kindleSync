# Implementation ledger

- User clarified Android, Libby/Hoopla, free-only; delegated approach selection.
- Earlier design-choice blocker is resolved. No further approval is needed for
  reversible local implementation; user's delegation overrides skill handoffs.
- Inline execution selected for the tightly coupled initial implementation.
- Workspace is empty except for docs and venv; `git status` confirms no usable
  repository. Work in the user-supplied folder without touching managed .git.
- Native Android Java APIs selected to reduce toolchain and dependency burden.
- Remaining real-world evidence: model/reader, current book pair, overnight host,
  device location precision, installed Android app and offline playback.
- User supplied Libra 2 + stock reader, then authorized one simplest method.
  Selected KOReader + sideloaded EPUB + accessible audio, explaining the required
  reader change and exclusion of protected library loans/PDFs in commentary.
- Core, pipeline and socket-level integration tests pass (28 tests at this point).
- Lua lifecycle tests found a selected marker overwritten on suspend; reproduced
  and fixed. Lua executes under Lupa in tests, not yet on the actual e-reader.
- Native Java transfer test verifies authenticated resumable download and rejects
  corrupt audio before publishing final cache files.
- Android APK compiled and v2/v3 signatures verified. No physical phone test yet.
- SDK setup initially failed because its default cache was outside the writable
  workspace. ANDROID_USER_HOME inside .tools fixed it; no escalation required.
- Fresh whole-project review completed by review_handoff. All four substantive
  findings reproduced with failing tests and fixed: delayed page capture,
  persistent multi-observation offline queue, preserved manual marker/time,
  deterministic same-second capture ordering. No deferred minor findings.
- Follow-up regressions verify sequence-only heartbeats keep the same revision
  and a failed host costs one timeout rather than one timeout per queued page.
- Actual public-domain audio/text sample: production transcription (243 words)
  and passage match succeeded at 40.46 seconds. Not actual device acceptance.
- Direct model fetch initially hit Xet's default read-only cache; caches now
  live with managed data. Real audio decode then exposed PyAV 19 incompatibility;
  locked PyAV 16.1.0 passes the exercised production transcription.
- Final: 36 Python/Lua tests pass. Native Java HTTP transfer test passes. Android
  APK compiles, packages resources and verifies v2/v3 signatures. Python wheel
  and plugin ZIP packaged in dist. No service/device changes were installed.
- Remaining: answer overnight-host question, choose user's real ebook/audio
  pair, install KOReader/plugin/APK and perform actual morning/offline acceptance.
- 2026-10-19: Built a separate Android instrumentation APK and emulator-only
  controller. Installed app/build hashes match. All four runtime scenarios pass
  on AOSP Android 11/API 30: verified MP3/M4B transfer, actual seek positions,
  track transition, notification media controls, repeat-sync cursor retention,
  process restart offline with screen-off advancement, new handoff and native
  JobScheduler download of an uncached book. Raw report in android-runtime-results.json.
- Runtime setup/test harness corrections: isolated emulator/ADB ports, avoid a
  timed-out redundant streamed install by verifying installed hashes, inspect
  the app's own notification media token without global media permission, quote
  ADB remote-shell arguments, and restore test radios in cleanup. No production
  app changes were needed for these checks.
- Scope of evidence: forced native job is not a timed overnight run; generated
  audio fixture is not a real Kobo observation or narration accuracy measurement.
  At that point physical setup and the unanswered overnight-host choice prevented completion.
- User chose OnePlus 13, authorized Windows/WSL overnight operation and any test
  book, confirmed neither device was connected, and authorized an administrator
  prompt. The earlier host-choice blocker is resolved.
- Registered the complete Peter Rabbit EPUB and 5m22s LibriVox recording;
  production local transcription produced 1,021 words. Four actual EPUB passages
  align, including an opening regression fixed by internal apostrophe normalization.
  No artificial observation/handoff was added to live data. Latest Python/Lua
  suite: 38 tests pass.
- Windows installer reviewed, then revised to require a persistent backend
  instance identity before creating LAN rules and roll back new resources on
  failure. Real Windows loopback conflict tests and task-definition checks pass.
- Actual launch revealed quoted WSL flags exit 127; reproduced natively and
  corrected. A native launch test now covers the action and spaces in its path.
  First failed setup rolled back its task and created no network rules. Separate
  per-attempt file logs avoid console/transcript blocking during elevation.
- Windows setup succeeded: limited interactive-user task Running, daily 05:50
  recovery + logon, coordinator 06:00 Toronto, backend 8788, portproxy 8787 and
  TCP/LocalSubnet rule. Read-back verifies all settings; backend/forwarded/LAN
  endpoints identify the expected store; full authenticated audio transfer
  through the LAN address passes its checksum.
- Private configured plugin, exact EPUB, APK and full official KOReader runtime
  prepared under data/setup. Official KOReader/NickelMenu release hashes verify;
  Kobo firmware inspection is required before choosing/installing the launcher.
- Remaining: actual Kobo/OnePlus installation and wireless progress capture,
  measured stopping-point playback and a real overnight run. USB connection
  requested asynchronously after host setup; no device is claimed tested.
- User paused the automatic goal, then connected the Kobo and requested a USB
  check. Detected Libra 2 on D:, firmware 4.38.23828, no existing KOReader or
  NickelMenu and no pending update. Backed up config/database with checksums;
  read-only database integrity check passes.
- Copied and verified all 1,049 reader/plugin/menu files plus the exact EPUB;
  applied the backed-up indexing exclusion and staged the verified NickelMenu
  package last. Private device install record preserves boot/capture as false.
  Eject/unplug and actual reader launch are the next external actions.
- Seven plugin regressions pass under LuaJIT 2.1; the release's actual SHA2
  module produces the server's whole-EPUB MD5. Confirmed the actual release's
  reader/highlight APIs. Clarified multiword selection in the precision instructions.
- User confirmed KOReader works and requested finishing phone pairing and the
  overnight handoff. Server now contains three real, edition-verified wireless
  observations from the Libra 2. All are Gutenberg front matter, and alignment
  correctly rejects them; asked the user to select inside the narrated story.
- Staged the 41,433-byte verified APK and private server/token text files in the
  Windows profile for MTP transfer. Asked the user to connect/unlock OnePlus and
  choose File transfer, with Wi-Fi installation offered as an alternative.
- OnePlus 13 appeared via MTP. Copied the APK, server address and token text
  files into its Download folder, read back only those three named files and
  verified every checksum. User confirmed the Android app is installed/open.
  Pairing instructions sent; waiting for the reported sync status and an actual
  story-text Kobo marker. No narration handoff has been fabricated from front matter.
- User confirmed "Audio preloaded" after pairing the OnePlus over home Wi-Fi.
  Physical Android app download path is now exercised; offline resume from an
  actual narrative marker remains pending.
- User reported silent Resume-audio button. Manual send reports offline/local
  save. Reproduced the silent offline callback with a failing test; source shows
  isOnline() depends on Microsoft's public DNS. Added real callback/LAN-only
  regressions, changed the gate to isConnected(), and added offline confirmation.
  All 41 tests and 10 LuaJIT plugin tests pass. Public/private plugin ZIPs and
  private source staging are updated; the physical Kobo still has the old file.
  Asked user to enable KOReader Wi-Fi and retry manual send before continuing.

- 2026-10-20: User confirmed the real “But Peter” handoff and offline/locked-screen
  OnePlus playback, then requested captions throughout the full narration.
  The actual daily run completed at 06:00:21 local and the matching phone
  readiness row's latest acknowledgment is 09:45:37 local (not first delivery).
- Added immutable whole-track WebVTT and Android player-clock captions with
  offline caching. Same revision retains listening position. Parser tests and
  HTTP integrity/range checks pass; physical caption update pending.
- Windows Wi-Fi DHCP changed 10.0.0.213 to10.0.0.49; router name resolution
  remained stale and user cannot access router settings. Added token-free,
  nonce/HMAC-verified discovery within the prior private /24 at the same port.
  Both actual production helpers recovered the real paired service at.49.
  Fourteen Lua plugin tests pass in LuaJIT2.1; full Python suite60 passes;
  Java discovery10 groups and captions8 groups pass.
- Review caught trailing-slash/default-port Lua discovery bypass and Android
  mutable pairing reads during sync. Both fixed and rereviewed. Added a gated
  native-download regression for concurrent pairing edits.
- Existing Windows task once returned CTRL_C_EXIT; restored only the verified
  task and confirmed independent health/discovery over2m26s. No task/firewall/
  power changes. Termination provenance is unknown; task history is disabled.
- First final Android test attempt never installed the app: disposable emulator
  system services crashed (“Lost network stack”, later native incremental-service
  SIGSEGV). Reset only emulator data, started with one software CPU, and changed
  test APK installation to --no-incremental. Final native checks in progress.

- Final 0.3.1 APK: all seven native Android scenarios pass, with installed
  hash checks and a server-side assertion that changed pairing cannot publish
  readiness. Corrected only the test fixture to cancel its scheduled job while
  offline before publishing the mutation-test revision; APK bytes unchanged.
- User confirmed actual changing captions in 0.3, requested six-line context
  and removal of inter-cue blanks, and confirmed the updated Kobo sends over
  Wi-Fi despite its original .213 configuration. Added ~50-word caption
  windows, six-line minimum height, same20sp font, and retained text across
  gaps. Caption13groups and discovery15groups pass; real Peter Rabbit handoff
  displays61words of context. Final phone APK transfer/install pending.

- Final 0.3.1 APK copied to the physical OnePlus Download folder and read back
  with its matching SHA-256. User asked to install with Update and confirm
  six-line text, unchanged font, and no inter-cue blanks. Installation pending.

- Found the existing authorized OnePlus debugging interface and existing
  Windows authorization key. Used official Windows platform-tools37.0.1 in
  the task folder to update0.3.0 to0.3.1 directly. Actual installed APK SHA256,
  app identity and first-install timestamp verified; app data retained.
  The final visual check is waiting on the phone's secure lock screen.

- Final physical acceptance: OnePlus0.3.1 is installed with the exact tested
  APK hash. After unlock, an app-scoped capture showed53words in eight visible
  caption lines at the original20sp size, with the saved49server and offline
  audio ready. Final visual check passed. No phone audio was started by the
  verification. Temporary UI dumps and dedicated debugging server cleaned up.
