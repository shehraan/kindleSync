import hashlib
import html
import json
import re
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_pipeline
from test_core import TEXT


def parsed_cues(data):
    text = data.decode("utf-8")
    if not text.startswith("WEBVTT\n\n"):
        raise AssertionError("A caption asset must be UTF-8 WebVTT")
    result = []
    for block in text[8:].strip().split("\n\n"):
        if not block:
            continue
        timing, content = block.split("\n", 1)
        times = []
        for timestamp in timing.split(" --> "):
            if not re.fullmatch(r"\d{2,}:\d{2}:\d{2}\.\d{3}", timestamp):
                raise AssertionError("WebVTT needs millisecond timestamps")
            hours, minutes, seconds = timestamp.split(":")
            times.append(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
        result.append((*times, html.unescape(content)))
    return result


class CaptionHTTPTests(unittest.TestCase):
    def setUp(self):
        test_pipeline.PipelineTests.setUp(self)
        from kobosync.server import make_server
        self.server = make_server(self.store, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        book = self.store.book("book")
        self.store.observe(dict(reader="koreader", content_id=book["content_id"],
                                epub_md5=book["epub_md5"], locator="page1", passage=TEXT,
                                source_time="2026-10-18T04:00:00Z"))

    def request(self, path, method="GET", authenticated=True, **headers):
        if authenticated:
            headers.setdefault("Authorization", "Bearer " + self.store.token)
        return urlopen(Request(self.base + path, method=method, headers=headers), timeout=3)

    def manifest(self, path="/v1/prepare"):
        with self.request(path) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def download(self, entry):
        with self.request(entry["url"]) as response:
            data = response.read()
        self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(entry["bytes"], len(data))
        return data

    def test_captions_cover_narrator_intro_every_word_and_late_track_speech(self):
        narration = "Narrated by María & friends. <Chapter> one begins with all these words preserved exactly."
        tokens = narration.split()
        words = [dict(word=word, start=i * .3, end=i * .3 + .2, track=0)
                 for i, word in enumerate(tokens)]
        words.extend([dict(word="An", start=3680.123, end=3680.523, track=0),
                      dict(word="ending.", start=3680.6, end=3681.001, track=0)])
        self.store.save_transcript("book", words)
        manifest = self.manifest()
        self.assertIn("captions", manifest, "A full transcript must produce downloadable captions")
        self.assertEqual([entry["track"] for entry in manifest["captions"]], [0])
        entry = manifest["captions"][0]
        self.assertRegex(entry["url"], r"^/v1/captions/book/[0-9a-f]{64}$")
        data = self.download(entry)
        self.assertIn(b"&amp;", data)
        self.assertIn(b"&lt;Chapter&gt;", data)
        self.assertIn(b"01:01:20.123", data)
        cues = parsed_cues(data)
        self.assertEqual(" ".join(cue[2] for cue in cues), narration + " An ending.")
        self.assertTrue(all(0 < end-start <= 4.001 and len(text.split()) <= 12
                            for start, end, text in cues))
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(cues, cues[1:])))
        self.assertTrue(any(end < 10 and next_start > 3600
                            for (_, end, _), (next_start, _, _) in zip(cues, cues[1:])))

    def test_overlapping_and_zero_duration_words_have_ordered_positive_cues(self):
        words = [dict(word=f"word{i}", start=2, end=2 if i % 2 else 8, track=0)
                 for i in range(28)]
        words.append(dict(word="after.", start=3, end=3, track=0))
        self.store.save_transcript("book", words)
        manifest = self.manifest()
        self.assertIn("captions", manifest)
        cues = parsed_cues(self.download(manifest["captions"][0]))
        self.assertEqual(" ".join(c[2] for c in cues).split(), [w["word"] for w in words])
        self.assertTrue(all(start < end for start, end, _ in cues))
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(cues, cues[1:])))

    def test_each_track_has_an_asset_even_when_speechless(self):
        self.store.register("multi", "file:///Multi.epub", self.epub,
                            [self.audio, self.audio, self.audio], "Multi")
        self.store.save_transcript("multi", [dict(word="Opening.", start=.1, end=.5, track=0),
                                               dict(word="Finale.", start=1, end=2, track=2)])
        book = self.store.book("multi")
        self.store.observe(dict(reader="koreader", content_id=book["content_id"],
                                epub_md5=book["epub_md5"], locator="page1", passage=TEXT,
                                source_time="2026-10-18T05:00:00Z"))
        manifest = self.manifest()
        self.assertIn("captions", manifest)
        self.assertEqual([c["track"] for c in manifest["captions"]], [0, 1, 2])
        self.assertEqual(self.download(manifest["captions"][1]), b"WEBVTT\n\n")
        self.assertEqual(parsed_cues(self.download(manifest["captions"][2]))[0][2], "Finale.")

    def test_assets_require_authentication_and_reject_unknown_or_unsafe_paths(self):
        manifest = self.manifest()
        self.assertIn("captions", manifest)
        url = manifest["captions"][0]["url"]
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with self.subTest(headers=list(headers)):
                with self.assertRaises(HTTPError) as raised:
                    self.request(url, authenticated=False, **headers)
                self.assertEqual(raised.exception.code, 401)
        for path in ("/v1/captions/book/" + "0" * 64,
                     "/v1/captions/unregistered/" + manifest["captions"][0]["sha256"],
                     "/v1/captions/../" + "0" * 64,
                     "/v1/captions/%2e%2e/" + "0" * 64,
                     url + ".vtt", url + "/../transcript.json"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as raised:
                    self.request(path)
                self.assertEqual(raised.exception.code, 404)

    def test_caption_assets_support_head_and_resumable_ranges(self):
        manifest = self.manifest()
        self.assertIn("captions", manifest)
        entry = manifest["captions"][0]
        data = self.download(entry)
        with self.request(entry["url"], method="HEAD") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            self.assertEqual(response.headers["Content-Length"], str(len(data)))
            self.assertEqual(response.headers["ETag"], '"' + entry["sha256"] + '"')
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertTrue(response.headers["Content-Type"].startswith("text/vtt"))
        for requested, expected_start, expected_end in (("bytes=5-9", 5, 9),
                                                         ("bytes=5-", 5, len(data)-1),
                                                         ("bytes=-4", len(data)-4, len(data)-1)):
            with self.subTest(requested=requested):
                with self.request(entry["url"], Range=requested) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), data[expected_start:expected_end+1])
                    self.assertEqual(response.headers["Content-Range"],
                                     f"bytes {expected_start}-{expected_end}/{len(data)}")
        with self.request(entry["url"], method="HEAD", Range="bytes=5-9") as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b"")
            self.assertEqual(response.headers["Content-Length"], "5")
        for requested in ("bytes=999999-", "bytes=-0", "bytes=9-5", "bytes=1-2,4-5", "bytes=-"):
            with self.subTest(requested=requested):
                with self.assertRaises(HTTPError) as raised:
                    self.request(entry["url"], Range=requested)
                self.assertEqual(raised.exception.code, 416)
                self.assertEqual(raised.exception.headers["Content-Range"], f"bytes */{len(data)}")

    def test_handoff_enrichment_preserves_publication_and_legacy_revision(self):
        from kobosync.pipeline import run_once
        published = run_once(self.store)
        self.assertNotIn("captions", published)
        with self.store.connect() as connection:
            original_rows = list(connection.execute("SELECT * FROM handoffs"))
        served = self.manifest("/v1/handoff")
        self.assertIn("captions", served)
        self.assertEqual({k:v for k,v in served.items() if k != "captions"}, published)
        self.assertEqual(self.store.latest(), published)
        with self.store.connect() as connection:
            self.assertEqual(list(connection.execute("SELECT * FROM handoffs")), original_rows)
        legacy = dict(published, revision="older-client-publication", position={"track": 0, "seconds": 42.5})
        self.store.publish(legacy)
        served = self.manifest("/v1/handoff")
        self.assertEqual(served["revision"], "older-client-publication")
        self.assertEqual(served["position"], {"track": 0, "seconds": 42.5})
        self.assertIn("captions", served)
        self.assertEqual(self.store.latest(), legacy)

    def test_retranscription_changes_caption_hash_and_keeps_old_assets(self):
        first_manifest = self.manifest()
        self.assertIn("captions", first_manifest)
        first = first_manifest["captions"][0]
        data = self.download(first)
        self.assertEqual(self.manifest()["captions"], first_manifest["captions"])
        self.store.save_transcript("book", [dict(word="Replacement narration.", start=0, end=2, track=0)])
        changed = self.manifest()["captions"][0]
        self.assertNotEqual(changed["sha256"], first["sha256"])
        self.assertEqual(self.download(first), data)
        self.assertIn(b"Replacement narration.", self.download(changed))
        asset_dir = self.store.root / "library" / "book" / "captions"
        self.assertEqual({p.name for p in asset_dir.iterdir()},
                         {first["sha256"] + ".vtt", changed["sha256"] + ".vtt"})

    def test_preparation_without_transcript_still_preloads_audio(self):
        (self.store.root / "library" / "book" / "transcript.json").unlink()
        manifest = self.manifest()
        self.assertNotIn("captions", manifest)
        self.assertEqual(manifest["book_id"], "book")
        self.assertEqual(self.download(manifest["tracks"][0]), b"test audio bytes")

    def test_invalid_transcript_is_not_served_as_trusted_captions(self):
        path = self.store.root / "library" / "book" / "transcript.json"
        data = json.loads(path.read_text())
        data["words"] = [dict(word="bad", start=2, end=1, track=0)]
        path.write_text(json.dumps(data))
        self.assertNotIn("captions", self.manifest())

    def test_unreadable_transcript_does_not_break_audio_preloading(self):
        path = self.store.root / "library" / "book" / "transcript.json"
        path.write_bytes(b"\xff\xfe")
        manifest = self.manifest()
        self.assertNotIn("captions", manifest)
        self.assertEqual(self.download(manifest["tracks"][0]), b"test audio bytes")
