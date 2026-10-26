"""Bounded EPUB text extraction and resumable, entirely local English speech."""
import hashlib
import json
import os
import posixpath
import re
import subprocess
import tempfile
import zipfile
from contextlib import suppress
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from xml.etree.ElementTree import ParseError

from . import audio
from .epub import local, xml
from .store import atomic_json, digest


MAX_XML_BYTES = 16_000_000
MAX_ARCHIVE_BYTES = 128_000_000
MAX_CHAPTER_CHARACTERS = 250_000
MAX_BOOK_CHARACTERS = 8_000_000
MAX_CHAPTERS = 1000
SPEECH_CHUNK_CHARACTERS = 800
VOICE = "en_US-lessac-medium"
VOICE_REVISION = "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"
VOICE_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/{VOICE_REVISION}/en/en_US/lessac/medium/"
VOICE_FILES = {
    VOICE + ".onnx": (63_201_294, "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"),
    VOICE + ".onnx.json": (4885, "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0"),
}
_BLOCKS = {"p", "div", "section", "article", "aside", "blockquote", "li", "ul", "ol", "dl", "dt", "dd", "table", "tr", "td", "th", "br", "hr", "pre", "figure", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"head", "script", "style", "noscript", "template"}


def _entry(archive, name, budget):
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ValueError("EPUB resource is missing: " + name) from exc
    if info.flag_bits & 1:
        raise ValueError("DRM/protected EPUBs cannot be narrated")
    if info.file_size > MAX_XML_BYTES:
        raise ValueError("Oversized EPUB XML resource: " + name)
    remaining = MAX_ARCHIVE_BYTES - budget[0]
    if info.file_size > remaining:
        raise ValueError("EPUB XML extraction exceeds its cumulative limit")
    with archive.open(info) as source:
        data = source.read(min(MAX_XML_BYTES, remaining) + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError("Oversized EPUB XML resource: " + name)
    budget[0] += len(data)
    if budget[0] > MAX_ARCHIVE_BYTES:
        raise ValueError("EPUB XML extraction exceeds its cumulative limit")
    try:
        return xml(data)
    except ParseError as exc:
        raise ValueError("Invalid EPUB XML resource: " + name) from exc


def _resource(package_path, href):
    parsed = urlsplit(href)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or path.startswith("/") or "\\" in path:
        raise ValueError("Unsupported EPUB resource path")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), path))
    if resolved == ".." or resolved.startswith("../") or not path:
        raise ValueError("Unsupported EPUB resource path")
    return resolved


