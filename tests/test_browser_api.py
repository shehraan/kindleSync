"""Paired browser RPC and frame delivery, independent of Chrome startup."""
import hashlib
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kobosync import server as api
from kobosync.store import Store


class BrowserApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / 'data')
        self.image = self.store.root / 'screen.png'
        self.image.write_bytes(b'\x89PNG\r\n\x1a\ncontrolled frame bytes')
        self.session = 'a' * 32
        self.actions = []
        self.closed = False
        self.conflict = type('BrowserConflict', (ValueError,), {'code': 'stale_frame'})
        owner = self

        class Browser:
            def action(self, data):
                owner.actions.append(data)
                if data['action'] == 'stale':
                    raise owner.conflict('The displayed page changed; refresh it.')
                if data['action'] == 'unavailable':
                    raise RuntimeError('The PC browser is unavailable.')
                return dict(session=owner.session, generation=1,
                            frame_url='/v1/browser/frame/' + owner.session + '/1',
                            frame_sha256=hashlib.sha256(owner.image.read_bytes()).hexdigest(),
                            frame_bytes=owner.image.stat().st_size)

            def frame(self, session, generation):
                if session != owner.session or generation != 1:
                    raise ValueError('Unknown browser frame')
                return owner.image

            def close(self):
                owner.closed = True

        self.browser = Browser()
        self.factory = self.enterContext(patch.object(api, 'create_browser', return_value=self.browser, create=True))
        self.server = api.make_server(self.store, '127.0.0.1', 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, data=None, auth=True, method=None):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.store.token
        body = json.dumps(data).encode() if data is not None else None
        return urlopen(Request(self.base + path, data=body, headers=headers, method=method), timeout=3)

    def test_commands_and_frames_require_pairing_before_browser_startup(self):
        for path, data in [('/v1/browser/action', {'action': 'open'}),
                           ('/v1/browser/frame/' + self.session + '/1', None)]:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path, data, auth=False)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
        self.assertEqual(self.actions, [])
        self.factory.assert_not_called()

    def test_complete_question_reaches_browser_and_frame_is_authenticated_png(self):
        question = 'what do you think of The Pathless Path book compared with Deep Work?'
        with self.request('/v1/browser/action', {'action': 'navigate', 'input': question}) as response:
            state = json.load(response)
        self.assertEqual(self.actions[0]['input'], question)
        self.assertEqual(self.factory.call_count, 1)
        with self.request(state['frame_url']) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            data = response.read()
        self.assertEqual(data, self.image.read_bytes())
        self.assertEqual(hashlib.sha256(data).hexdigest(), state['frame_sha256'])
        self.assertEqual(len(data), state['frame_bytes'])
        with self.request(state['frame_url'], method='HEAD') as response:
            self.assertEqual(response.headers['Content-Length'], str(len(data)))
            self.assertEqual(response.read(), b'')

    def test_unknown_frames_and_paths_cannot_read_local_files(self):
        for path in ['/v1/browser/frame/' + 'b' * 32 + '/1',
                     '/v1/browser/frame/' + self.session + '/2',
                     '/v1/browser/frame/../../state.sqlite',
                     '/v1/browser/frame/' + self.session + '/-1']:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    def test_stale_actions_and_unavailable_engine_are_distinct_errors(self):
        for action, expected in [('stale', 409), ('unavailable', 503)]:
            with self.subTest(action=action), self.assertRaises(HTTPError) as error:
                self.request('/v1/browser/action', {'action': action})
            self.assertEqual(error.exception.code, expected)
            self.assertIn('error', json.load(error.exception))
            error.exception.close()

    def test_server_close_releases_the_browser_process(self):
        with self.request('/v1/browser/action', {'action': 'open'}):
            pass
        self.server.server_close()
        self.assertTrue(self.closed)

    def test_pending_request_cannot_start_a_new_browser_after_server_close(self):
        accepted = threading.Event()
        get_request = self.server.get_request
        def observe_accept():
            result = get_request()
            accepted.set()
            return result
        self.server.get_request = observe_accept
        body = b'{"action":"open"}'
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as client:
            headers = ('POST /v1/browser/action HTTP/1.1\r\nHost: localhost\r\n'
                       'Authorization: Bearer ' + self.store.token + '\r\n'
                       'Content-Type: application/json\r\nContent-Length: ' + str(len(body)) + '\r\n\r\n')
            client.sendall(headers.encode())
            self.assertTrue(accepted.wait(timeout=3))
            self.server.shutdown()
            self.server.server_close()
            client.sendall(body)
            with client.makefile('rb') as reply:
                status = reply.readline()
                while reply.readline().strip():
                    pass
                reply.read()
        self.assertIn(b'503', status)
        self.factory.assert_not_called()
