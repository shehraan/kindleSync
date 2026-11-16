# PC-assisted browser implementation plan

**Goal:** Browse real websites on the Kobo using the user's awake PC.
**Architecture:** Paired API controls isolated Chromium; KOReader displays cached
grayscale frames and forwards touch/keyboard actions.
**Spec:** `docs/superpowers/specs/2026-10-25-pc-browser-design.md`.
**Stack:** Python Playwright/Pillow, existing paired HTTP service, KOReader Lua.

The user chose PC-assisted rendering. Execute the authorized implementation,
using independent backend/client tasks and a final independent review. Preserve
current files and private settings with backups; publish reusable source only.

Review focus: authentication/frame confinement; stale taps and cancelled requests;
image-heavy memory use; secret field handling; lost sessions/service restarts.

### Task1: Browser process and state

- [x] Add failing tests for full-query URLs, viewport bounds, real image/JS/form
  rendering, history, stale generations and private profile lifecycle.
- [x] Implement `kobosync/browser.py`: BrowserService(root), action(dict),
  frame(session,generation), close(); dedicated event loop and bounded actions.
- [x] Verify with real local Chromium; retain a small immutable frame window.

### Task2: KOReader remote client and controls

- [x] Add failing Lua tests for authenticated requests, coordinate mapping,
  frame hash/length validation, keyboard/select, back/scroll, failure and closure.
- [x] Implement `webbrowser_remote.lua` and `webbrowser_remote_view.lua` only,
  reusing sibling KoboSync pairing/discovery and native keyboard/ImageWidget.
- [x] Bound binary streaming/GC and cache; preserve the book and browser bookmarks.
- [x] Verify with the actual Linux KOReader runtime and captured Chrome frames.

### Task3: API, dependency and menu integration

- [x] Add browser optional dependencies and install an isolated Chromium runtime.
- [x] Add authenticated API/action/frame tests, then wire `server.py`; implement
  orderly CLI/service shutdown and preserve existing book workers.
- [x] Point the existing browser menu at remote mode; retain legacy code/settings.
- [x] Verify full service tests and controlled Chromium-to-HTTP-to-KOReader flow.

### Task4: Runtime, review and deployment

- [x] Check live search behavior and images/JS without text-feed fallbacks.
  Ordinary search pages requested manual verification; results relevance remains
  a user/device acceptance check, not a claimed automated success.
- [x] Get an independent review and fix material defects with regressions.
- [x] Package client/server, document PC requirement and measured limits.
- [x] Back up and deploy via existing verified service/device paths; inspect actual
  runtime results, then record any final physical interaction still required.
- [ ] Confirm browsing, search relevance and responsiveness on the physical Kobo
  after the user restarts KOReader.

## Execution record

213 project tests pass, including 31 actual managed-Chromium tests and 26 client
tests. Independent review's five reproduced issues were fixed with regressions:
field identity, legacy viewport mapping, large dropdowns, cancelled startup and
post-shutdown requests. Thirteen complete native KOReader interface scenarios
passed with actual authenticated Chromium frames. The running coordinator and
Windows proxy are healthy. A live controlled-page check verified sandboxed
Chromium, authenticated PNG SHA256, legacy-page rendering and focus-change
rejection; cold open measured 0.796 s. These timings do not establish physical Kobo
speed or a comparison against the stock browser.

On November 16, build 0.9.0-kobosync3 was installed on the physical Libra 2 using
the guarded USB updater. The previous plugin was backed up, all six installed
files matched their expected SHA256 hashes, and protected settings, saved browser
data and KoboSync pairing/state remained unchanged. The Windows proxy health
check passed. The existing NickelMenu launcher was inspected; automatic boot into
KOReader is not configured. The user still needs to safely eject/unplug, restart
KOReader and confirm browsing, search relevance and responsiveness on the device.