def _hidden(node):
    style = node.attrib.get("style", "").lower()
    return ("hidden" in node.attrib or node.attrib.get("aria-hidden", "").lower() == "true"
            or re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\s*(?:!important)?\s*(?:;|$)", style))


def _text(root):
    # An explicit stack also handles deeply nested XHTML without Python recursion.
    chunks = []
    size = 0
    stack = [(root, False)]
    while stack:
        node, closing = stack.pop()
        if closing is None:
            text = node
        elif closing:
            text = " " if local(node.tag).lower() in _BLOCKS else ""
        elif local(node.tag).lower() in _SKIP or _hidden(node):
            continue
        else:
            text = (" " if local(node.tag).lower() in _BLOCKS else "") + (node.text or "")
            stack.append((node, True))
            for child in reversed(list(node)):
                # Tails belong to the visible parent even when a child is hidden.
                if child.tail:
                    stack.append((child.tail, None))
                stack.append((child, False))
        size += len(text)
        if size > MAX_CHAPTER_CHARACTERS * 2:
            raise ValueError("EPUB chapter text exceeds narration limit")
        chunks.append(text)
    result = re.sub(r"\s+", " ", "".join(chunks)).strip()
    if len(result) > MAX_CHAPTER_CHARACTERS:
        raise ValueError("EPUB chapter text exceeds narration limit")
    return result


def read_epub(path):
    """Return metadata and visible chapters in package spine order, within limits."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 10_000:
                raise ValueError("EPUB archive exceeds extraction limits")
            # Artwork is never decompressed for narration. Bound the XML we
            # actually read instead of rejecting illustrated books by image size.
            budget = [0]
            if len({info.filename for info in infos}) != len(infos):
                raise ValueError("EPUB archive has ambiguous duplicate resources")
            if "META-INF/rights.xml" in archive.namelist():
                raise ValueError("DRM/protected EPUBs cannot be narrated")
            if "META-INF/encryption.xml" in archive.namelist():
                encryption = _entry(archive, "META-INF/encryption.xml", budget)
                algorithms = [node.attrib.get("Algorithm", "") for node in encryption.iter() if local(node.tag) == "EncryptionMethod"]
                # EPUB font obfuscation does not encrypt the readable book text.
                if not algorithms or any(algorithm not in {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"} for algorithm in algorithms):
                    raise ValueError("DRM/protected EPUBs cannot be narrated")
            container = _entry(archive, "META-INF/container.xml", budget)
            roots = [node for node in container.iter() if local(node.tag) == "rootfile"]
            if not roots or not roots[0].attrib.get("full-path"):
                raise ValueError("EPUB has no package document")
            package_path = _resource("", roots[0].attrib["full-path"])
            package = _entry(archive, package_path, budget)
            metadata = next((node for node in package if local(node.tag) == "metadata"), None)
            fields = {}
            if metadata is not None:
                for node in metadata:
                    value = re.sub(r"\s+", " ", "".join(node.itertext())).strip()
                    fields.setdefault(local(node.tag), value)
            manifest = {node.attrib.get("id"): node for node in package.iter() if local(node.tag) == "item"}
            chapters = []
            total = 0
            linear_count = 0
            for ref in package.iter():
                if local(ref.tag) != "itemref" or ref.attrib.get("linear", "yes").lower() == "no":
                    continue
                linear_count += 1
                if linear_count > MAX_CHAPTERS:
                    raise ValueError("EPUB spine chapter count exceeds narration limit")
                item = manifest.get(ref.attrib.get("idref"))
                if item is None:
                    raise ValueError("EPUB spine references a missing resource")
                if item.attrib.get("media-type", "application/xhtml+xml") not in {"application/xhtml+xml", "text/html"}:
                    raise ValueError("EPUB spine contains unsupported non-text content")
                chapter = _text(_entry(archive, _resource(package_path, item.attrib.get("href", "")), budget))
                if chapter:
                    chapters.append(chapter)
                    total += len(chapter)
                if len(chapters) > MAX_CHAPTERS or total > MAX_BOOK_CHARACTERS:
                    raise ValueError("EPUB text exceeds narration limits")
            if not chapters:
                raise ValueError("EPUB has no readable linear chapters")
            return dict(title=fields.get("title") or path.stem, author=fields.get("creator", ""),
                        language=fields.get("language") or "en", chapters=chapters)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("Invalid or protected EPUB archive") from exc


def ensure_voice(models_root):
    """Download the pinned free Piper voice once; verify cached and new bytes."""
    destination = Path(models_root) / "piper"
    destination.mkdir(parents=True, exist_ok=True)
    for name, (size, sha256) in VOICE_FILES.items():
        target = destination / name
        if target.is_file() and target.stat().st_size == size and digest(target) == sha256:
            continue
        fd, temporary = tempfile.mkstemp(dir=destination, prefix=".voice-", suffix=".part")
        try:
            actual = hashlib.sha256()
            received = 0
            request = Request(VOICE_URL + name, headers={"User-Agent": "KoboSync/0.1 (local narration)"})
            with os.fdopen(fd, "wb") as output, urlopen(request, timeout=60) as source:
                while chunk := source.read(min(262144, size - received + 1)):
                    received += len(chunk)
                    if received > size:
                        raise ValueError("Piper voice download exceeded expected size")
                    actual.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if received != size or actual.hexdigest() != sha256:
                raise ValueError("Piper voice download failed integrity verification")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return destination / (VOICE + ".onnx")


def speech_chunks(text):
    """Keep each synthesizer input small, preferring sentence or word boundaries."""
    while text:
        end = min(len(text), SPEECH_CHUNK_CHARACTERS)
        if end < len(text):
            sentence = list(re.finditer(r"[.!?][\"'”’]?\s+", text[:end]))
            boundary = sentence[-1].end() if sentence else text.rfind(" ", 0, end)
            if boundary > end // 2:
                end = boundary
        chunk, text = text[:end].strip(), text[end:].lstrip()
        if chunk:
            yield chunk


class PiperNarrator:
    """Stream bounded Piper PCM chunks directly to ffmpeg, without a book WAV."""
    def __init__(self, models_root):
        try:
            import onnxruntime
            from piper import PiperVoice
            from piper.config import PiperConfig
        except ImportError as exc:
            raise ValueError("Install local speech: uv pip install -e '.[speech]'") from exc
        model = ensure_voice(models_root)
        onnxruntime.disable_telemetry_events()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 2) - 1))
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        session = onnxruntime.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        config = PiperConfig.from_dict(json.loads(model.with_suffix(".onnx.json").read_text()))
        self.voice = PiperVoice(session=session, config=config, download_dir=Path(models_root))

    def __call__(self, text, destination):
        chunks = (chunk for piece in speech_chunks(text) for chunk in self.voice.synthesize(piece))
        first = next(chunks, None)
        if first is None:
            raise ValueError("Piper produced no narration audio")
        if first.sample_width != 2:
            raise ValueError("Piper produced an unsupported audio sample format")
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-f", "s16le", "-ar", str(first.sample_rate),
                                        "-ac", str(first.sample_channels), "-i", "pipe:0", "-codec:a", "libmp3lame", "-q:a", "4", "-f", "mp3", "-y", str(destination)],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                process.stdin.write(first.audio_int16_bytes)
                for chunk in chunks:
                    if (chunk.sample_rate, chunk.sample_channels, chunk.sample_width) != (first.sample_rate, first.sample_channels, first.sample_width):
                        raise ValueError("Piper changed audio format within a chapter")
                    process.stdin.write(chunk.audio_int16_bytes)
                process.stdin.close()
                if process.wait() != 0:
                    errors.seek(0)
                    raise ValueError("MP3 encoding failed: " + errors.read(4000).decode(errors="replace"))
            except BaseException:
                if process.stdin and not process.stdin.closed:
                    with suppress(OSError):
                        process.stdin.close()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise


def _cached(path, metadata, key):
    try:
        data = json.loads(metadata.read_text())
        return (data["key"] == key and path.is_file() and path.stat().st_size > 0
                and data["sha256"] == digest(path))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _finish(store, book, transcribe):
    store.verify_assets(book["id"])
    try:
        store.transcript(book["id"])
    except (ValueError, OSError, KeyError, TypeError):
        transcribe(store, book["id"], language="en")
    # Never report prepared before actual word timings exist.
    store.transcript(book["id"])
    return book


def prepare_book(store, md5, *, synthesize=None, transcribe=None):
    """Resume chapter synthesis and transcription, then return the registered book."""
    if not isinstance(md5, str) or not re.fullmatch(r"[0-9a-f]{32}", md5):
        raise ValueError("Invalid EPUB edition hash")
    transcribe = transcribe or audio.transcribe
    book_id = "auto-" + md5
    existing = store.book(book_id, required=False)
    if existing:
        if existing["epub_md5"] != md5:
            raise ValueError("Registered book does not match the requested edition")
        return _finish(store, existing, transcribe)
    source = store.root / "inbox" / md5 / "book.epub"
    if digest(source, "md5") != md5:
        raise ValueError("EPUB hash does not match requested edition")
    book = read_epub(source)
    if book["language"].lower().replace("_", "-").split("-")[0] not in {"en", "eng", "english"}:
        raise ValueError("Local generated narration currently supports English EPUBs only (book language: " + book["language"] + ")")
    stage = store.root / "generated" / md5
    stage.mkdir(parents=True, exist_ok=True)
    tracks = []
    for index, text in enumerate(book["chapters"]):
        path = stage / f"{index:04d}.mp3"
        metadata = stage / f"{index:04d}.json"
        key = dict(text_sha256=hashlib.sha256(text.encode()).hexdigest(), voice=VOICE,
                   voice_sha256=VOICE_FILES[VOICE + ".onnx"][1], encoding="piper-1.8.0-mp3-v1")
        if not _cached(path, metadata, key):
            print(f'Narrating {book_id}, chapter {index + 1}/{len(book["chapters"])}', flush=True)
            if synthesize is None:
                synthesize = PiperNarrator(store.root / "models")
            fd, temporary = tempfile.mkstemp(dir=stage, prefix=".chapter-", suffix=".mp3")
            os.close(fd)
            try:
                synthesize(text, Path(temporary))
                if not Path(temporary).stat().st_size:
                    raise ValueError("Piper produced an empty narration chapter")
                os.replace(temporary, path)
                atomic_json(metadata, dict(key=key, sha256=digest(path)))
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        tracks.append(path)
    registered = store.register(book_id, md5 + ".epub", source, tracks, book["title"], book["author"])
    return _finish(store, registered, transcribe)
