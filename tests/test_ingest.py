import fcntl
import hashlib
import io
import json
import tempfile
import sqlite3
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from test_core import TEXT, make_epub, words
from kobosync.store import Store


class ImportFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.epub = self.root / 'book.epub'
        make_epub(self.epub)
        self.body = self.epub.read_bytes()
        self.md5 = hashlib.md5(self.body).hexdigest()
        self.audio = self.root / 'book.mp3'
        self.audio.write_bytes(b'test audio bytes')

    def observation(self, md5=None, content_id='Same.epub', sequence=1):
        return dict(reader='koreader', content_id=content_id, epub_md5=md5 or self.md5,
                    passage=TEXT, locator='/body/p[1]', source_time='2026-10-18T04:00:00Z', sequence=sequence)

    def upload(self, body=None, md5=None):
        from kobosync.ingest import receive_epub
        body = self.body if body is None else body
        return receive_epub(self.store, md5 or self.md5, io.BytesIO(body), len(body))

    def generate(self, store, md5):
        book = store.book('auto-' + md5, required=False)
        if book is None:
            book = store.register('auto-' + md5, 'auto-' + md5,
                                  store.root / 'inbox' / md5 / 'book.epub', [self.audio], 'Generated')
        store.save_transcript(book['id'], words())
        return book

