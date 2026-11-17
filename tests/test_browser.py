"""PC browser protocol checks against a real, isolated Chromium profile."""

import hashlib
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch


def browser_executable():
    explicit = os.environ.get('KOBOSYNC_BROWSER_EXECUTABLE') or os.environ.get('KOBOSYNC_TEST_CHROMIUM')
    managed = sorted((Path(__file__).resolve().parents[1] / 'data/browser/engines').glob('chromium-*/chrome-linux64/chrome'))
    return explicit or (str(managed[-1]) if managed else shutil.which('google-chrome'))


PAGE = b'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Controlled browser</title><style>
body {margin:0;background:white;font:18px sans-serif} .item {position:absolute;left:20px;width:260px;height:35px;box-sizing:border-box}
#photo {position:absolute;left:20px;top:20px;width:60px;height:60px}
#js {top:90px} #name {top:140px} #secret {top:190px} #choice {top:240px}
#next {top:290px} #popup {top:340px} #unsafe {top:390px} #download {top:440px}
#low {position:absolute;left:20px;top:1200px} body {height:1500px}
</style><img id="photo" src="/image.svg"><div id="js" class="item">JavaScript missing</div>
<form action="/submitted"><input id="name" class="item" name="name" value="initial">
<input id="secret" class="item" type="password" value="initial-secret">
<select id="choice" class="item"><option value="one">First choice</option><option value="two">Second choice</option></select></form>
<a id="next" class="item" href="/second">Second page</a>
<a id="popup" class="item" href="/popup" target="_blank">Popup page</a>
<a id="unsafe" class="item" href="/redirect">Unsafe redirect</a>
<a id="download" class="item" href="/download">Download file</a>
<div id="low">Low page</div><script>
document.querySelector('#js').textContent='JavaScript active'; document.title='JavaScript active';
document.querySelector('#choice').onchange = event => document.title='Selected '+event.target.value;
</script>'''


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        headers = {}
        if path == '/':
            content, kind = PAGE, 'text/html'
        elif path == '/image.svg':
            self.server.image_requests += 1
            content = b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60"><rect width="60" height="60" fill="black"/></svg>'
            kind = 'image/svg+xml'
            headers['Cache-Control'] = 'public, max-age=3600'
        elif path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'file:///etc/passwd')
            self.end_headers()
            return
        elif path == '/submitted':
            content = ('<title>Submitted ' + parse_qs(urlsplit(self.path).query).get('name', [''])[0] + '</title>').encode()
            kind = 'text/html'
        elif path == '/cookie':
            content, kind = b'<title>Cookie saved</title>', 'text/html'
            headers['Set-Cookie'] = 'browser_fixture=retained; Max-Age=3600; Path=/'
        elif path == '/read-cookie':
            content = ('<title>' + ('Cookie retained' if 'browser_fixture=retained' in self.headers.get('Cookie', '') else 'Cookie absent') + '</title>').encode()
            kind = 'text/html'
        elif path == '/download':
            content, kind = b'controlled download', 'application/octet-stream'
            headers['Content-Disposition'] = 'attachment; filename="fixture.txt"'
        elif path == '/delayed':
            content, kind = b'<title>Waiting</title><script>setTimeout(()=>location.replace("/second"),1200)</script>', 'text/html'
        elif path in ('/focus-steal', '/focus-type'):
            change = "document.querySelector('#name').focus()" if path == '/focus-steal' else "event.target.type='text'"
            content = PAGE + ("<script>document.querySelector('#secret').addEventListener('focus',event=>setTimeout(()=>{" + change + "},1000))</script>").encode()
            kind = 'text/html'
        elif path == '/no-meta':
            content = b'''<!doctype html><title>Visible target</title><style>body{margin:0;background:white}button{position:absolute;left:200px;top:100px;width:100px;height:80px;border:0;background:black;color:white}</style><button onclick="document.title='Target clicked'">Target</button>'''
            kind = 'text/html'
        elif path == '/wide':
            content = b'''<!doctype html><title>Wide target</title><style>body{margin:0;background:white;width:1200px;height:500px}button{position:absolute;left:800px;top:100px;width:100px;height:80px;border:0;background:black;color:white}</style><button onclick="document.title='Wide target clicked'">Target</button>'''
            kind = 'text/html'
        elif path in ('/countries', '/large-select'):
            options = ''.join(f'<option value="country-{index}">{"Country " + str(index) if path == "/countries" else chr(0x754c) * 1024}</option>' for index in range(249 if path == '/countries' else 512))
            content = ('<meta name="viewport" content="width=device-width,initial-scale=1"><title>Countries</title><select style="position:absolute;left:20px;top:20px;width:260px;height:35px" onchange="document.title=\'Selected \'+this.value">' + options + '</select>').encode()
            kind = 'text/html'
        elif path == '/unicode-select':
            content = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Unicode choices</title><select style="position:absolute;left:20px;top:20px;width:260px;height:35px"><option value="one">First</option><option value="' + '界' * 1400 + '">Too long</option><option value="' + '界' * 1000 + '">Exact supported value</option></select>').encode()
            kind = 'text/html'
        else:
            content = ('<title>' + ('Popup page' if path == '/popup' else 'Second page') + '</title><a href="/">First page</a>').encode()
            kind = 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)


class BrowserContractTests(unittest.TestCase):
    def test_browser_service_exists_without_starting_or_importing_playwright(self):
        import kobosync.browser as browser
        with tempfile.TemporaryDirectory() as directory:
            service = browser.BrowserService(Path(directory))
            self.addCleanup(service.close)
            with self.assertRaises(ValueError):
                service.action({'action': 'open', 'width': 0, 'height': 1000})
            self.assertFalse((Path(directory) / 'browser' / 'profile').exists())

    def test_search_and_address_urls_preserve_complete_input(self):
        from kobosync.browser import navigation_url
        self.assertEqual(navigation_url('what do you think of The Pathless Path book'),
                         'https://duckduckgo.com/?q=what+do+you+think+of+The+Pathless+Path+book')
        self.assertEqual(navigation_url('  Deep Work book reviews  ', 'google'),
                         'https://www.google.com/search?q=Deep+Work+book+reviews')
        self.assertEqual(navigation_url('example.org/books?q=one+two'), 'https://example.org/books?q=one+two')
        self.assertEqual(navigation_url('http://127.0.0.1:8765/book'), 'http://127.0.0.1:8765/book')
        for invalid in ['file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,hello', 'ftp://example.org', 'x' * 4097]:
            with self.subTest(invalid=invalid[:30]), self.assertRaises(ValueError):
                navigation_url(invalid)

    def test_question_punctuation_and_host_ports_do_not_become_unsupported_schemes(self):
        from kobosync.browser import navigation_url
        self.assertEqual(navigation_url('Compare: Deep Work & The Pathless Path?'),
                         'https://duckduckgo.com/?q=Compare%3A+Deep+Work+%26+The+Pathless+Path%3F')
        self.assertEqual(navigation_url('example.org:8080/books'), 'https://example.org:8080/books')
        self.assertEqual(navigation_url('site:goodreads.com The Pathless Path'),
                         'https://duckduckgo.com/?q=site%3Agoodreads.com+The+Pathless+Path')
        self.assertEqual(navigation_url('site:goodreads.com'), 'https://duckduckgo.com/?q=site%3Agoodreads.com')

    @unittest.skipUnless(importlib.util.find_spec('playwright') and importlib.util.find_spec('PIL'), 'Install browser extra')
    def test_cancelled_browser_launch_releases_driver_before_retry(self):
        from kobosync.browser import BrowserService

        class DelayedDriver:
            def __init__(self, delayed):
                self.chromium = self
                self.stopped = False
                self.delayed = delayed

            async def launch_persistent_context(self, *args, **kwargs):
                if self.delayed:
                    await asyncio.Event().wait()
                raise RuntimeError('Controlled retry failure')

            async def stop(self):
                self.stopped = True

        first, second = DelayedDriver(True), DelayedDriver(False)
        drivers = iter((first, second))

        class Starter:
            async def start(self):
                return next(drivers)

        with tempfile.TemporaryDirectory() as directory:
            service = BrowserService(Path(directory), executable_path='/controlled-browser')
            self.addCleanup(service.close)

            async def cancel_launch():
                try:
                    await asyncio.wait_for(service._start(), timeout=.05)
                except TimeoutError:
                    pass
                return first.stopped, service._driver is None

            with patch('playwright.async_api.async_playwright', side_effect=Starter):
                self.assertEqual(service._submit(cancel_launch()), (True, True))
                with self.assertRaises(RuntimeError):
                    service._submit(service._start())
                self.assertTrue(second.stopped)


@unittest.skipUnless(importlib.util.find_spec('playwright') and importlib.util.find_spec('PIL'), 'Install browser extra for real Chromium tests')
class BrowserRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        cls.server.image_requests = 0
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def setUp(self):
        from kobosync.browser import BrowserService
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = BrowserService(self.root, executable_path=browser_executable())
        self.addCleanup(self.service.close)
        self.state = self.service.action({'action': 'open', 'width': 800, 'height': 1000, 'input': self.base + '/'})

    def act(self, action, **values):
        self.state = self.service.action({'action': action, 'session': self.state['session'], 'generation': self.state['generation'], **values})
        return self.state

    def tap(self, css_x, css_y):
        return self.act('click', x=css_x / 400, y=css_y / 500)

    def test_frame_renders_images_and_javascript_at_physical_dimensions(self):
        from PIL import Image
        self.assertEqual(self.state['title'], 'JavaScript active')
        self.assertEqual(self.state['zoom'], 2)
        self.assertRegex(self.state['session'], r'^[0-9a-f]{32}$')
        frame = self.service.frame(self.state['session'], self.state['generation'])
        data = frame.read_bytes()
        self.assertEqual(self.state['frame_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.state['frame_bytes'], len(data))
        self.assertLessEqual(len(data), 8 * 1024 * 1024)
        self.assertEqual(self.state['frame_url'], f"/v1/browser/frame/{self.state['session']}/{self.state['generation']}")
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (800, 1000))
            self.assertEqual(image.mode, 'L')
            self.assertLess(image.getpixel((80, 80)), 30)
            self.assertGreater(image.getpixel((700, 80)), 240)

    def test_text_focus_and_keyboard_submission(self):
        state = self.tap(80, 155)
        self.assertEqual(state['focus'], {'kind': 'text', 'value': 'initial'})
        state = self.act('text', value='Complete sentence with spaces', submit=True)
        self.assertEqual(state['title'], 'Submitted Complete sentence with spaces')
        self.assertEqual(parse_qs(urlsplit(state['url']).query)['name'], ['Complete sentence with spaces'])

    def test_password_value_never_leaves_browser(self):
        state = self.tap(80, 205)
        self.assertEqual(state['focus']['kind'], 'password')
        self.assertNotIn('initial-secret', json.dumps(state))
        state = self.act('text', value='new-private-password')
        self.assertNotIn('new-private-password', json.dumps(state))
        self.assertIn(state['focus'].get('value'), (None, ''))

    def test_select_metadata_and_actual_selection(self):
        state = self.tap(80, 255)
        self.assertEqual(state['focus']['kind'], 'select')
        self.assertEqual(state['focus']['options'], [{'label': 'First choice', 'value': 'one'}, {'label': 'Second choice', 'value': 'two'}])
        state = self.act('select', value='two')
        self.assertEqual(state['title'], 'Selected two')
        self.assertEqual(state['focus']['value'], 'two')

    def test_history_popup_and_zoom_coordinate_mapping(self):
        state = self.tap(80, 305)
        self.assertEqual(state['title'], 'Second page')
        self.assertTrue(state['can_back'])
        state = self.act('back')
        self.assertEqual(state['title'], 'JavaScript active')
        self.assertTrue(state['can_forward'])
        state = self.act('forward')
        self.assertEqual(state['title'], 'Second page')
        self.act('back')
        state = self.tap(80, 355)
        self.assertEqual(state['title'], 'Popup page')
        self.assertTrue(state['can_back'])
        self.assertEqual(self.act('back')['title'], 'JavaScript active')
        state = self.act('zoom', zoom=1)
        self.assertEqual(state['zoom'], 1)
        state = self.act('click', x=80 / 800, y=305 / 1000)
        self.assertEqual(state['title'], 'Second page')

    def test_stale_taps_are_rejected_and_refresh_recovers_latest_state(self):
        from kobosync.browser import BrowserConflict
        previous = self.state.copy()
        self.act('navigate', input=self.base + '/second')
        with self.assertRaises(BrowserConflict):
            self.service.action({'action': 'click', 'session': previous['session'], 'generation': previous['generation'], 'x': .2, 'y': .2})
        refreshed = self.service.action({'action': 'refresh', 'session': previous['session'], 'generation': previous['generation']})
        self.assertEqual(refreshed['title'], 'Second page')
        self.assertGreater(refreshed['generation'], previous['generation'])

    def test_only_three_immutable_frames_are_retained_and_paths_are_confined(self):
        first = self.state.copy()
        retained = self.service.frame(first['session'], first['generation']).read_bytes()
        self.act('refresh')
        self.assertEqual(self.service.frame(first['session'], first['generation']).read_bytes(), retained)
        self.act('refresh')
        self.act('refresh')
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.service.frame(first['session'], first['generation'])
        for session, generation in [('../profile', 1), ('0' * 32, 1), (first['session'], '../profile'), (first['session'], 0), (first['session'], True)]:
            with self.subTest(session=session, generation=generation), self.assertRaises((ValueError, FileNotFoundError)):
                self.service.frame(session, generation)

    def test_invalid_actions_are_rejected_without_moving_the_page(self):
        for action in [dict(action='navigate', input='file:///etc/passwd'), dict(action='click', x=2, y=0),
                       dict(action='scroll', delta=2), dict(action='key', key='Control+L'),
                       dict(action='zoom', zoom=4), dict(action='text', value='abc'), dict(action='select', value='two')]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.service.action({'session': self.state['session'], 'generation': self.state['generation'], **action})
        self.assertEqual(self.act('refresh')['title'], 'JavaScript active')

    def test_scroll_changes_frame_and_home_returns_to_local_landing_page(self):
        initial = self.state['frame_sha256']
        self.assertNotEqual(self.act('scroll', delta=1)['frame_sha256'], initial)
        state = self.act('home')
        self.assertEqual(state['title'], 'KoboSync Browser')
        self.assertEqual(state['focus']['kind'], 'none')

    def test_open_existing_session_preserves_page_and_expired_session_restarts(self):
        state = self.act('navigate', input=self.base + '/second')
        reopened = self.service.action({'action': 'open', 'session': state['session'], 'width': 800, 'height': 1000})
        self.assertEqual(reopened['title'], 'Second page')
        self.assertEqual(reopened['session'], state['session'])
        self.assertEqual(self.service.action({'action': 'close', 'session': state['session'], 'generation': 0}), {'closed': True})
        reopened = self.service.action({'action': 'open', 'session': state['session'], 'width': 800, 'height': 1000})
        self.assertNotEqual(reopened['session'], state['session'])
        self.assertIn('notice', reopened)

    def test_cookie_profile_survives_context_close_and_service_restart(self):
        self.act('navigate', input=self.base + '/cookie')
        self.assertEqual(self.act('close'), {'closed': True})
        self.service.close()
        from kobosync.browser import BrowserService
        self.service = BrowserService(self.root, executable_path=browser_executable())
        self.addCleanup(self.service.close)
        self.state = self.service.action({'action': 'open', 'width': 800, 'height': 1000, 'input': self.base + '/read-cookie'})
        self.assertEqual(self.state['title'], 'Cookie retained')
        self.service.close()
        with self.assertRaises(RuntimeError):
            self.service.action({'action': 'open', 'width': 800, 'height': 1000})

    def test_download_exposes_real_url_without_claiming_saved_file(self):
        state = self.tap(80, 455)
        self.assertIn('download', state['notice'].lower())
        self.assertEqual(state.get('download_url'), self.base + '/download')

    def test_redirect_cannot_open_a_local_file(self):
        state = self.tap(80, 405)
        self.assertIn(urlsplit(state['url']).scheme, ('http', 'https', 'about'))
        self.assertNotIn('root:', self.service.frame(state['session'], state['generation']).read_bytes().decode(errors='ignore'))
        self.assertIn('notice', state)

    def test_valid_https_failure_reports_loading_problem_and_recovers(self):
        # This fixture speaks plain HTTP, so HTTPS deterministically fails TLS.
        target = self.base.replace('http:', 'https:', 1) + '/?private=do-not-repeat-in-notice'
        session = self.state['session']
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                state = self.act('navigate', input=target)
                self.assertIn('loading', state.get('notice', '').lower())
                self.assertNotIn('HTTP and HTTPS', state['notice'])
                self.assertNotIn('do-not-repeat-in-notice', state['notice'])
                self.assertEqual(state['session'], session)
                self.assertEqual(state['url'], target)
                self.assertTrue(self.service.frame(state['session'], state['generation']).is_file())
        recovered = self.act('navigate', input=self.base + '/second')
        self.assertEqual(recovered['title'], 'Second page')
        self.assertEqual(recovered['session'], session)
        self.assertNotIn('notice', recovered)

    def test_failed_https_navigation_keeps_reload_and_history_usable(self):
        target = self.base.replace('http:', 'https:', 1) + '/'
        self.act('navigate', input=target)
        self.assertEqual(self.act('back')['title'], 'JavaScript active')
        failed = self.act('forward')
        self.assertEqual(failed['url'], target)
        self.assertIn('loading', failed.get('notice', '').lower())
        self.assertEqual(self.act('refresh')['url'], target)
        reloaded = self.act('reload')
        self.assertEqual(reloaded['url'], target)
        self.assertIn('loading', reloaded.get('notice', '').lower())
        self.assertEqual(self.act('back')['title'], 'JavaScript active')

    def test_malformed_action_values_return_input_errors_without_invalidating_frame(self):
        for values in [dict(action=[]), dict(action='key', key=[]), dict(action='open', width=True),
                       dict(action='click', x=float('nan'), y=.5), dict(action='navigate', input=['private'])]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.service.action({'session': self.state['session'], 'generation': self.state['generation'], **values})
        self.assertEqual(self.tap(80, 155)['focus']['kind'], 'text')

    def test_existing_session_open_cannot_replay_stale_navigation(self):
        from kobosync.browser import BrowserConflict
        old = self.state.copy()
        self.act('navigate', input=self.base + '/second')
        with self.assertRaises(BrowserConflict):
            self.service.action({'action': 'open', 'session': old['session'], 'generation': old['generation'], 'input': self.base + '/'})

    def test_concurrent_same_generation_taps_only_apply_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from kobosync.browser import BrowserConflict
        action = {'action': 'click', 'session': self.state['session'], 'generation': self.state['generation'], 'x': .2, 'y': .61}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(self.service.action, action) for _ in range(2)]
            outcomes = []
            for result in results:
                try:
                    outcomes.append(result.result())
                except BrowserConflict:
                    outcomes.append('conflict')
        self.assertEqual(outcomes.count('conflict'), 1)
        self.assertEqual([state['title'] for state in outcomes if isinstance(state, dict)], ['Second page'])

    def test_image_cache_survives_normal_navigation(self):
        image_requests = self.server.image_requests
        self.act('navigate', input=self.base + '/second')
        self.act('navigate', input=self.base + '/')
        self.assertEqual(self.server.image_requests, image_requests)

    def test_navigation_from_page_javascript_invalidates_the_displayed_frame(self):
        from kobosync.browser import BrowserConflict
        state = self.act('navigate', input=self.base + '/delayed')
        self.assertEqual(state['title'], 'Waiting')
        time.sleep(1.4)
        with self.assertRaises(BrowserConflict):
            self.service.action({'action': 'click', 'session': state['session'], 'generation': state['generation'], 'x': .2, 'y': .2})
        self.assertEqual(self.act('refresh')['title'], 'Second page')

    def test_review_password_focus_steal_rejects_secret_before_typing(self):
        from kobosync.browser import BrowserConflict
        self.act('navigate', input=self.base + '/focus-steal')
        self.assertEqual(self.tap(80, 205)['focus']['kind'], 'password')
        time.sleep(1.1)
        with self.assertRaises(BrowserConflict):
            self.act('text', value='do-not-leak-this-secret')
        state = self.act('refresh')
        self.assertEqual(state['focus'], {'kind': 'text', 'value': 'initial'})
        self.assertNotIn('do-not-leak-this-secret', json.dumps(state))

    def test_review_password_type_change_rejects_secret_before_typing(self):
        from kobosync.browser import BrowserConflict
        self.act('navigate', input=self.base + '/focus-type')
        self.assertEqual(self.tap(80, 205)['focus']['kind'], 'password')
        time.sleep(1.1)
        with self.assertRaises(BrowserConflict):
            self.act('text', value='do-not-leak-this-secret')
        self.assertNotIn('do-not-leak-this-secret', json.dumps(self.act('refresh')))

    def black_target(self):
        from PIL import Image
        with Image.open(self.service.frame(self.state['session'], self.state['generation'])) as image:
            bounds = image.point(lambda pixel: 255 if pixel < 30 else 0).getbbox()
        self.assertIsNotNone(bounds)
        return bounds

    def test_review_no_meta_visible_button_tap_uses_displayed_position(self):
        self.act('navigate', input=self.base + '/no-meta')
        left, top, right, bottom = self.black_target()
        state = self.act('click', x=(left + right) / 1600, y=(top + bottom) / 2000)
        self.assertEqual(state['title'], 'Target clicked')

    def test_review_no_meta_zoom_increases_visible_control_size(self):
        self.act('navigate', input=self.base + '/no-meta')
        self.act('zoom', zoom=1)
        left, _, right, _ = self.black_target()
        small = right - left
        self.act('zoom', zoom=2)
        left, _, right, _ = self.black_target()
        self.assertGreaterEqual(right - left, small * 1.6)

    def test_review_country_249_is_transmitted_and_selectable(self):
        self.act('navigate', input=self.base + '/countries')
        state = self.tap(80, 35)
        self.assertEqual(len(state['focus']['options']), 249)
        self.assertEqual(state['focus']['options'][-1]['value'], 'country-248')
        self.assertEqual(self.act('select', value='country-248')['title'], 'Selected country-248')

    def test_review_large_select_metadata_fits_the_transport_budget(self):
        self.act('navigate', input=self.base + '/large-select')
        state = self.tap(80, 35)
        self.assertEqual(state['focus']['kind'], 'select')
        self.assertLessEqual(len(json.dumps(state).encode()), 32768)
        self.assertTrue(state['focus']['options'])
        self.assertEqual(state['focus']['options'][0]['value'], 'country-0')

    def test_review_wide_page_horizontal_scroll_reaches_and_taps_control(self):
        state = self.act('navigate', input=self.base + '/wide')
        initial = state['frame_sha256']
        self.act('scroll', delta=0, delta_x=1)
        self.assertNotEqual(self.act('scroll', delta=0, delta_x=1)['frame_sha256'], initial)
        left, top, right, bottom = self.black_target()
        self.assertEqual(self.act('click', x=(left + right) / 1600, y=(top + bottom) / 2000)['title'], 'Wide target clicked')

    def test_review_focus_changes_after_validation_cannot_redirect_password_fill(self):
        self.assertEqual(self.tap(80, 205)['focus']['kind'], 'password')
        validate = self.service._validated_focus

        async def move_focus_after_validation():
            snapshot = await validate()
            await snapshot['element'].evaluate("() => document.querySelector('#name').focus()")
            return snapshot

        self.service._validated_focus = move_focus_after_validation
        state = self.act('text', value='still-private')
        self.assertEqual(state['focus']['kind'], 'password')
        self.assertNotIn('still-private', json.dumps(state))
        self.service._validated_focus = validate
        self.assertEqual(self.tap(80, 155)['focus']['value'], 'initial')

    def test_review_unicode_option_values_fit_client_byte_limit_without_truncation(self):
        self.act('navigate', input=self.base + '/unicode-select')
        state = self.tap(80, 35)
        values = [option['value'] for option in state['focus']['options']]
        self.assertTrue(all(len(value.encode()) <= 4096 for value in values))
        self.assertIn('界' * 1000, values)
        self.assertNotIn('界' * 1400, values)

