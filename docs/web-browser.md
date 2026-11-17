# PC-assisted web browser

Open KOReader's magnifying-glass **Search** menu → **Web Browser**. The PC must
be awake with KoboSync running, and the Kobo must reach it on the same home Wi-Fi.
The existing KoboSync pairing is reused; there is no new account or search API key.
After installing a plugin update, safely eject/unplug the Kobo and restart KOReader.

The PC runs an isolated Chromium browser. Images, site styling, JavaScript and
website state are rendered there; the Kobo displays compressed grayscale views
and forwards touch and keyboard actions. This replaces the previous simplified
RSS search and text-only page rendering. It does not use your personal Chrome
profile, existing tabs or saved logins.

Tap the address bar, type a website or a complete search question, and choose
**Go**, **DuckDuckGo**, or **Google**. Questions are sent intact through ordinary
web search. Search providers can still request human verification; those pages
are displayed and can be operated normally. They are never treated as search
results, and verification is not bypassed.

Tap links in the page. Swipe vertically or use **Page up/down** to scroll; swipe
horizontally to pan wide pages. **Back**, **Forward**, **Home** and **Reload** use
the actual browser history. **Refresh** captures the latest state of a page,
including content that arrived after the previous view. The **2×** control changes
text size. **Bookmarks** and **Bookmark** retain the existing bookmark store.

Tapping a text field opens the Kobo keyboard. **Apply** changes its value;
**Enter** also submits the field/form. Password entry is masked and password
values are excluded from control metadata. Dropdowns open native choice lists.
If a page moves focus or changes while you are interacting, the browser refreshes
instead of sending your input to a different field. Close returns to your book.

Direct EPUB/PDF addresses retain the existing Kobo downloader. Public downloads
from a website may use that path too. Downloads requiring website session
credentials are not silently represented as saved files. Continuous video,
drag-and-drop, OS file choosers and every desktop interaction are not guaranteed
by a viewport client on an e-ink device. Modern Chromium improves compatibility;
neither universal website access nor faster loading on every site is claimed.

## PC setup

The deployed computer uses the existing Windows `KoboSync` task and ports. No new
firewall rule or public browser-debugging port is added. Chromium starts lazily,
reuses its own cookies and normal HTTP cache, and closes after fifteen minutes
without activity. Closing the client also releases its session. The private
profile and cached engine are under `data/browser/`.

For a new coordinator installation:

```bash
uv sync --extra transcribe --extra speech --extra browser
PLAYWRIGHT_BROWSERS_PATH="$PWD/data/browser/engines" uv run python -m playwright install chromium
uv run kobosync serve --host 0.0.0.0
```

Use the trusted home-LAN pairing described in the main setup guide. Browser
control and PNG frames require the existing bearer token; it is never supplied
to websites. The client verifies frame length, dimensions and SHA256, keeps a
small cache, and uses a cancellable subprocess for network work.

Book behavior differs from browsing: reading positions queue on the Kobo while
the PC is off, and downloaded phone audio remains usable offline. Browser page
navigation needs the PC awake throughout use.

## Verification and limitations

Real Chromium tests exercise images, JavaScript, forms/passwords, history,
popups, normal HTTP cache, accurate legacy-page taps and horizontal panning,
large dropdowns, cancellation and private profile lifecycle. Native Linux
KOReader at the Libra 2 resolution exercises the complete authenticated
Chromium → HTTP → image/touch/keyboard path with controlled pages. Run it with:

```bash
uv run python -m unittest discover -s tests -q
uv run python tests/browser_native_runtime.py
```

The native acceptance runner needs the downloaded Linux KOReader runtime under
`.tools/browser-ui-linux/runtime/`. It uses a separate test profile and generated
pairing, never the live reading library. Runtime receipts and screenshots are in
`.tools/browser-pc-runtime/`; private installation receipts are under `data/setup/`.
Desktop simulation does not establish physical Kobo latency or ghosting.

The upstream plugin base and licenses remain recorded in `UPSTREAM.json`.
Local build **0.9.0-kobosync4** includes the browser error fixes verified on
November 16. Rendering requests allow the PC's existing 35-second action budget;
image downloads retain a five-second stall limit and the loading dialog remains
cancellable. A slow render is not immediately reported as an offline PC.

The home address field starts empty. Failed HTTPS pages retain their attempted
website address, a loading-error notice and usable history/Reload, instead of
being misreported as unsupported HTTP/HTTPS navigation. TLS verification remains
enabled. See [reproduction and verification](browser-recovery.md).

The public plugin
archive is `dist/webbrowser-koreader-plugin.zip`; the device updater backs up the
existing plugin and preserves browser settings, bookmarks, menu order and
KoboSync state.
