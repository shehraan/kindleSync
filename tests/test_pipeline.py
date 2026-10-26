import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from test_core import TEXT, make_db, make_epub, words


class PipelineTests(unittest.TestCase):
    def setUp(self):
        from kobosync.store import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "data")
        self.epub = self.root / "book.epub"
        self.audio = self.root / "book.mp3"
        self.db = self.root / "KoboReader.sqlite"
        make_epub(self.epub)
        self.audio.write_bytes(b"test audio bytes")
        make_db(self.db)
        self.store.register("book", "file:///mnt/onboard/Book.epub", self.epub, [self.audio], "Book", "Writer")
        self.store.save_transcript("book", words())

    def test_publishes_stable_revision_and_daily_history(self):
        from kobosync.pipeline import run_once
        a = run_once(self.store, self.db)
        b = run_once(self.store, self.db)
        self.assertEqual(a["revision"], b["revision"])
        self.assertEqual(a["position"]["seconds"], 10)
        self.assertEqual(a["tracks"][0]["bytes"], len(b"test audio bytes"))
        self.assertEqual(self.store.latest()["revision"], a["revision"])
        self.assertEqual(len(self.store.history()), 1)

    def test_transcript_from_changed_audio_is_rejected(self):
        from kobosync.pipeline import run_once
        self.store.asset("book", 0).write_bytes(b"different recording")
        with self.assertRaisesRegex(ValueError, "changed"):
            run_once(self.store, self.db)
        self.assertIsNone(self.store.latest())

    def test_unknown_book_never_guesses_by_title(self):
        import sqlite3
        from kobosync.pipeline import run_once
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE content SET ContentID='another-edition' WHERE BookID IS NULL")
        with self.assertRaisesRegex(ValueError, "registered"):
            run_once(self.store, self.db)

    def test_scheduler_catches_up_and_does_not_repeat_success(self):
        from kobosync.schedule import tick
        now = datetime(2026, 10, 18, 11, tzinfo=timezone.utc)
        started = datetime.now(timezone.utc)
        self.assertTrue(tick(self.store, self.db, now))
        record = json.loads(self.store.value("last_scheduled_run", "null"))
        self.assertIsNotNone(record, 'A completed morning run needs durable timing evidence')
        self.assertEqual(record["scheduled_for"], "2026-10-18T10:00:00+00:00")
        self.assertEqual(record["source_time"], "2026-10-18T03:45:00+00:00")
        self.assertEqual(record["revision"], self.store.latest()["revision"])
        self.assertLessEqual(started, datetime.fromisoformat(record["completed_at"]))
        self.assertLessEqual(datetime.fromisoformat(record["completed_at"]), datetime.now(timezone.utc))
        self.assertFalse(tick(self.store, self.db, now))
        self.assertEqual(json.loads(self.store.value("last_scheduled_run")), record)

    def test_scheduler_retry_after_source_failure(self):
        from kobosync.schedule import tick
        now = datetime(2026, 10, 18, 11, tzinfo=timezone.utc)
        with self.assertRaises(FileNotFoundError):
            tick(self.store, self.root / "absent", now)
        self.assertIsNone(self.store.value("last_scheduled_run"))
        self.assertTrue(tick(self.store, self.db, now))

    def test_dst_keeps_six_am_local(self):
        from kobosync.schedule import due_date
        self.assertIsNone(due_date(datetime(2026, 11, 1, 10, 59, tzinfo=timezone.utc)))
        self.assertEqual(due_date(datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)), "2026-11-01")
        self.assertIsNone(due_date(datetime(2026, 3, 8, 9, 59, tzinfo=timezone.utc)))
        self.assertEqual(due_date(datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)), "2026-03-08")

    def test_duplicate_registration_cannot_destroy_existing_book(self):
        with self.assertRaisesRegex(ValueError, "already"):
            self.store.register("book", "other", self.epub, [self.audio], "Book", "Writer")
        self.assertEqual(self.store.book("book")["content_id"], "file:///mnt/onboard/Book.epub")

    def test_bad_transcript_track_fails_before_publication(self):
        with self.assertRaisesRegex(ValueError, "track"):
            self.store.save_transcript("book", words(track=9))

    def observation(self, timestamp, text=TEXT, locator="page1"):
        return dict(reader="koreader",content_id="file:///mnt/onboard/Book.epub",
                    epub_md5=self.store.book("book")["epub_md5"], source_time=timestamp,
                    passage=text, locator=locator)

    def test_same_position_heartbeat_does_not_rewind_listening(self):
        from kobosync.pipeline import run_once
        a = self.observation("2026-10-18T04:00:00Z")
        a["sequence"] = 1
        self.store.observe(a)
        first = run_once(self.store)
        b = self.observation("2026-10-18T04:01:00Z")
        b["sequence"] = 2
        self.store.observe(b)
        self.assertEqual(first["revision"], run_once(self.store)["revision"])

    def test_late_upload_of_overnight_position_updates_completed_morning(self):
        from kobosync.schedule import tick
        now = datetime(2026,10,18,11,tzinfo=timezone.utc)
        tick(self.store,self.db,now)
        self.store.observe(self.observation("2026-10-18T09:59:00Z", " ".join(TEXT.split()[3:]), "page2"))
        self.assertTrue(tick(self.store,None,now))
        self.assertEqual(self.store.latest()["position"]["seconds"], 11.5)

    def test_evening_reading_waits_for_following_morning(self):
        from kobosync.schedule import tick
        tick(self.store,self.db,datetime(2026,10,18,11,tzinfo=timezone.utc))
        revision = self.store.latest()["revision"]
        self.store.observe(self.observation("2026-10-18T23:00:00Z", " ".join(TEXT.split()[3:]), "page2"))
        self.assertFalse(tick(self.store,None,datetime(2026,10,18,23,10,tzinfo=timezone.utc)))
        self.assertEqual(self.store.latest()["revision"],revision)
        self.assertTrue(tick(self.store,None,datetime(2026,10,19,11,tzinfo=timezone.utc)))
        self.assertEqual(self.store.latest()["position"]["seconds"],11.5)

    def test_same_second_capture_order_wins_even_when_uploaded_in_reverse(self):
        late = self.observation("2026-10-18T04:00:00Z", " ".join(TEXT.split()[3:]), "selected")
        early = self.observation("2026-10-18T04:00:00Z")
        late["sequence"], early["sequence"] = 2, 1
        self.store.observe(late)
        self.store.observe(early)
        self.assertEqual(self.store.observation()["locator"], "selected")

    def test_later_selected_marker_with_same_timestamp_wins(self):
        early = self.observation("2026-10-18T04:00:00Z")
        late = self.observation("2026-10-18T04:00:00Z", " ".join(TEXT.split()[3:]), "selected")
        early["sequence"], late["sequence"] = 1, 2
        self.store.observe(early)
        self.store.observe(late)
        self.assertEqual(self.store.observation()["locator"], "selected")
