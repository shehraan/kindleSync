"""Optional local speech recognition and free LibriVox acquisition."""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from .store import atomic_json


def fetch_json(url):
    with urlopen(Request(url, headers={"User-Agent": "KoboSync/0.1 (personal audiobook handoff)"}), timeout=30) as r:
        return json.load(r)


def search_librivox(title):
    query = urlencode(dict(title=title, format="json", limit=10))
    try:
        return fetch_json("https://librivox.org/api/feed/audiobooks/?" + query).get("books", [])
    except HTTPError as e:
        if e.code == 404:
            return []
        raise


def download(url, destination):
    if urlsplit(url).scheme not in {"https", "http"}:
        raise ValueError("Audio download URL must use HTTP or HTTPS")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=destination.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, urlopen(Request(url, headers={"User-Agent": "KoboSync/0.1"}), timeout=60) as response:
            size = 0
            while chunk := response.read(262144):
                size += len(chunk)
                if size > 8_000_000_000:
                    raise ValueError("Audio file exceeds 8 GB")
                out.write(chunk)
            expected = response.headers.get("Content-Length")
            if expected and size != int(expected):
                raise ValueError("Audio download was incomplete")
        os.replace(part, destination)
    finally:
        if os.path.exists(part):
            os.unlink(part)


def fetch_librivox(project_id, destination):
    if not str(project_id).isdigit():
        raise ValueError("LibriVox project id must be numeric")
    data = fetch_json("https://librivox.org/api/feed/audiotracks/?" + urlencode(dict(project_id=project_id, format="json")))
    # The tracks endpoint returns `sections`; inspect names rather than infer ZIP order.
    tracks = data.get("sections", data.get("audiotracks", []))
    if not tracks:
        raise ValueError("No downloadable LibriVox tracks found for this project")
    tracks.sort(key=lambda t: int(t["section_number"]))
    destination = Path(destination)
    paths = []
    for i, t in enumerate(tracks):
        path = destination / f"{i:04d}.mp3"
        download(t["listen_url"], path)
        paths.append(path)
    return paths


def transcribe(store, book_id, model="base.en", language=None):
    # Keep model downloads and the optional Xet transport's cache with app data.
    os.environ.setdefault("HF_HOME", str(store.root / "models" / "hub"))
    os.environ.setdefault("HF_XET_CACHE", str(store.root / "models" / "xet"))
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ValueError("Install local transcription: uv pip install -e '.[transcribe]'") from e
    store.verify_assets(book_id)
    book = store.book(book_id)
    recognizer = WhisperModel(model, device="cpu", compute_type="int8", download_root=str(store.root / "models"))
    all_words = []
    for track in book["tracks"]:
        cache = store.root / "library" / book_id / f'transcript-{track["id"]:04d}.json'
        key = dict(sha256=track["sha256"], model=model, language=language)
        cached = json.loads(cache.read_text()) if cache.exists() else {}
        if cached.get("key") == key:
            all_words.extend(cached["words"])
            continue
        print(f'Transcribing {book_id}, track {track["id"]+1}/{len(book["tracks"])}', flush=True)
        segments, _ = recognizer.transcribe(str(store.asset(book_id, track["id"])), word_timestamps=True,
                                           language=language, vad_filter=True)
        track_words = [dict(word=w.word, start=w.start, end=w.end, track=track["id"])
                       for segment in segments for w in (segment.words or [])]
        atomic_json(cache, dict(key=key, words=track_words))
        all_words.extend(track_words)
    store.save_transcript(book_id, all_words)
    return len(all_words)
