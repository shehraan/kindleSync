"""Managed inputs, reading history and atomic handoff publication."""
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def digest(path, algorithm="sha256"):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, algorithm).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def edition_md5(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", value):
        raise ValueError("Expected the EPUB's 32-character whole-file MD5")
    return value.lower()


def atomic_json(path, value):
    path = Path(path)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "library").mkdir(exist_ok=True)
        with self.connect() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS books (id TEXT PRIMARY KEY, content_id TEXT, data TEXT);
              CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, source_time TEXT, data TEXT);
              CREATE TABLE IF NOT EXISTS handoffs (revision TEXT PRIMARY KEY, data TEXT, created TEXT);
              CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
              CREATE TABLE IF NOT EXISTS days (date TEXT PRIMARY KEY, completed TEXT);
              CREATE TABLE IF NOT EXISTS readiness (revision TEXT PRIMARY KEY, ready_at TEXT);
              CREATE TABLE IF NOT EXISTS imports (md5 TEXT PRIMARY KEY, data TEXT NOT NULL);
            ''')
            # Editions are identified by their bytes; filenames can be shared.
            for index in c.execute("PRAGMA index_list(books)").fetchall():
                if index[2] and [r[2] for r in c.execute('PRAGMA index_info("' + index[1] + '")')] == ["content_id"]:
                    c.execute("BEGIN IMMEDIATE")
                    c.execute("CREATE TABLE books_editions (id TEXT PRIMARY KEY, content_id TEXT, data TEXT)")
                    c.execute("INSERT INTO books_editions SELECT id,content_id,data FROM books")
                    c.execute("DROP TABLE books")
                    c.execute("ALTER TABLE books_editions RENAME TO books")
                    break
            cols = {r[1] for r in c.execute("PRAGMA table_info(observations)")}
            if "capture_order" not in cols:
                c.execute("ALTER TABLE observations ADD COLUMN capture_order INTEGER NOT NULL DEFAULT 0")
            if c.execute("SELECT 1 FROM state WHERE key='token'").fetchone() is None:
                c.execute("INSERT INTO state VALUES ('token', ?)", (secrets.token_urlsafe(32),))
            c.execute("INSERT OR IGNORE INTO state VALUES ('instance_id', ?)", (secrets.token_hex(16),))

    def connect(self):
        return _Connection(self.root / "state.sqlite")

    def value(self, key, default=None):
        with self.connect() as c:
            row = c.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_value(self, key, value):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, str(value)))

    @property
    def token(self):
        return self.value("token")

    def register(self, book_id, content_id, epub, audio, title, author=""):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", book_id):
            raise ValueError("Book id must use 1–64 letters, digits, '-' or '_'")
        if self.book(book_id, required=False):
            raise ValueError("Book id already registered; use a new id for another edition")
        if not audio or len(audio) > 1000:
            raise ValueError("Provide 1–1000 ordered audio tracks")
        dest = self.root / "library" / book_id
        stage = Path(tempfile.mkdtemp(dir=self.root / "library"))
        try:
            shutil.copyfile(epub, stage / "book.epub")
            tracks = []
            for i, path in enumerate(audio):
                path = Path(path)
                ext = path.suffix.lower()
                if ext not in {".mp3", ".m4b", ".m4a", ".mp4", ".ogg", ".flac", ".wav"}:
                    raise ValueError("Unsupported audio extension: " + ext)
                name = f"{i:04d}{ext}"
                shutil.copyfile(path, stage / name)
                tracks.append(dict(id=i, name=name, sha256=digest(stage/name), bytes=(stage/name).stat().st_size))
            book = dict(id=book_id, content_id=content_id, title=title, author=author,
                        epub_sha256=digest(stage/"book.epub"), epub_md5=digest(stage/"book.epub", "md5"), tracks=tracks)
            with self.connect() as c:
                c.execute("INSERT INTO books VALUES (?,?,?)", (book_id, content_id, json.dumps(book)))
                if dest.exists():
                    # A process can die after the atomic rename but before SQLite
                    # commits. Adopt only identical, complete orphaned assets.
                    expected = {"book.epub": book["epub_sha256"], **{t["name"]: t["sha256"] for t in tracks}}
                    if dest.is_symlink() or not dest.is_dir() or any(
                        (dest / name).is_symlink() or not (dest / name).is_file()
                        or digest(dest / name) != sha256 for name, sha256 in expected.items()
                    ):
                        raise ValueError("Book directory already exists with different or incomplete assets")
                else:
                    stage.rename(dest)
                directory_fd = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return book
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def book(self, book_id, required=True):
        with self.connect() as c:
            row = c.execute("SELECT data FROM books WHERE id=?", (book_id,)).fetchone()
        if row:
            return json.loads(row[0])
        if required:
            raise ValueError("Book is not registered: " + str(book_id))

    def find_edition(self, md5):
        md5 = edition_md5(md5)
        matches = [book for book in self.books() if book["epub_md5"] == md5]
        return next((book for book in matches if not book["id"].startswith("auto-")), matches[0] if matches else None)

    def find_book(self, content_id, epub_md5=None):
        if epub_md5 is not None:
            md5 = edition_md5(epub_md5)
            exact = [book for book in self.books() if book["epub_md5"] == md5 and book["content_id"] == content_id]
            book = next((book for book in exact if not book["id"].startswith("auto-")), exact[0] if exact else None)
            book = book or self.find_edition(md5)
            if book is None:
                raise ValueError("EPUB edition not registered yet; upload it for automatic narration")
            return book
        with self.connect() as c:
            rows = c.execute("SELECT data FROM books WHERE content_id=?", (content_id,)).fetchall()
        if not rows:
            raise ValueError("Book not registered: " + str(content_id) + "; register its exact ebook and audio first")
        if len(rows) > 1:
            raise ValueError("Multiple registered editions share this filename; supply the EPUB MD5")
        return json.loads(rows[0][0])

    def books(self):
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT data FROM books ORDER BY id")]

    def ebook(self, book_id):
        self.book(book_id)
        return self.root / "library" / book_id / "book.epub"

    def asset(self, book_id, track):
        book = self.book(book_id)
        if not isinstance(track, int) or not 0 <= track < len(book["tracks"]):
            raise ValueError("Unknown audio track")
        return self.root / "library" / book_id / book["tracks"][track]["name"]

    def signature(self, book_id):
        book = self.book(book_id)
        return [book["epub_sha256"]] + [t["sha256"] for t in book["tracks"]]

    def verify_assets(self, book_id):
        actual = [digest(self.ebook(book_id))] + [digest(self.asset(book_id, t["id"])) for t in self.book(book_id)["tracks"]]
        if actual != self.signature(book_id):
            raise ValueError("Book/audio files changed; register and transcribe the new edition")

    def save_transcript(self, book_id, words):
        book = self.book(book_id)
        previous = {}
        for w in words:
            t, start, end = w["track"], float(w["start"]), float(w["end"])
            if type(t) is not int or not 0 <= t < len(book["tracks"]):
                raise ValueError("Transcript references unknown track")
            if not w.get("word") or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end or start < previous.get(t, 0):
                raise ValueError("Invalid or out-of-order transcript timestamps")
            previous[t] = start
        if not words:
            raise ValueError("Transcript is empty")
        self.verify_assets(book_id)
        atomic_json(self.root / "library" / book_id / "transcript.json", dict(signature=self.signature(book_id), words=words))

    def transcript(self, book_id):
        self.verify_assets(book_id)
        path = self.root / "library" / book_id / "transcript.json"
        if not path.exists():
            raise ValueError("Audio not transcribed yet; run transcribe " + book_id)
        data = json.loads(path.read_text())
        if data["signature"] != self.signature(book_id):
            raise ValueError("Transcript inputs changed; transcribe this edition again")
        return data["words"]

    def observe(self, observation):
        # The wireless collector submits text from crengine, never a guessed XPath.
        unknown_md5 = None
        if observation.get("reader") == "koreader":
            observation["epub_md5"] = edition_md5(observation.get("epub_md5"))
            if self.find_edition(observation["epub_md5"]) is None:
                unknown_md5 = observation["epub_md5"]
            if not isinstance(observation.get("passage"), str) or not 20 <= len(observation["passage"]) <= 8000:
                raise ValueError("KOReader passage must contain 20–8000 characters")
        when = datetime.fromisoformat(observation["source_time"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("Reading time needs a timezone")
        if (when - datetime.now(timezone.utc)).total_seconds() > 300:
            raise ValueError("Reading time is in the future; check the Kobo clock")
        observation["source_time"] = when.astimezone(timezone.utc).isoformat()
        sequence = observation.get("sequence", 0)
        if type(sequence) is not int or not 0 <= sequence < 2**53:
            raise ValueError("Invalid reading capture sequence")
        body = json.dumps(observation, sort_keys=True)
        key = hashlib.sha256(body.encode()).hexdigest()
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO observations (id,source_time,data,capture_order) VALUES (?,?,?,?)", (key, observation["source_time"], body, sequence))
            if unknown_md5:
                job = dict(md5=unknown_md5, book_state="awaiting_epub", attempts=0,
                           created_at=utcnow(), updated_at=utcnow(), next_attempt_at=None, error="")
                c.execute("INSERT OR IGNORE INTO imports VALUES (?,?)", (unknown_md5, json.dumps(job)))
        return key

    def observation(self, before=None):
        with self.connect() as c:
            if before:
                row = c.execute("SELECT data FROM observations WHERE source_time <= ? ORDER BY source_time DESC,capture_order DESC,rowid DESC LIMIT 1", (before,)).fetchone()
            else:
                row = c.execute("SELECT data FROM observations ORDER BY source_time DESC,capture_order DESC,rowid DESC LIMIT 1").fetchone()
        if not row:
            raise ValueError("No reading progress received yet; connect KOReader and send progress")
        return json.loads(row[0])

    def publish(self, manifest):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO handoffs VALUES (?,?,?)", (manifest["revision"], json.dumps(manifest), utcnow()))
            c.execute("INSERT OR REPLACE INTO state VALUES ('latest',?)", (manifest["revision"],))

    def latest(self):
        revision = self.value("latest")
        if not revision:
            return None
        with self.connect() as c:
            return json.loads(c.execute("SELECT data FROM handoffs WHERE revision=?", (revision,)).fetchone()[0])

    def history(self):
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT data FROM observations ORDER BY source_time DESC")]

    def acknowledge(self, revision):
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM handoffs WHERE revision=?", (revision,)).fetchone():
                raise ValueError("Unknown handoff revision")
            c.execute("INSERT OR REPLACE INTO readiness VALUES (?,?)", (revision, utcnow()))


class _Connection:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=30)

    def __enter__(self):
        return self.connection

    def __exit__(self, kind, value, traceback):
        try:
            if kind:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.connection.close()
