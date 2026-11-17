# Browser error reproduction and recovery, November 16

The user reported an unreachable PC or an HTTP/HTTPS requirement while entering
the valid address `https://google.com`. The physical Kobo's saved address,
cached endpoint and installed source were inspected: they matched the current
paired PC and source. A copied private configuration was then exercised with
the real native KOReader runtime, its widgets, subprocesses and LuaSocket,
against the actual PC coordinator.

Three failures were reproduced before their fixes:

- The home address editor prefilled `about:blank`, an internal address rejected
  by website validation. It now starts empty and the toolbar shows its normal
  search/address prompt on Home.
- A real native action POST timed out after 5.03 seconds and reported an
  unavailable PC, even though the PC API allows 35 seconds for rendering. A
  separate healthy HTTP fixture delaying its response by six seconds reproduced
  the same failure. POST response waits now allow that API budget; GET image
  stall limits, bounded total time, short background-close waits and user
  cancellation remain in place. Timeout, local request and server-error messages
  distinguish those failures from an unreachable PC.
- Valid HTTPS navigation failing TLS committed Chromium's internal error page.
  It was misclassified as forbidden navigation, replaced with Home and reported
  as requiring HTTP/HTTPS. The error page now retains its actual HTTP(S) target,
  a loading notice and usable Back/Forward/Reload. Successful navigation clears
  the notice. TLS checks, the Chromium sandbox and explicit scheme restrictions
  remain enabled.

The full project suite passed **245 tests in 290.238 seconds**. New regressions
cover the empty home field, real delayed LuaSocket responses, repeated HTTPS
failures followed by successful navigation, and failure history/reload. An
independent review reported no material findings.

Thirteen controlled native UI scenarios passed in 32.59 seconds, including an
actual image pixel, JavaScript, text/password keyboards, a dropdown, links,
history and scrolling. After restarting the installed coordinator, six live
Google open/home/navigate/close/reopen cycles passed in 89.60 seconds using the
copied Kobo settings. These are desktop/native simulator checks, not physical
Kobo latency measurements. [Verification results](browser-recovery-runtime-results.json)
record the final build and acceptance limits.

A further live check against the restarted coordinator verified an HTTPS
failure, Reload on that failed address, then recovery on a working HTTP page.
Every returned frame matched its advertised length and SHA256.

Build **0.9.0-kobosync4** was backed up, installed and hash-verified on the
Libra 2. Pairing, reader/browser settings, bookmarks and queued state were
preserved. The existing PC coordinator was gracefully restarted after verifying
its instance identity and exact process command. Its original Windows task,
ports and firewall configuration were retained. The user must safely eject and
restart KOReader to load the update; post-update physical interaction remains
an acceptance check.

Reproduction commands (the live runner needs a private device snapshot):

```bash
uv run python -m unittest discover -s tests -q
uv run python tests/browser_native_runtime.py
uv run python tests/browser_live_runtime.py --snapshot /private/device-snapshot --cycles 6 --expect-empty-home-input
```

The live runner never submits reading observations. It copies pairing and
browser settings into a temporary private KOReader home and removes that copy
afterward. Its screenshots/results/request timings stay under the ignored
`.tools/browser-pc-runtime/` directory. It logs timing/status only, without
tokens, form values, page contents or request bodies.
