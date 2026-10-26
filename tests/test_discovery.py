import hashlib
import hmac
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kobosync.server import make_server
from kobosync.store import Store


class DiscoveryTests(unittest.TestCase):
    NONCE = "0123456789abcdef0123456789abcdef"
    INSTANCE = "11223344556677889900aabbccddeeff"
    TEST_SECRET = "test-discovery-shared-secret"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(temporary.name)
        self.store.set_value("instance_id", self.INSTANCE)
        self.store.set_value("token", self.TEST_SECRET)
        self.server = make_server(self.store, "127.0.0.1", 0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, query=None, method="GET", path="/v1/discovery", headers=None):
        suffix = "?" + query if query is not None else ""
        request = Request(self.base + path + suffix, method=method, headers=headers or {})
        try:
            return urlopen(request, timeout=3)
        except HTTPError as error:
            return error

    def test_public_discovery_returns_exact_ascii_hmac_proof_without_secrets(self):
        with self.request("nonce=" + self.NONCE) as response:
            self.assertEqual(response.status, 200)
            body = response.read()
            self.assertEqual(response.headers["Content-Type"], "text/plain; charset=us-ascii")
            self.assertEqual(response.headers["Content-Length"], str(len(body)))
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        expected = ("kobosync-discovery-v1\n"
                    "0123456789abcdef0123456789abcdef\n"
                    "11223344556677889900aabbccddeeff\n"
                    "31d25ddf0043244d91e7550ef67f1ddef44a56dad3700031b9d0a62e5ae4f498\n")
        self.assertEqual(body, expected.encode("ascii"))
        self.assertNotIn(self.store.token.encode(), body)
        self.assertNotIn(b"Bearer", body)
        self.assertNotIn(b"tracks", body)

    def test_proof_binds_nonce_and_instance_to_paired_token(self):
        proofs = []
        for nonce in (self.NONCE, "f" * 32):
            with self.request("nonce=" + nonce) as response:
                self.assertEqual(response.status, 200)
                body = response.read().decode("ascii")
            version, received_nonce, instance, proof, ending = body.split("\n")
            self.assertEqual((version, received_nonce, instance, ending),
                             ("kobosync-discovery-v1", nonce, self.INSTANCE, ""))
            message = ("kobosync-discovery-v1\n" + nonce + "\n" + instance).encode("ascii")
            expected = hmac.new(self.TEST_SECRET.encode(), message, hashlib.sha256).hexdigest()
            self.assertTrue(hmac.compare_digest(proof, expected))
            self.assertFalse(hmac.compare_digest(proof,
                             hmac.new(b"different-pairing", message, hashlib.sha256).hexdigest()))
            proofs.append(proof)
        self.assertNotEqual(*proofs)

    def test_discovery_rejects_missing_malformed_duplicate_or_extra_query(self):
        invalid = (None, "", "nonce=", "nonce=" + "a" * 31, "nonce=" + "a" * 33,
                   "nonce=" + "A" * 32, "nonce=" + "g" * 32, "nonce=" + self.NONCE + "&nonce=" + self.NONCE,
                   "nonce=" + self.NONCE + "&extra=1", "extra=1&nonce=" + self.NONCE,
                   "nonce=" + self.NONCE + "&", "nonce=" + self.NONCE + ";extra=1")
        for query in invalid:
            with self.subTest(query=query):
                with self.request(query) as response:
                    self.assertEqual(response.status, 400)
                    self.assertNotIn(self.store.token.encode(), response.read())

    def test_head_reports_identical_length_without_response_body(self):
        with self.request("nonce=" + self.NONCE) as response:
            self.assertEqual(response.status, 200)
            expected_length = len(response.read())
        with self.request("nonce=" + self.NONCE, method="HEAD") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            self.assertEqual(int(response.headers["Content-Length"]), expected_length)
            self.assertEqual(response.headers["Content-Type"], "text/plain; charset=us-ascii")
        with self.request("nonce=invalid", method="HEAD") as response:
            self.assertEqual(response.status, 400)
            self.assertEqual(response.read(), b"")
            self.assertGreater(int(response.headers["Content-Length"]), 0)

    def test_discovery_does_not_relax_normal_data_authentication(self):
        protected = ("/v1/handoff", "/v1/prepare", "/v1/status", "/v1/audio/book/0",
                     "/v1/captions/book/" + "f" * 64)
        for path in protected:
            for headers in ({}, {"Authorization": "Bearer invalid"}):
                with self.subTest(path=path, headers=list(headers)):
                    with self.request(path=path, headers=headers) as response:
                        self.assertEqual(response.status, 401)
        for path in ("/v1/progress", "/v1/ready", "/v1/discovery"):
            with self.subTest(post=path):
                with self.request(path=path, method="POST") as response:
                    self.assertEqual(response.status, 401)
        with self.request("nonce=" + self.NONCE,
                          headers={"Authorization": "Bearer invalid"}) as response:
            self.assertEqual(response.status, 200)
        with self.request(path="/v1/status",
                          headers={"Authorization": "Bearer " + self.store.token}) as response:
            self.assertEqual(response.status, 200)
