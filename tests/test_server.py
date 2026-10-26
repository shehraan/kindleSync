import hashlib
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from test_core import TEXT
import test_pipeline


class ServerTests(unittest.TestCase):
    def setUp(self):
        test_pipeline.PipelineTests.setUp(self)
        from kobosync.server import make_server
        self.server = make_server(self.store, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, data=None, **headers):
        return urlopen(Request(self.base + path, data=json.dumps(data).encode() if data is not None else None,
                               headers={"Authorization": "Bearer " + self.store.token, "Content-Type": "application/json", **headers}), timeout=3)

    def test_auth_is_required_for_book_data(self):
        with self.assertRaises(HTTPError) as e:
            urlopen(self.base + "/v1/handoff")
        self.assertEqual(e.exception.code, 401)

    def test_health_identifies_the_persistent_store_without_pairing_secret(self):
        from kobosync.store import Store
        with urlopen(self.base + "/health") as r:
            health = json.load(r)
        self.assertEqual(health["service"], "kobosync")
        self.assertRegex(health["instance_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(Store(self.store.root).value("instance_id"), health["instance_id"])
        self.assertNotEqual(Store(self.store.root / "other").value("instance_id"), health["instance_id"])
        self.assertNotIn(self.store.token, json.dumps(health))

    def test_phone_can_download_and_verify_entire_handoff(self):
        from kobosync.pipeline import run_once
        handoff = run_once(self.store, self.db)
        with self.request("/v1/handoff") as r:
            manifest = json.load(r)
        self.assertEqual(manifest["revision"], handoff["revision"])
        t = manifest["tracks"][0]
        with self.request(t["url"]) as r:
            audio = r.read()
        self.assertEqual(hashlib.sha256(audio).hexdigest(), t["sha256"])
        with self.request("/v1/ready", {"revision": manifest["revision"]}) as r:
            self.assertEqual(r.status, 200)

    def test_byte_ranges_resume_downloads(self):
        with self.request("/v1/audio/book/0", Range="bytes=5-9") as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.read(), b"audio")
            self.assertEqual(r.headers["Content-Range"], "bytes 5-9/16")

    def test_out_of_bounds_range_is_rejected(self):
        with self.assertRaises(HTTPError) as e:
            self.request("/v1/audio/book/0", Range="bytes=99-")
        self.assertEqual(e.exception.code, 416)

    def test_koreader_progress_arrives_before_morning(self):
        from kobosync.pipeline import run_once
        book = self.store.book("book")
        observation = dict(reader="koreader", content_id=book["content_id"], epub_md5=book["epub_md5"],
                           locator="/body/DocFragment[1]/body/p[2]", passage=TEXT,
                           source_time="2026-10-18T04:00:00Z")
        with self.request("/v1/progress", observation) as r:
            self.assertEqual(r.status, 200)
        self.assertIsNone(self.store.latest())
        with self.request("/v1/prepare") as r:
            self.assertEqual(json.load(r)["book_id"], "book")
        result = run_once(self.store)
        self.assertEqual(result["position"]["seconds"], 10)

    def test_koreader_wrong_edition_is_rejected(self):
        observation = dict(reader="koreader", content_id="file:///mnt/onboard/Book.epub", epub_md5="wrong",
                           locator="/somewhere", passage=TEXT, source_time="2026-10-18T04:00:00Z")
        with self.assertRaises(HTTPError) as e:
            self.request("/v1/progress", observation)
        self.assertEqual(e.exception.code, 400)

    def test_unknown_asset_cannot_read_arbitrary_files(self):
        with self.assertRaises(HTTPError) as e:
            self.request("/v1/audio/../0")
        self.assertEqual(e.exception.code, 404)
