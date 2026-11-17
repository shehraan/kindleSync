"""Real LuaSocket transport checks against a healthy, slow browser action."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from test_webbrowser_remote import png


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT/'.tools/browser-ui-linux/runtime/lib/koreader'


class SlowBrowserHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.headers.get('Authorization') != 'Bearer runtime-test-token':
            self.send_error(401)
            return
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert payload['action'] == 'open'
        time.sleep(6)  # A cold Chromium start can exceed the old five-second block timeout.
        self.reply(json.dumps(self.server.metadata).encode(), 'application/json')

    def do_GET(self):
        if (self.headers.get('Authorization') != 'Bearer runtime-test-token'
                or self.path != self.server.metadata['frame_url']):
            self.send_error(401)
            return
        self.reply(self.server.frame, 'image/png')

    def reply(self, body, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected when the pre-fix client abandons a healthy request.


@unittest.skipUnless((RUNTIME/'luajit').is_file(), 'Local KOReader runtime needed')
class SlowBrowserTransportTests(unittest.TestCase):
    def test_slow_browser_action_is_not_reported_as_an_offline_pc(self):
        frame = png()
        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowBrowserHandler)
        server.frame = frame
        server.metadata = dict(session='a'*32, generation=1, width=600, height=800,
                               url='https://example.org/', title='Slow but reachable', zoom=2,
                               can_back=False, can_forward=False, focus=dict(kind='none'),
                               frame_url='/v1/browser/frame/'+'a'*32+'/1',
                               frame_sha256=hashlib.sha256(frame).hexdigest(), frame_bytes=len(frame))
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            script = home/'check.lua'
            script.write_text(r'''
require('setupkoenv')
package.path=arg[1]..'/koreader/webbrowser.koplugin/?.lua;'..package.path
G_defaults=require('luadefaults'):open()
G_reader_settings=require('luasettings'):open(os.getenv('KO_HOME')..'/settings.reader.lua')
local transport=require('webbrowser_remote_transport')
local page,message=transport.exchange(arg[2],'runtime-test-token',
 {action='open',width=600,height=800,zoom=2},'abcdef0123456789-1')
assert(page,'Healthy slow PC was reported unavailable: '..tostring(message))
assert(page.title=='Slow but reachable' and page.path)
transport.remove(page.path)
''')
            result = subprocess.run(
                [str(RUNTIME/'luajit'), str(script), str(ROOT), f'http://127.0.0.1:{server.server_port}'],
                cwd=RUNTIME, env={**os.environ, 'KO_HOME':folder, 'SDL_VIDEODRIVER':'dummy'},
                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
