"""Resolve the latest reading observation and publish a stable revision."""
import fcntl
import hashlib
import json
from .alignment import match_passage
from .epub import passage
from .kobo import read_latest
from .store import utcnow


def track_manifest(book):
    return [dict(t, url=f'/v1/audio/{book["id"]}/{t["id"]}') for t in book["tracks"]]


def prepare(store):
    observation = store.observation()
    book = store.find_book(observation["content_id"], observation.get("epub_md5"))
    if book["id"].startswith("auto-"):
        from .ingest import progress_status
        if progress_status(store, observation)["book_state"] != "ready":
            raise ValueError("Computer narration and captions are still preparing")
        store.transcript(book["id"])
    return dict(book_id=book["id"], title=book["title"], tracks=track_manifest(book))


def run_once(store, database=None, before=None):
    with open(store.root / "run.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if database:
            store.observe(read_latest(database))
        observation = store.observation(before)
        book = store.find_book(observation["content_id"], observation.get("epub_md5"))
        text = observation.get("passage") if observation.get("reader") == "koreader" else passage(store.ebook(book["id"]), observation["locator"])
        position = match_passage(text, store.transcript(book["id"]))
        position_identity = {k:v for k,v in observation.items() if k not in {"source_time", "sequence"}}
        identity = dict(book_id=book["id"], observation=position_identity,
                        signature=store.signature(book["id"]), position=position)
        revision = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        previous = store.latest()
        if previous and previous["revision"] == revision:
            return previous
        manifest = dict(schema=1, revision=revision, book_id=book["id"],
                        title=book["title"], author=book["author"],
                        source_time=observation["source_time"], created_at=utcnow(),
                        position=position, tracks=track_manifest(book))
        store.publish(manifest)
        store.set_value("last_error", "")
        return manifest
