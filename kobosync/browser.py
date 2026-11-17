"""Paired viewport control of a private, persistent desktop Chromium browser.

The public facade is synchronous for the HTTP server. All Playwright objects and
browser actions stay on one event-loop thread, including orderly shutdown.
Browser dependencies are imported only when the first session is opened.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlencode, urlsplit


class BrowserConflict(ValueError):
    """The displayed frame or session has expired and must be refreshed."""

    code = 'stale_frame'


def navigation_url(value: str, engine: str = 'duckduckgo') -> str:
    """Keep an entire question in one ordinary web-search query."""
    if not isinstance(value, str) or len(value) > 4096 or not value.strip():
        raise ValueError('Enter a website address or a search of at most 4096 characters.')
    if engine not in ('duckduckgo', 'google'):
        raise ValueError('Choose DuckDuckGo or Google search.')
    value = value.strip()
    scheme = re.match(r'^([a-zA-Z][a-zA-Z0-9+.-]*):', value)
    if scheme and scheme[1].lower() in ('http', 'https'):
        result = urlsplit(value)
        if not result.hostname or any(char.isspace() for char in value):
            raise ValueError('Enter a valid HTTP or HTTPS website address.')
        try:
            result.port
        except ValueError:
            raise ValueError('Enter a valid website port.') from None
        return value
    if scheme and (scheme[1].lower() in ('file', 'javascript', 'data', 'ftp', 'ftps', 'about',
                                        'chrome', 'chrome-extension', 'devtools', 'mailto', 'tel', 'blob')
                   or value[len(scheme[0]):].startswith('//')):
        raise ValueError('Only HTTP and HTTPS website addresses are supported.')
    if not any(char.isspace() for char in value):
        host = value.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]
        hostname = host.split(':', 1)[0]
        if '.' in hostname or hostname == 'localhost' or host.startswith('['):
            return navigation_url('https://' + value, engine)
    base = 'https://www.google.com/search' if engine == 'google' else 'https://duckduckgo.com/'
    return base + '?' + urlencode({'q': value})


_FOCUS_SCRIPT = """el => {
 const metadata = () => {
  if (!el || !el.isConnected || el !== document.activeElement || !document.hasFocus() || el.disabled || el.readOnly) return {kind:'none'};
  if (el.tagName === 'SELECT') {
   if (el.value.length > 4096) return {kind:'none'};
   return {kind:'select',value:el.value,
    options:Array.from(el.options).filter(o=>!o.disabled && o.value.length<=4096 && !(o.parentElement.tagName==='OPTGROUP' && o.parentElement.disabled))
     .slice(0,512).map(o=>({label:o.label.slice(0,128),value:o.value}))};
  }
  if (el.tagName === 'INPUT' && el.type === 'password') return {kind:'password'};
  if (el.tagName === 'TEXTAREA' || el.isContentEditable ||
      (el.tagName === 'INPUT' && ['text','search','email','url','tel','number'].includes(el.type)))
   return {kind:'text',value:(el.isContentEditable ? el.innerText : el.value).slice(0,4096)};
  return {kind:'none'};
 };
 return {metadata:metadata(),signature:[el.tagName,el.type||'',el.contentEditable]};
}"""

_VALID_FOCUS_SCRIPT = """(el, signature) => el.isConnected &&
 el === document.activeElement && document.hasFocus() && !el.disabled && !el.readOnly &&
 [el.tagName,el.type||'',el.contentEditable].every((value,index)=>value===signature[index])"""

_VIEW_SCRIPT = """() => {
 const view = window.visualViewport;
 return {width:view ? view.width : innerWidth, height:view ? view.height : innerHeight,
         x:view ? view.offsetLeft : 0, y:view ? view.offsetTop : 0};
}"""

_HOME = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KoboSync Browser</title><style>body{font:20px sans-serif;margin:28px;line-height:1.5;background:white;color:black}
h1{font-size:28px}</style></head><body><h1>Browse the web</h1><p>Enter a website address or a complete search question in the address bar.</p>
<p>Websites run on your paired PC. Keep it awake while browsing.</p></body></html>'''


class BrowserService:
    """Own the private browser process, its displayed generation and PNG frames."""

    _KEYS = frozenset(('Enter', 'Tab', 'Shift+Tab', 'Escape', 'Backspace', 'Delete',
                       'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End',
                       'PageUp', 'PageDown', 'Space'))
    _MUTATIONS = frozenset(('navigate', 'click', 'scroll', 'text', 'select', 'key',
                            'back', 'forward', 'home', 'reload', 'zoom'))

    def __init__(self, root: Path, *, executable_path: str | Path | None = None):
        self.root = Path(root) / 'browser'
        self._executable_path = str(executable_path) if executable_path else None
        self._loop = asyncio.new_event_loop()
        self._submission_lock = threading.Lock()
        self._closed = False
        self._driver = self._context = self._page = None
        self._session = None
        self._generation = 0
        self._document_revision = 0
        self._width, self._height, self._zoom = 800, 1000, 2
        self._pages = []
        self._parents = {}
        self._guards = {}
        self._frames = {}
        self._notice = None
        self._download_url = None
        self._idle_timer = None
        self._action_lock = None
        self._display_focus = None
        self._display_view = None
        self._thread = threading.Thread(target=self._run_loop, name='kobosync-browser', daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        self._loop.close()

    def _submit(self, operation):
        with self._submission_lock:
            if self._closed:
                operation.close()
                raise RuntimeError('The browser service has stopped.')
            future = asyncio.run_coroutine_threadsafe(operation, self._loop)
        try:
            return future.result(timeout=35)
        except FutureTimeout:
            future.cancel()
            raise RuntimeError('The PC browser took too long. Reopen it and try again.') from None

    def action(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError('A browser action must be a JSON object.')
        return self._submit(self._dispatch(dict(data)))

    def frame(self, session: str, generation: int) -> Path:
        return self._submit(self._get_frame(session, generation))

    async def _get_frame(self, session, generation):
        if not isinstance(session, str) or not re.fullmatch('[0-9a-f]{32}', session):
            raise ValueError('Invalid browser session.')
        if type(generation) is not int or generation < 1:
            raise ValueError('Invalid browser frame generation.')
        path = self._frames.get((session, generation))
        if path is None or not path.is_file():
            raise FileNotFoundError('This browser frame has expired. Refresh the browser.')
        if not path.resolve().is_relative_to((self.root / 'frames').resolve()):
            raise ValueError('Invalid browser frame path.')
        return path

    @staticmethod
    def _number(value, minimum, maximum, label, *, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Invalid browser ' + label + '.')
        if value < minimum or value > maximum or (integer and value != int(value)):
            raise ValueError('Invalid browser ' + label + '.')
        return int(value) if integer else value

    def _viewport(self):
        return {'width': math.ceil(self._width / self._zoom), 'height': math.ceil(self._height / self._zoom)}

    def _engine_binary(self):
        if self._executable_path:
            return self._executable_path
        engines = self.root / 'engines'
        for pattern in ('chromium-*/chrome-linux64/chrome', 'chromium-*/chrome-linux/chrome',
                        'chromium-*/chrome-win64/chrome.exe', 'chromium-*/chrome-win/chrome.exe',
                        'chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium'):
            found = sorted(engines.glob(pattern), reverse=True)
            if found:
                return str(found[0])
        raise RuntimeError('Install the PC browser engine in the KoboSync data/browser/engines folder, then reopen the browser.')

    async def _start(self):
        if self._context:
            return
        try:
            from playwright.async_api import async_playwright
            from PIL import Image  # noqa: F401 - validate optional dependency before starting Chromium
        except ImportError:
            raise RuntimeError('Install the KoboSync browser extra and its Chromium engine on the PC.') from None
        executable = self._engine_binary()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        environment = os.environ.copy()
        for variable, dirname in (('XDG_CONFIG_HOME', 'runtime-config'), ('XDG_CACHE_HOME', 'runtime-cache')):
            directory = self.root / dirname
            directory.mkdir(mode=0o700, exist_ok=True)
            environment[variable] = str(directory)
        starter = async_playwright()
        try:
            self._driver = await starter.start()
            self._context = await self._driver.chromium.launch_persistent_context(
                str(self.root / 'profile'), executable_path=executable, headless=True,
                chromium_sandbox=True, viewport=self._viewport(), device_scale_factor=2,
                is_mobile=False, has_touch=True, accept_downloads=False,
                env=environment,
            )
            self._context.set_default_timeout(4000)
            self._context.set_default_navigation_timeout(10000)
            self._context.on('page', self._observe_page)
            existing = list(self._context.pages)
            self._page = existing[0] if existing else await self._context.new_page()
            if existing:
                await self._observe_page(self._page)
            for page in existing[1:]:
                await page.close()
            self._session = secrets.token_hex(16)
            self._generation = 0
            await self._home()
        except BaseException as error:
            driver_started = self._driver is not None
            try:
                await self._close_context()
                if not driver_started and hasattr(starter, '__aexit__'):
                    await starter.__aexit__(type(error), error, error.__traceback__)
            except BaseException:
                pass
            if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError('Chromium could not start with its sandbox enabled. Check the PC browser installation and OS sandbox permissions.') from None

    async def _observe_page(self, page):
        if page in self._pages:
            return
        if len([p for p in self._pages if not p.is_closed()]) >= 4:
            self._notice = 'The browser has four open pages. Close a popup with Back before opening another.'
            await page.close()
            return
        self._pages.append(page)
        self._page = page
        self._invalidate_document()
        page.on('framenavigated', lambda frame: self._navigated(page, frame))
        page.on('close', lambda: self._forget_page(page))
        # Request interception disables Chromium's HTTP cache. A small native
        # block list keeps privileged schemes closed without intercepting normal
        # images, scripts, data URLs or cached HTTP resources.
        guard = await self._context.new_cdp_session(page)
        self._guards[page] = guard
        await guard.send('Network.enable')
        await guard.send('Network.setBlockedURLs', {'urls': [
            'file:*', 'ftp:*', 'ftps:*', 'chrome:*', 'chrome-extension:*', 'devtools:*',
        ]})
        opener = await page.opener()
        if opener:
            self._parents[page] = opener
        page.on('download', self._download)

    def _invalidate_document(self):
        self._generation += 1
        self._document_revision += 1

    def _navigated(self, page, frame):
        if page == self._page and frame == page.main_frame:
            self._invalidate_document()

    def _forget_page(self, page):
        if page in self._pages:
            self._pages.remove(page)
        parent = self._parents.pop(page, None)
        self._guards.pop(page, None)
        if self._page == page:
            self._page = parent if parent and not parent.is_closed() else next((p for p in reversed(self._pages) if not p.is_closed()), None)
            self._invalidate_document()

    async def _download(self, download):
        self._notice = 'This download was not saved. Public file addresses can be opened with the Kobo downloader.'
        parsed = urlsplit(download.url)
        if parsed.scheme in ('http', 'https') and not parsed.username:
            cookies = await self._context.cookies(download.url)
            if not cookies:
                self._download_url = download.url[:4096]

    async def _home(self):
        await self._page.goto('about:blank', wait_until='domcontentloaded')
        await self._page.set_content(_HOME, wait_until='domcontentloaded')

    async def _focus(self):
        if self._page and not self._page.is_closed():
            for frame in reversed(self._page.frames):
                try:
                    handle = await frame.evaluate_handle('() => document.activeElement')
                    element = handle.as_element()
                    if element:
                        result = await element.evaluate(_FOCUS_SCRIPT)
                        if result['metadata']['kind'] != 'none':
                            return {'element': element, 'frame': frame, **result}
                    await handle.dispose()
                except Exception:
                    continue
        return None

    async def _replace_focus(self, snapshot):
        previous, self._display_focus = self._display_focus, snapshot
        if previous:
            try:
                await previous['element'].dispose()
            except Exception:
                pass

    async def _validated_focus(self):
        snapshot = self._display_focus
        if snapshot is None:
            return None
        try:
            valid = await snapshot['element'].evaluate(_VALID_FOCUS_SCRIPT, snapshot['signature'])
        except Exception:
            valid = False
        if not valid:
            raise BrowserConflict('The focused field changed. Refresh and tap the intended field again.')
        return snapshot

    @staticmethod
    def _bounded_focus(focus, budget):
        """Bound JSON metadata without ever changing a selectable option value."""
        omitted = False
        if focus['kind'] == 'select':
            if len(focus['value'].encode()) > 4096:
                return {'kind': 'none'}, True
            options = [option for option in focus['options'] if len(option['value'].encode()) <= 4096]
            omitted = len(options) != len(focus['options'])
            focus = {**focus, 'options': options}
        if len(json.dumps(focus).encode()) <= budget:
            return focus, omitted
        if focus['kind'] != 'select':
            return {'kind': 'none'}, True
        result = {'kind': 'select', 'value': focus['value'], 'options': []}
        remaining = budget - len(json.dumps(result).encode())
        for option in focus['options']:
            candidate = dict(option)
            cost = len(json.dumps(candidate).encode()) + 2
            while cost > remaining and candidate['label']:
                candidate['label'] = candidate['label'][:len(candidate['label']) // 2]
                cost = len(json.dumps(candidate).encode()) + 2
            if cost <= remaining:
                result['options'].append(candidate)
                remaining -= cost
        if not result['options']:
            return {'kind': 'none'}, True
        return result, True

    async def _history(self):
        client = self._guards.get(self._page)
        if client is None:
            await self._observe_page(self._page)
            client = self._guards[self._page]
        history = await client.send('Page.getNavigationHistory')
        index, entries = history['currentIndex'], history['entries']
        return index > 0, index + 1 < len(entries), entries[index]['url']

    async def _navigate(self, operation, *args):
        try:
            await operation(*args, wait_until='domcontentloaded', timeout=10000)
        except Exception as error:
            # Timeouts still leave a useful, partially loaded real page visible.
            # Never include Playwright's exception text: it can contain private URLs.
            if 'Timeout' in type(error).__name__:
                self._notice = 'The page is still loading. Refresh to update it.'
            elif self._notice is None:
                self._notice = 'The website could not finish loading. Check the address or try Reload.'

    async def _settle(self):
        if not self._page or self._page.is_closed():
            self._page = await self._context.new_page()
            await self._home()
        try:
            await self._page.wait_for_load_state('load', timeout=700)
        except Exception:
            pass
        await self._page.wait_for_timeout(150)
        if self._page.url == 'chrome-error://chromewebdata/':
            _, _, target = await self._history()
            if urlsplit(target).scheme in ('http', 'https'):
                # Chromium commits an internal error document after a network or
                # TLS failure. Keep its history entry and report the web failure.
                self._notice = 'The website could not finish loading. Check the address or try Reload.'
                return
        if self._page.url != 'about:blank' and urlsplit(self._page.url).scheme not in ('http', 'https'):
            self._notice = 'Only HTTP and HTTPS website navigation is supported.'
            await self._home()

    async def _capture(self):
        from PIL import Image
        await self._settle()
        for attempt in range(2):
            revision = self._document_revision
            page = self._page
            view = await page.evaluate(_VIEW_SCRIPT)
            screenshot = await page.screenshot(type='png', full_page=False, timeout=5000, animations='disabled')
            can_back, can_forward, history_url = await self._history()
            snapshot = await self._focus()
            focus = snapshot['metadata'] if snapshot else {'kind': 'none'}
            title = (await page.title())[:1024]
            url = (history_url if page.url == 'chrome-error://chromewebdata/' else page.url)[:4096]
            if page == self._page and revision == self._document_revision and view == await page.evaluate(_VIEW_SCRIPT):
                break
            if snapshot:
                await snapshot['element'].dispose()
            if attempt:
                raise BrowserConflict('The page changed while rendering. Refresh before interacting with it.')
            await self._settle()
        with Image.open(io.BytesIO(screenshot)) as original:
            image = original.convert('L')
            if image.size != (self._width, self._height):
                image = image.resize((self._width, self._height), Image.Resampling.LANCZOS)
            encoded = io.BytesIO()
            image.save(encoded, format='PNG')
            image.close()
        data = encoded.getvalue()
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeError('The browser frame is too large. Reduce the viewport and reopen it.')
        frame_dir = self.root / 'frames'
        frame_dir.mkdir(exist_ok=True)
        self._generation += 1
        path = frame_dir / f'{self._session}-{self._generation}.png'
        path.write_bytes(data)
        self._frames[(self._session, self._generation)] = path
        while len(self._frames) > 3:
            old = self._frames.pop(next(iter(self._frames)))
            old.unlink(missing_ok=True)
        result = {
            'session': self._session, 'generation': self._generation,
            'url': url, 'title': title,
            'can_back': can_back or self._page in self._parents, 'can_forward': can_forward,
            'zoom': self._zoom, 'width': self._width, 'height': self._height,
            'frame_url': f'/v1/browser/frame/{self._session}/{self._generation}',
            'frame_sha256': hashlib.sha256(data).hexdigest(), 'frame_bytes': len(data), 'focus': focus,
        }
        if self._notice:
            result['notice'] = self._notice
        if self._download_url:
            result['download_url'] = self._download_url
        # Leave headroom for the notice and JSON formatting used by the HTTP API.
        result['focus'] = {'kind': 'none'}
        budget = min(24 * 1024, 32768 - len(json.dumps(result).encode()) - 512)
        result['focus'], limited = self._bounded_focus(focus, max(0, budget))
        if limited:
            result['notice'] = 'Some field choices are too large for the reader. Use the PC browser for additional choices.'
        if snapshot and result['focus']['kind'] == 'none':
            await snapshot['element'].dispose()
            snapshot = None
        if snapshot:
            snapshot['metadata'] = result['focus']
        await self._replace_focus(snapshot)
        self._display_view = view
        return result

    async def _dispatch(self, data):
        if self._action_lock is None:
            self._action_lock = asyncio.Lock()
        async with self._action_lock:
            try:
                async with asyncio.timeout(25):
                    return await self._perform(data)
            except (ValueError, FileNotFoundError, RuntimeError):
                raise
            except TimeoutError:
                self._generation += 1
                raise RuntimeError('The PC browser took too long. Refresh or reopen it to continue.') from None
            except Exception:
                self._generation += 1
                raise RuntimeError('The PC browser could not complete this action. Refresh or reopen it to continue.') from None
            finally:
                self._schedule_idle_close()

    async def _perform(self, data):
        action = data.get('action')
        if not isinstance(action, str):
            raise ValueError('A browser action name is required.')
        if action == 'open':
            width = self._number(data.get('width', self._width), 320, 2048, 'width', integer=True)
            height = self._number(data.get('height', self._height), 320, 2048, 'height', integer=True)
            zoom = self._number(data.get('zoom', self._zoom if self._context else 2), 1, 3, 'zoom')
            target = navigation_url(data['input'], data.get('engine', 'duckduckgo')) if data.get('input') else None
            requested = data.get('session')
            if self._context and requested == self._session and target is not None:
                if type(data.get('generation')) is not int or data['generation'] != self._generation:
                    raise BrowserConflict('This browser frame is stale. Refresh before navigating.')
            expired = requested is not None and requested != self._session
            if self._context and requested != self._session:
                await self._close_context()
            self._width, self._height, self._zoom = width, height, zoom
            self._notice = 'The previous browser session expired. A new session has opened.' if expired else None
            self._download_url = None
            await self._start()
            await self._page.set_viewport_size(self._viewport())
            if target:
                await self._navigate(self._page.goto, target)
            return await self._capture()
        if action == 'close':
            if data.get('session') == self._session:
                await self._close_context()
            return {'closed': True}
        if action not in self._MUTATIONS and action != 'refresh':
            raise ValueError('Unknown browser action.')
        if not self._context or data.get('session') != self._session:
            raise BrowserConflict('This browser session expired. Reopen the browser to continue.')
        if action in self._MUTATIONS and (type(data.get('generation')) is not int or data['generation'] != self._generation):
            raise BrowserConflict('This browser frame is stale. Refresh before interacting with the page.')
        target = None
        focus = element = None
        if action == 'navigate':
            target = navigation_url(data.get('input'), data.get('engine', 'duckduckgo'))
        elif action == 'click':
            self._number(data.get('x'), 0, 1, 'tap coordinate')
            self._number(data.get('y'), 0, 1, 'tap coordinate')
            if self._display_view != await self._page.evaluate(_VIEW_SCRIPT):
                raise BrowserConflict('The visible page moved. Refresh before tapping it.')
        elif action == 'scroll':
            self._number(data.get('delta'), -1, 1, 'scroll amount')
            self._number(data.get('delta_x', 0), -1, 1, 'horizontal scroll amount')
        elif action == 'zoom':
            self._number(data.get('zoom'), 1, 3, 'zoom')
        elif action == 'key' and (not isinstance(data.get('key'), str) or data['key'] not in self._KEYS):
            raise ValueError('Unsupported browser key.')
        elif action in ('text', 'select'):
            value = data.get('value')
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError('Browser field values must contain at most 4096 characters.')
            snapshot = await self._validated_focus()
            focus = snapshot['metadata'] if snapshot else {'kind': 'none'}
            element = snapshot['element'] if snapshot else None
            if action == 'text' and focus['kind'] not in ('text', 'password'):
                raise ValueError('Tap a text field before entering text.')
            if action == 'select' and focus['kind'] != 'select':
                raise ValueError('Tap a dropdown before choosing an option.')
            if action == 'text' and type(data.get('submit', False)) is not bool:
                raise ValueError('Invalid browser field submission flag.')
            if action == 'select' and value not in [option['value'] for option in focus['options']]:
                raise ValueError('Choose one of the available dropdown options.')
        self._notice = self._download_url = None
        if action in self._MUTATIONS:
            # A failed interaction must not leave its old frame actionable.
            self._generation += 1
        if action == 'navigate':
            await self._navigate(self._page.goto, target)
        elif action == 'click':
            view = self._display_view
            await self._page.mouse.click(view['x'] + min(data['x'] * view['width'], view['width'] - 1),
                                         view['y'] + min(data['y'] * view['height'], view['height'] - 1))
            temporary_focus = await self._focus()
            if temporary_focus:
                if temporary_focus['metadata']['kind'] == 'select':
                    await self._page.keyboard.press('Escape')
                await temporary_focus['element'].dispose()
        elif action == 'scroll':
            await self._page.mouse.wheel(data.get('delta_x', 0) * self._display_view['width'] * .85,
                                         data['delta'] * self._display_view['height'] * .85)
        elif action == 'text':
            await element.fill(data['value'])
            if data.get('submit', False):
                submitted = await element.evaluate('''element => {
                    if (!element.form) return false;
                    element.form.requestSubmit(); return true;
                }''')
                if not submitted:
                    await element.press('Enter')
        elif action == 'select':
            await element.select_option(data['value'])
        elif action == 'key':
            await self._page.keyboard.press(data['key'])
        elif action == 'back':
            can_back, _, _ = await self._history()
            if can_back:
                await self._navigate(self._page.go_back)
            elif self._page in self._parents:
                page = self._page
                self._page = self._parents[page]
                await page.close()
        elif action == 'forward':
            await self._navigate(self._page.go_forward)
        elif action == 'home':
            await self._home()
        elif action == 'reload':
            if self._page.url == 'about:blank':
                await self._home()
            else:
                await self._navigate(self._page.reload)
        elif action == 'zoom':
            self._zoom = data['zoom']
            for page in self._pages:
                await page.set_viewport_size(self._viewport())
        return await self._capture()

    def _schedule_idle_close(self):
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None
        if self._context:
            self._idle_timer = self._loop.call_later(15 * 60, lambda: self._loop.create_task(self._expire()))

    async def _expire(self):
        async with self._action_lock:
            await self._close_context()

    async def _close_context(self):
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None
        context, driver = self._context, self._driver
        await self._replace_focus(None)
        self._display_view = None
        self._context = self._driver = self._page = None
        self._session = None
        try:
            if context:
                await context.close()
        finally:
            if driver:
                await driver.stop()
            self._pages.clear()
            self._parents.clear()
            self._guards.clear()
            for path in self._frames.values():
                path.unlink(missing_ok=True)
            self._frames.clear()

    async def _shutdown(self):
        if self._action_lock is None:
            self._action_lock = asyncio.Lock()
        async with self._action_lock:
            await self._close_context()

    def close(self):
        with self._submission_lock:
            if self._closed:
                return
            self._closed = True
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            future.result(timeout=35)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)

