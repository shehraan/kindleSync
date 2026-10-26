# PC-assisted Kobo browser

The user wants useful, fast browsing with complete search questions, images and
normal website interactions. They explicitly chose an awake PC rendering pages
on October 25. Keep the existing free KoboSync Windows/WSL service and pairing.
Reading/audio handoffs remain unchanged.

The current Bing RSS search returns dictionary definitions for complete book
questions; request encoding is correct. Native DuckDuckGo HTML search can return
good results but also verification pages. Merely swapping scrapers is insufficient.
Standalone cached HTML/images and stock-browser handoff were investigated; the
user selected a real PC browser for better compatibility.

Run an isolated persistent Chromium profile through Playwright on the PC. The
Kobo displays compressed grayscale viewport images and forwards taps, scrolling,
address/search input and text entry. JavaScript, images, styling, cookies and
normal browser history stay in Chromium. Use normal DuckDuckGo web search, with
Google selectable; preserve the complete query, without RSS or word heuristics.
Search verification is visible and interactive, never bypassed. Retain the
browser's normal cache; don't blanket-disable images/scripts to manufacture speed.

The Kobo's existing browser menu opens the new client. Controls: address/search,
Back, Forward, Home, Reload, Refresh, page up/down, text entry, zoom, bookmarks,
close. Touches use normalized coordinates within the displayed frame. A clicked
text field opens the Kobo keyboard, including password masking. Dropdown fields
offer native choices. Closing the browser returns to the original book. Show a
clear PC-unavailable message and retain the previous frame on network failure.

## Protocol and boundaries

All routes use the existing bearer authentication and private LAN discovery.
POST `/v1/browser/action` accepts JSON with `action`, optional `session`, physical
`width`/`height` (320–2048), `zoom` (1–3, default2), and action-specific values.
Actions: `open`, `navigate` (`input`, optional `engine`), `click` (normalized `x,y`),
`scroll` (`delta` in -1..1), `text` (`value`, `submit`), `select` (`value`),
`key` (allowlisted key), `back`, `forward`, `home`, `reload`, `refresh`, `zoom`,
`close`. Mutating actions carry the displayed `generation`; stale frames cannot
activate controls on a different page. Queries are bounded at 4096 characters.
Only HTTP(S) website navigation is accepted, including redirect/popup guards.

Results contain `session` (32 lowercase hex), `generation`, `url`, `title`,
`can_back`, `can_forward`, `zoom`, `width`, `height`, `frame_url`, `frame_sha256`,
`frame_bytes`, optional `notice`, and `focus` (`kind` text/password/select/none,
`value`, optional options list of {label,value}). Never return a password value.
Close returns `{closed:true}`. Frames are immutable PNGs at authenticated GET
`/v1/browser/frame/<session>/<generation>`; keep the newest3 per session. Maximum
frame8MiB. Client validates relative frame URLs, byte counts and SHA256 before
publishing a frame. Binary data goes to a cache file in the network child, not
through a large subprocess return value. Keep two client frames, with bounded
Lua allocation and stale-result protection on close/navigation.

`BrowserService(root).action(data) -> dict`, `.frame(session,generation) -> Path`,
`.close()` form the synchronous interface to a dedicated browser event-loop
thread. Serialize actions, bound waits/navigation, and start Chromium lazily.
Use `data/browser/` only; never attach to the user's personal Chrome profile.
One persistent context, up to4 pages; Back closes a newly opened page when it
has no prior history. Start with device scale2 and a mobile-width viewport for
readable text. Resize screenshots to the exact physical viewport if necessary.
Show partial/loading pages after a capped settling wait; Refresh updates them.
Preserve cookies/cache across reopening; orderly service shutdown closes Chromium.

Opening direct EPUB/PDF URLs retains the existing downloader. A clicked download
may expose its public URL for that workflow; authenticated downloads and uploads
must not silently lose data or be represented as successful unsupported actions.
No personal tabs, browser console, entered text or URLs are written to logs.
Do not expose browser control outside the paired API, add new public ports, or
change global browser/network settings. Dependency/model installs are local.

## Verification

Use real Chromium with a controlled page containing a linked second page, an
image, JavaScript, a text form, a password and a select. Verify rendering,
coordinate mapping, scrolling/history, keyboard submission, stale generations,
auth and process shutdown/restart. Native Linux KOReader checks must display the
actual returned frame and exercise toolbar and tap/keyboard paths. Test complete
book questions on live normal search pages, recording relevance and latency,
with explicit verification/blocked-page results. Measure cached actions and keep
claims within evidence. Package/deploy the plugin preserving settings/bookmarks
and KoboSync files; physical-device response and viewing remain final checks.