class ImportTests(ImportFixture, unittest.TestCase):
    def test_illustrated_epub_larger_than_64_mib_uploads_without_loading_images(self):
        from kobosync import ingest
        from kobosync.store import digest
        large=self.root/'illustrated.epub'
        with zipfile.ZipFile(self.epub) as original, zipfile.ZipFile(large,'w') as archive:
            for item in original.infolist():
                archive.writestr(item,original.read(item))
            with archive.open('large-illustration.bin','w') as image:
                block=b'\0'*262144
                remaining=85239102
                while remaining:
                    part=block[:min(len(block),remaining)]
                    image.write(part)
                    remaining-=len(part)
        with large.open('rb') as stream:
            result=ingest.receive_epub(self.store,digest(large,'md5'),stream,large.stat().st_size)
        self.assertEqual(result['book_state'],'queued')

    def test_registration_recovers_after_rename_before_database_commit(self):
        real_rename = Path.rename
        destination = self.store.root / 'library' / ('auto-' + self.md5)
        def interrupted(path, target):
            result = real_rename(path, target)
            if target == destination:
                raise RuntimeError('crash after rename before commit')
            return result
        with patch.object(Path, 'rename', interrupted):
            with self.assertRaisesRegex(RuntimeError, 'crash after rename'):
                self.store.register('auto-' + self.md5, 'Crash.epub', self.epub, [self.audio], 'Recovered')
        self.assertIsNone(self.store.book('auto-' + self.md5, required=False))
        self.assertTrue(destination.is_dir())
        recovered = Store(self.store.root).register('auto-' + self.md5, 'Crash.epub', self.epub, [self.audio], 'Recovered')
        self.assertEqual(recovered['epub_md5'], self.md5)
        self.assertEqual(self.store.asset(recovered['id'], 0).read_bytes(), self.audio.read_bytes())

    def test_unknown_position_survives_reopen_and_requests_upload(self):
        from kobosync import ingest
        self.store.observe(self.observation())
        reopened = Store(self.store.root)
        self.assertEqual(reopened.observation()['epub_md5'], self.md5)
        status = ingest.progress_status(reopened, reopened.observation())
        self.assertTrue(status['needs_epub'])
        self.assertEqual(status['book_state'], 'awaiting_epub')
        self.assertTrue(status['message'])
        self.assertEqual(ingest.jobs(reopened)[0]['md5'], self.md5)

    def test_malformed_hash_is_rejected_before_persistence(self):
        with self.assertRaises(ValueError):
            self.store.observe(self.observation('../not-an-edition'))
        self.assertEqual(self.store.history(), [])

    def test_same_filename_uses_exact_edition_and_rename_uses_same_audio(self):
        from kobosync.pipeline import prepare, run_once
        original = self.store.register('human', 'Same.epub', self.epub, [self.audio], 'Human')
        self.store.save_transcript('human', words())
        self.store.observe(self.observation(content_id='Renamed.epub'))
        self.assertEqual(prepare(self.store)['book_id'], 'human')
        self.assertEqual(run_once(self.store)['book_id'], 'human')
        other = self.root / 'other.epub'
        make_epub(other)
        with zipfile.ZipFile(other, 'a') as archive:
            archive.writestr('extra', 'A different edition')
        second = self.store.register('second', 'Same.epub', other, [self.audio], 'Second')
        self.store.save_transcript('second', words())
        self.store.observe(self.observation(second['epub_md5'], sequence=2))
        self.assertEqual(prepare(self.store)['book_id'], 'second')
        self.assertEqual(self.store.find_book('Renamed.epub', original['epub_md5'])['id'], 'human')
        with self.assertRaises(ValueError):
            self.store.find_book('Same.epub', 'f' * 32)

    def test_upload_is_durable_idempotent_and_ready_only_after_transcription(self):
        from kobosync import ingest
        from kobosync.pipeline import prepare, run_once
        self.store.observe(self.observation())
        result = self.upload()
        self.assertFalse(result['needs_epub'])
        self.assertEqual(result['book_state'], 'queued')
        self.assertEqual((self.store.root / 'inbox' / self.md5 / 'book.epub').read_bytes(), self.body)
        before = ingest.jobs(self.store)
        self.upload()
        self.assertEqual(ingest.jobs(self.store), before)
        with self.assertRaises(ValueError):
            prepare(self.store)
        ingest.process_pending(self.store, self.generate)
        self.assertEqual(ingest.progress_status(self.store, self.observation())['book_state'], 'ready')
        self.assertEqual(prepare(self.store)['book_id'], 'auto-' + self.md5)
        self.assertEqual(run_once(self.store)['position']['seconds'], 10)
        ready = ingest.jobs(self.store)
        self.upload()
        ingest.process_pending(self.store, lambda *_: self.fail('ready edition must not regenerate'))
        self.assertEqual(ingest.jobs(self.store), ready)

    def test_hash_size_truncation_and_non_epub_fail_without_queueing(self):
        from kobosync import ingest
        for body, md5, length in [(self.body, 'f' * 32, len(self.body)),
                                  (self.body[:-1], self.md5, len(self.body)),
                                  (b'not an EPUB', hashlib.md5(b'not an EPUB').hexdigest(), 11),
                                  (self.body, self.md5, 256 * 1024 * 1024 + 1),
                                  (self.body, self.md5, 0)]:
            with self.subTest(length=length, md5=md5), self.assertRaises(ValueError):
                ingest.receive_epub(self.store, md5, io.BytesIO(body), length)
        self.assertEqual(ingest.jobs(self.store), [])
        self.assertEqual(list((self.store.root / 'inbox').rglob('book.epub')), [])

    def test_generation_failure_keeps_old_handoff_and_retries_after_restart(self):
        from kobosync import ingest
        from kobosync.pipeline import prepare, run_once
        self.store.register('human', 'Human.epub', self.epub, [self.audio], 'Human')
        self.store.save_transcript('human', words())
        self.store.observe(self.observation())
        previous = run_once(self.store)
        other = self.body + b'new edition'
        self.body, self.md5 = other, hashlib.md5(other).hexdigest()
        self.store.observe(self.observation(sequence=2))
        self.upload()
        def interrupted(store, md5):
            store.register('auto-' + md5, 'auto-' + md5,
                           store.root / 'inbox' / md5 / 'book.epub', [self.audio], 'Pending')
            raise RuntimeError('transcription unavailable')
        ingest.process_pending(self.store, interrupted)
        status = ingest.jobs(self.store)[0]
        self.assertEqual(status['book_state'], 'error')
        self.assertIn('transcription unavailable', status['error'])
        self.assertIsNotNone(status['next_attempt_at'])
        self.assertEqual(self.store.latest(), previous)
        with self.assertRaises(ValueError):
            prepare(self.store)
        ingest.process_pending(self.store, lambda *_: self.fail('retry must wait for backoff'))
        # Simulate a worker killed while preparing: a new worker must reclaim it.
        with self.store.connect() as connection:
            record = dict(status, book_state='preparing')
            connection.execute('UPDATE imports SET data=? WHERE md5=?', (json.dumps(record), self.md5))
        ingest.process_pending(Store(self.store.root), self.generate)
        self.assertEqual(ingest.jobs(self.store)[0]['book_state'], 'ready')
        self.assertEqual(prepare(self.store)['book_id'], 'auto-' + self.md5)

    def test_worker_process_lock_prevents_double_generation(self):
        from kobosync import ingest
        self.upload()
        with (self.store.root / 'import.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ingest.process_pending(self.store, lambda *_: self.fail('another worker owns import'))
        self.assertEqual(ingest.jobs(self.store)[0]['book_state'], 'queued')

    def test_registered_human_edition_upload_does_not_create_a_generation_job(self):
        from kobosync import ingest
        self.store.register('human', 'Original.epub', self.epub, [self.audio], 'Human')
        result = self.upload()
        self.assertEqual(result['book_state'], 'ready')
        self.assertEqual(ingest.jobs(self.store), [])

    def test_existing_filename_unique_schema_migrates_without_losing_pairing_or_progress(self):
        legacy = self.root / 'legacy'
        legacy.mkdir()
        with sqlite3.connect(legacy / 'state.sqlite') as connection:
            connection.execute('CREATE TABLE books (id TEXT PRIMARY KEY, content_id TEXT UNIQUE, data TEXT)')
        store = Store(legacy)
        token = store.token
        first = store.register('first', 'Same.epub', self.epub, [self.audio], 'First')
        store.observe(self.observation())
        reopened = Store(legacy)
        reopened.register('second', 'Same.epub', self.epub, [self.audio], 'Second')
        self.assertEqual(reopened.token, token)
        self.assertEqual(reopened.observation()['epub_md5'], first['epub_md5'])
        self.assertEqual(len(reopened.books()), 2)

    def test_reupload_replaces_corrupt_canonical_bytes(self):
        self.upload()
        canonical = self.store.root / 'inbox' / self.md5 / 'book.epub'
        canonical.write_bytes(b'interrupted or changed input')
        self.upload()
        self.assertEqual(canonical.read_bytes(), self.body)

    def test_different_bytes_cannot_reuse_an_existing_md5_identity(self):
        self.upload()
        different = self.body + b'changed bytes with a simulated MD5 collision'
        # The hash implementation is the boundary here: simulate a collision
        # while preserving real file upload, EPUB validation and queue storage.
        with patch('kobosync.ingest.hashlib.md5') as checksum:
            checksum.return_value.hexdigest.return_value = self.md5
            with self.assertRaisesRegex(ValueError, 'different|Different'):
                self.upload(different)
        self.assertEqual((self.store.root / 'inbox' / self.md5 / 'book.epub').read_bytes(), self.body)


class ImportApiTests(ImportFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from kobosync.server import make_server
        self.server = make_server(self.store, '127.0.0.1', 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, body=None, auth=True):
        headers = {'Content-Type': 'application/octet-stream'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.store.token
        return urlopen(Request(self.base + path, data=body, headers=headers), timeout=3)

    def test_authenticated_progress_upload_and_status_contract(self):
        with self.request('/v1/progress', json.dumps(self.observation()).encode()) as response:
            result = json.load(response)
        self.assertIn('observation', result)
        self.assertTrue(result['needs_epub'])
        with self.request('/v1/ebooks/' + self.md5, self.body) as response:
            self.assertIn(response.status, (200, 202))
            self.assertEqual(json.load(response)['book_state'], 'queued')
        with self.request('/v1/status') as response:
            status = json.load(response)
        self.assertEqual(status['imports'][0]['md5'], self.md5)
        self.assertEqual(self.store.observation()['locator'], '/body/p[1]')

    def test_upload_auth_hash_and_json_errors_have_http_responses(self):
        for path, body, auth, code in [('/v1/ebooks/' + self.md5, self.body, False, 401),
                                      ('/v1/ebooks/' + 'f' * 32, self.body, True, 400),
                                      ('/v1/ebooks/invalid', self.body, True, 400),
                                      ('/v1/progress', b'[]', True, 400),
                                      ('/v1/progress', b'null', True, 400),
                                      ('/v1/progress', b'{', True, 400)]:
            with self.subTest(path=path, body=body[:20]):
                with self.assertRaises(HTTPError) as error:
                    self.request(path, body, auth)
                self.assertEqual(error.exception.code, code)
                self.assertIn('error', json.load(error.exception))

    def test_generated_incomplete_book_is_not_advertised_and_progress_is_nonblocking(self):
        from kobosync import ingest
        self.store.observe(self.observation())
        self.upload()
        started, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        def delayed(store, md5):
            book = store.register('auto-' + md5, 'auto-' + md5,
                                  store.root / 'inbox' / md5 / 'book.epub', [self.audio], 'Pending')
            started.set()
            if not finish.wait(5):
                raise RuntimeError('test worker release timed out')
            store.save_transcript(book['id'], words())
            return book
        worker = threading.Thread(target=ingest.process_pending, args=(self.store, delayed))
        worker.start()
        self.addCleanup(worker.join, 5)
        self.assertTrue(started.wait(3))
        with self.request('/v1/prepare') as response:
            self.assertEqual(response.status, 204)
        with self.request('/v1/progress', json.dumps(self.observation(sequence=2)).encode()) as response:
            self.assertEqual(json.load(response)['book_state'], 'preparing')
        finish.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        with self.request('/v1/prepare') as response:
            self.assertTrue(json.load(response)['captions'])
