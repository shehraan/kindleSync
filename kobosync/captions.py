"""Deterministic full-track WebVTT assets, independent of listening revisions."""
import hashlib
import html
import math
import os
import re
import tempfile


_BOOK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SHA256 = re.compile(r"[a-f0-9]{64}")


def _directory(store, book_id):
    if not isinstance(book_id, str) or not _BOOK_ID.fullmatch(book_id):
        raise ValueError("Invalid caption book")
    store.book(book_id)
    directory = store.root / "library" / book_id / "captions"
    if directory.resolve() != directory:
        raise ValueError("Caption directory must remain in the managed library")
    return directory


def caption_asset(store, book_id, sha256):
    """Resolve only a registered book's immutable, hash-verified caption file."""
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError("Invalid caption hash")
    asset = _directory(store, book_id) / (sha256 + ".vtt")
    if asset.is_symlink() or not asset.is_file():
        raise ValueError("Unknown caption asset")
    with asset.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
            raise ValueError("Caption asset changed")
    return asset


def _timestamp(milliseconds):
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def _webvtt(words):
    groups = []
    current = []
    start = end = 0
    for word, word_start, word_end in words:
        # A substantial silence must clear the screen; normal word spacing can
        # stay within a short phrase. Count words even if one transcript entry
        # contains several whitespace-separated words.
        count = sum(len(item.split()) for item in current)
        if current and (count + len(word.split()) > 12 or word_end-start > 4000
                        or word_start-end >= 750):
            groups.append((start, end, current))
            current = []
        if not current:
            start = word_start
            end = word_end
        else:
            end = max(end, word_end)
        current.append(word)
    if current:
        groups.append((start, end, current))

    lines = ["WEBVTT\n\n"]
    previous_end = 0
    for index, (raw_start, raw_end, text) in enumerate(groups):
        start = max(previous_end, raw_start)
        end = min(raw_end, start + 4000)
        if index + 1 < len(groups):
            end = min(end, groups[index + 1][0])
        # Whisper can emit simultaneous or zero-duration words. Allocate at
        # least one millisecond without overlapping the preceding cue.
        end = max(start + 1, end)
        previous_end = end
        lines.append(f"{_timestamp(start)} --> {_timestamp(end)}\n")
        lines.append(html.escape(" ".join(text), quote=False) + "\n\n")
    return "".join(lines).encode("utf-8")


def _tracks(words, count):
    tracks = [[] for _ in range(count)]
    previous = {}
    if not isinstance(words, list) or not words:
        raise ValueError("Transcript is empty")
    for entry in words:
        track, word = entry["track"], entry["word"]
        start, end = float(entry["start"]), float(entry["end"])
        if (type(track) is not int or not 0 <= track < count
                or not isinstance(word, str) or not word.strip()
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end or start < previous.get(track, 0)):
            raise ValueError("Invalid caption transcript")
        previous[track] = start
        # Whitespace from ASR is formatting, not narration; normalizing it also
        # prevents embedded blank lines or cue syntax from becoming markup.
        tracks[track].append((" ".join(word.split()), round(start * 1000), round(end * 1000)))
    return tracks


def _publish(directory, sha256, data):
    destination = directory / (sha256 + ".vtt")
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".caption-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Linking a complete file publishes atomically and never replaces
            # an existing hash, including when two HTTP requests race.
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or destination.read_bytes() != data:
                raise ValueError("Existing immutable caption asset changed")
    finally:
        os.unlink(temporary)


def caption_manifest(store, book_id):
    """Create all tracks from a validated transcript, retaining earlier hashes."""
    directory = _directory(store, book_id)
    book = store.book(book_id)
    tracks = _tracks(store.transcript(book_id), len(book["tracks"]))
    directory.mkdir(exist_ok=True)
    result = []
    for track, words in enumerate(tracks):
        data = _webvtt(words)
        sha256 = hashlib.sha256(data).hexdigest()
        _publish(directory, sha256, data)
        result.append(dict(track=track, sha256=sha256, bytes=len(data),
                           url=f"/v1/captions/{book_id}/{sha256}"))
    return result


def with_captions(store, manifest):
    """Enrich only the HTTP response, preserving stored publication identity."""
    if manifest is None:
        return None
    result = dict(manifest)
    result.pop("captions", None)
    try:
        result["captions"] = caption_manifest(store, manifest["book_id"])
    except (ValueError, OSError, KeyError, TypeError, OverflowError):
        # An untranscribed or invalid edition still supports audio preloading.
        pass
    return result
