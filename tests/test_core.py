import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path


TEXT = "The morning sunlight touched the windows while Clara quietly opened the heavy wooden door and stepped into the silent garden beyond the old house."


def make_epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        z.writestr("OEBPS/book.opf", '<package><manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("OEBPS/ch1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Earlier words.</p><p id="kobo.2.1">' + TEXT + '</p></body></html>')


def make_db(path, locator="OEBPS/ch1.xhtml#kobo.2.1"):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE content (ContentID TEXT, BookID TEXT, ContentType INTEGER, Title TEXT, Attribution TEXT, ChapterIDBookmarked TEXT, DateLastRead TEXT, ReadStatus INTEGER, ___PercentRead REAL)')
        c.execute('INSERT INTO content VALUES (?,?,?,?,?,?,?,?,?)', ("file:///mnt/onboard/Book.epub", None, 6, "Book", "Writer", locator, "2026-10-18T03:45:00Z", 1, 42))
        c.execute('INSERT INTO content VALUES (?,?,?,?,?,?,?,?,?)', ("chapter", "file:///mnt/onboard/Book.epub", 9, "Chapter", None, None, "2026-10-19T00:00:00Z", 1, 90))


def words(text=TEXT, track=0, offset=10):
    return [dict(word=w, track=track, start=offset+i*.5, end=offset+(i+1)*.5) for i,w in enumerate(text.split())]


class CoreTests(unittest.TestCase):
    def test_safe_xhtml_doctypes_are_allowed_without_fetching_external_dtds(self):
        from kobosync.epub import xml
        for declaration in [b'<!DOCTYPE html>', b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">']:
            for encoding in ['utf-8', 'utf-16']:
                data = (declaration + b'<html><body>Safe text</body></html>').decode().encode(encoding)
                self.assertEqual(xml(data).find('body').text, 'Safe text')

    def test_xml_entities_and_internal_subsets_are_rejected_in_all_encodings(self):
        from kobosync.epub import xml
        for encoding in ['utf-8', 'utf-16']:
            data='<!DOCTYPE html [<!ENTITY private "expanded">]><html>&private;</html>'.encode(encoding)
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                xml(data)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_reads_book_not_later_chapter_without_mutation(self):
        from kobosync.kobo import read_latest
        p = self.root / "KoboReader.sqlite"
        make_db(p)
        before = p.read_bytes()
        result = read_latest(p)
        self.assertEqual(result["title"], "Book")
        self.assertEqual(result["percent"], 42)
        self.assertEqual(result["source_time"], "2026-10-18T03:45:00+00:00")
        self.assertEqual(p.read_bytes(), before)

    def test_resolves_real_epub_fragment(self):
        from kobosync.epub import passage
        p = self.root / "book.epub"
        make_epub(p)
        self.assertEqual(passage(p, "file:///mnt/onboard/Book.epub!OEBPS/ch1.xhtml#kobo.2.1"), TEXT)

    def test_missing_fragment_fails_instead_of_using_percentage(self):
        from kobosync.epub import passage
        p = self.root / "book.epub"
        make_epub(p)
        with self.assertRaisesRegex(ValueError, "fragment"):
            passage(p, "OEBPS/ch1.xhtml#missing")

    def test_matches_words_after_intro_on_second_track(self):
        from kobosync.alignment import match_passage
        transcript = words("An introduction to a different story entirely", 0) + words(track=1, offset=65)
        result = match_passage(TEXT, transcript)
        self.assertEqual(result["track"], 1)
        self.assertEqual(result["seconds"], 65)
        self.assertGreaterEqual(result["score"], .9)

    def test_repeated_passage_is_ambiguous(self):
        from kobosync.alignment import match_passage
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            match_passage(TEXT, words() + words(offset=200))

    def test_matches_peter_rabbit_opening_with_transcription_punctuation(self):
        from kobosync.alignment import match_passage
        # Gutenberg 14838 and the corresponding complete LibriVox recording.
        passage = ("Once upon a time there were four little Rabbits, and their names were— "
                   "Flopsy, Mopsy, Cotton-tail, and Peter. They lived with their Mother "
                   "in a sand-bank, underneath the root of a very big fir-tree. "
                   "Now, my dears, said old Mrs. Rabbit one morning")
        spoken = ("Once upon a time there were four little rabbits, and their names were "
                  "Flopsy, Mopsy, Cottontail, and Peter. They lived with their mother "
                  "in a sandbank, underneath the root of a very big fir tree. "
                  "Now, my dear's, said Old Mrs. Rabbit one morning")
        result = match_passage(passage, words(spoken, offset=18.72))
        self.assertEqual(result["seconds"], 18.72)
        self.assertGreaterEqual(result["score"], .88)

    def test_wrong_narration_fails(self):
        from kobosync.alignment import match_passage
        with self.assertRaisesRegex(ValueError, "match"):
            match_passage(TEXT, words("Something quite different happened yesterday at the harbor where the ships arrived before dawn"))

    def test_bad_schema_diagnoses(self):
        from kobosync.kobo import read_latest
        p = self.root / "empty.sqlite"
        sqlite3.connect(p).close()
        with self.assertRaisesRegex(ValueError, "content"):
            read_latest(p)
