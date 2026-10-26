import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from kobosync.store import Store


def make_epub(path, chapters=None, language="en-US", extra=None, spine=None):
    chapters = chapters or {"one": "<p>First chapter text.</p>", "two": "<p>Second chapter text.</p>"}
    spine = spine or list(chapters)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>')
        items = ''.join(f'<item id="{key}" href="{key}.xhtml" media-type="application/xhtml+xml"/>' for key in chapters)
        refs = ''.join(f'<itemref idref="{key}"/>' for key in spine)
        archive.writestr("OPS/package.opf", '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Morning Book</dc:title><dc:creator>A Writer</dc:creator><dc:language>' + language + '</dc:language></metadata><manifest>' + items + '</manifest><spine>' + refs + '</spine></package>')
        for key, body in chapters.items():
            archive.writestr(f"OPS/{key}.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Do not speak this title</title></head><body>' + body + '</body></html>')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


class NarrationTests(unittest.TestCase):
    def test_unused_images_do_not_count_toward_extracted_xml_budget(self):
        from kobosync.narration import read_epub
        source=self.root/'illustrated.epub'
        make_epub(source,extra={'cover.jpg':b'picture'*2000})
        with patch('kobosync.narration.MAX_ARCHIVE_BYTES',2000):
            self.assertEqual(read_epub(source)['chapters'],['First chapter text.','Second chapter text.'])

    def test_xml_resources_share_a_cumulative_extraction_limit(self):
        from kobosync.narration import read_epub
        source=self.root/'large-text.epub'
        make_epub(source)
        with patch('kobosync.narration.MAX_ARCHIVE_BYTES',500):
            with self.assertRaises(ValueError):
                read_epub(source)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_epub_uses_spine_order_and_visible_block_text(self):
        from kobosync.narration import read_epub
        source = self.root / "book.epub"
        make_epub(source, {"one": '<h1>Start</h1><p>Hello\u00a0\u2009world <em>again</em>.</p><p hidden="hidden">Secret</p><span aria-hidden="true">Invisible</span><div style="display: none">Hidden CSS</div><script>bad()</script><style>bad</style><p>Finish<br/>Here</p>', "two": '<p>Second chapter.</p>'}, spine=["two", "one"])
        self.assertEqual(read_epub(source), dict(title="Morning Book", author="A Writer", language="en-US", chapters=["Second chapter.", "Start Hello world again. Finish Here"]))

    def test_non_linear_spine_items_are_not_narrated(self):
        from kobosync.narration import read_epub
        source = self.root / "book.epub"
        make_epub(source)
        with zipfile.ZipFile(source) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["OPS/package.opf"] = entries["OPS/package.opf"].replace(b'<itemref idref="two"/>', b'<itemref idref="two" linear="no"/>')
        with zipfile.ZipFile(source, "w") as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        self.assertEqual(read_epub(source)["chapters"], ["First chapter text."])

    def test_protected_epub_is_rejected_clearly(self):
        from kobosync.narration import read_epub
        source = self.root / "book.epub"
        make_epub(source, extra={"META-INF/encryption.xml": '<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></EncryptedData></encryption>'})
        with self.assertRaisesRegex(ValueError, "DRM|protected"):
            read_epub(source)

    def test_extraction_rejects_oversized_chapter_text(self):
        from kobosync.narration import read_epub
        source = self.root / "book.epub"
        make_epub(source)
        with patch("kobosync.narration.MAX_CHAPTER_CHARACTERS", 10):
            with self.assertRaisesRegex(ValueError, "chapter.*(large|limit)|[Oo]versized.*chapter"):
                read_epub(source)

    def test_malformed_xml_reports_an_invalid_epub(self):
        from kobosync.narration import read_epub
        source = self.root / "book.epub"
        make_epub(source, {"one": "<p>Unclosed chapter"})
        with self.assertRaisesRegex(ValueError, "[Ii]nvalid.*XML"):
            read_epub(source)

    def test_empty_chapters_still_count_toward_spine_limits(self):
        from kobosync.narration import read_epub
        source = self.root / "book.epub"
        make_epub(source, {"one": "", "two": "<p>Second</p>"})
        with patch("kobosync.narration.MAX_CHAPTERS", 1):
            with self.assertRaisesRegex(ValueError, "(chapter|spine).*limit"):
                read_epub(source)

    def inbox(self, language="en-US"):
        source = self.root / "source.epub"
        make_epub(source, language=language)
        md5 = hashlib.md5(source.read_bytes()).hexdigest()
        store = Store(self.root / "data")
        target = store.root / "inbox" / md5 / "book.epub"
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        return store, md5

    def transcriber(self, store, book_id, **kwargs):
        store.save_transcript(book_id, [dict(word="First", start=0, end=.5, track=0), dict(word="Second", start=0, end=.5, track=1)])
        return 2

    def test_retry_reuses_finished_chapters_and_registered_audio(self):
        from kobosync.narration import prepare_book
        store, md5 = self.inbox()
        spoken = []

        def interrupted(text, destination):
            spoken.append(text)
            if text.startswith("Second"):
                raise OSError("temporary speech interruption")
            Path(destination).write_bytes(b"audio:" + text.encode())

        with self.assertRaisesRegex(OSError, "interruption"):
            prepare_book(store, md5, synthesize=interrupted, transcribe=self.transcriber)
        self.assertEqual(store.books(), [])

        def synthesize(text, destination):
            spoken.append(text)
            Path(destination).write_bytes(b"audio:" + text.encode())

        book = prepare_book(store, md5, synthesize=synthesize, transcribe=self.transcriber)
        self.assertEqual(book["id"], "auto-" + md5)
        self.assertEqual(book["epub_md5"], md5)
        self.assertEqual(book["title"], "Morning Book")
        self.assertEqual(len(book["tracks"]), 2)
        self.assertEqual(spoken, ["First chapter text.", "Second chapter text.", "Second chapter text."])
        self.assertEqual(len(store.transcript(book["id"])), 2)
        unchanged = prepare_book(store, md5, synthesize=lambda *_: self.fail("registered book synthesized again"), transcribe=lambda *_: self.fail("completed transcript repeated"))
        self.assertEqual(unchanged, book)

    def test_retry_after_transcription_failure_preserves_registered_audio(self):
        from kobosync.narration import prepare_book
        store, md5 = self.inbox()

        def synthesize(text, destination):
            Path(destination).write_bytes(text.encode())

        def unavailable(*_, **__):
            raise RuntimeError("recognizer unavailable")

        with self.assertRaisesRegex(RuntimeError, "recognizer unavailable"):
            prepare_book(store, md5, synthesize=synthesize, transcribe=unavailable)
        book = prepare_book(store, md5, synthesize=lambda *_: self.fail("audio regenerated"), transcribe=self.transcriber)
        self.assertEqual(len(store.transcript(book["id"])), 2)

    def test_rejects_non_english_narration_without_generating_audio(self):
        from kobosync.narration import prepare_book
        store, md5 = self.inbox(language="fr")
        with self.assertRaisesRegex(ValueError, "English"):
            prepare_book(store, md5, synthesize=lambda *_: self.fail("unsupported narration generated"), transcribe=self.transcriber)
        self.assertEqual(store.books(), [])

    def test_source_hash_must_match_requested_edition(self):
        from kobosync.narration import prepare_book
        store, md5 = self.inbox()
        (store.root / "inbox" / md5 / "book.epub").write_bytes(b"changed edition")
        with self.assertRaisesRegex(ValueError, "hash|edition"):
            prepare_book(store, md5, synthesize=lambda *_: self.fail("changed edition generated"), transcribe=self.transcriber)

    def test_modified_chapter_cache_is_regenerated(self):
        from kobosync.narration import prepare_book
        for corruption in ("text", "audio"):
            with self.subTest(corruption=corruption):
                case = self.root / corruption
                case.mkdir()
                source = case / "source.epub"
                make_epub(source)
                md5 = hashlib.md5(source.read_bytes()).hexdigest()
                store = Store(case / "data")
                target = store.root / "inbox" / md5 / "book.epub"
                target.parent.mkdir(parents=True)
                target.write_bytes(source.read_bytes())

                def interrupted(text, destination):
                    if text.startswith("Second"):
                        raise OSError("interrupted")
                    Path(destination).write_bytes(b"first cached audio")

                with self.assertRaises(OSError):
                    prepare_book(store, md5, synthesize=interrupted, transcribe=self.transcriber)
                stage = store.root / "generated" / md5
                if corruption == "text":
                    metadata = stage / "0000.json"
                    data = json.loads(metadata.read_text())
                    data["key"]["text_sha256"] = "wrong previous chapter"
                    metadata.write_text(json.dumps(data))
                else:
                    (stage / "0000.mp3").write_bytes(b"corrupted audio")

                def synthesize(text, destination):
                    Path(destination).write_bytes(("fresh:" + text).encode())

                book = prepare_book(store, md5, synthesize=synthesize, transcribe=self.transcriber)
                self.assertEqual(store.asset(book["id"], 0).read_bytes(), b"fresh:First chapter text.")

    def test_voice_download_is_verified_and_reused(self):
        from kobosync.narration import VOICE, ensure_voice
        payloads = {VOICE + ".onnx": b"verified voice", VOICE + ".onnx.json": b'{"voice": true}'}
        files = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in payloads.items()}

        def fetch(request, **kwargs):
            return io.BytesIO(payloads[request.full_url.rsplit("/", 1)[1]])

        with patch("kobosync.narration.VOICE_FILES", files), patch("kobosync.narration.urlopen", fetch):
            model = ensure_voice(self.root / "models")
        self.assertEqual(model.read_bytes(), b"verified voice")
        self.assertEqual(model.with_suffix(".onnx.json").read_bytes(), b'{"voice": true}')
        with patch("kobosync.narration.VOICE_FILES", files), patch("kobosync.narration.urlopen", side_effect=AssertionError("cached voice downloaded twice")):
            self.assertEqual(ensure_voice(self.root / "models"), model)

    def test_failed_voice_integrity_never_publishes_partial_model(self):
        from kobosync.narration import VOICE, ensure_voice
        files = {VOICE + ".onnx": (4, hashlib.sha256(b"good").hexdigest())}
        with patch("kobosync.narration.VOICE_FILES", files), patch("kobosync.narration.urlopen", return_value=io.BytesIO(b"evil")):
            with self.assertRaisesRegex(ValueError, "integrity"):
                ensure_voice(self.root / "models")
        self.assertEqual(list((self.root / "models" / "piper").iterdir()), [])

    def test_speech_chunks_bound_even_unbroken_text_without_losing_words(self):
        from kobosync.narration import speech_chunks
        with patch("kobosync.narration.SPEECH_CHUNK_CHARACTERS", 16):
            chunks = list(speech_chunks("First sentence. Second sentence. " + "x" * 40))
        self.assertTrue(all(0 < len(chunk) <= 16 for chunk in chunks))
        self.assertEqual(" ".join(chunks[:2]), "First sentence. Second sentence.")
        self.assertEqual("".join(chunks[2:]), "x" * 40)


if __name__ == "__main__":
    unittest.main()
