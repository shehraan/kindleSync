"""Small paired-device API. Administrative paths remain CLI-only."""
import hashlib
import hmac
import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .pipeline import prepare
from .captions import caption_asset, with_captions
from . import ingest


def create_browser(root):
    # Book-only installations do not import or start the optional browser.
    from .browser import BrowserService
    return BrowserService(root)


def _discovery_body(store, query):
    match = re.fullmatch(r"nonce=([a-f0-9]{32})", query)
    if not match:
        raise ValueError("Expected one 32-character lowercase hexadecimal nonce")
    message = f"kobosync-discovery-v1\n{match[1]}\n{store.value('instance_id')}"
    proof = hmac.new(store.token.encode("utf-8"), message.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{message}\n{proof}\n".encode("ascii")


def make_server(store, host="127.0.0.1", port=8787):
    browser_service = None
    browser_closing = False
    browser_lock = threading.Lock()

    def browser():
        nonlocal browser_service
        with browser_lock:
            if browser_closing:
                raise RuntimeError("The PC browser service is stopping.")
            if browser_service is None:
                browser_service = create_browser(store.root)
            return browser_service

    class Server(ThreadingHTTPServer):
        def server_close(self):
            nonlocal browser_service, browser_closing
            with browser_lock:
                browser_closing = True
                current, browser_service = browser_service, None
            try:
                super().server_close()
            finally:
                if current is not None:
                    current.close()

    class Handler(BaseHTTPRequestHandler):
        server_version = "KoboSync/0.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, fmt, *args):
            pass  # Never log pairing secrets or ebook passages.

        def respond(self, status, data=None):
            body = json.dumps(data, ensure_ascii=False).encode() if data is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def authenticated(self):
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + store.token).encode()):
                self.respond(401, {"error": "Pair this device using the server token"})
                return False
            return True

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            target = urlsplit(self.path)
            path = target.path
            if path == "/v1/discovery":
                try:
                    body = _discovery_body(store, target.query)
                except ValueError as error:
                    return self.respond(400, {"error": str(error)})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=us-ascii")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
            if path == "/health":
                return self.respond(200, {"ok": True, "service": "kobosync",
                                          "instance_id": store.value("instance_id")})
            if not self.authenticated():
                return
            if path.startswith("/v1/browser/frame/"):
                frame = re.fullmatch(r"/v1/browser/frame/([a-f0-9]{32})/([0-9]{1,12})", path)
                if not frame:
                    return self.respond(404, {"error": "Unknown browser frame"})
                try:
                    asset = browser().frame(frame[1], int(frame[2]))
                    with asset.open("rb") as stream:
                        sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
                    return self.stream_asset(asset, sha256, "image/png", private=True)
                except (ValueError, OSError):
                    return self.respond(404, {"error": "Unknown browser frame"})
                except RuntimeError as error:
                    return self.respond(503, {"error": str(error)})
            if path == "/v1/handoff":
                latest = with_captions(store, store.latest())
                return self.respond(200 if latest else 204, latest)
            if path == "/v1/status":
                with store.connect() as c:
                    ready = list(c.execute("SELECT revision,ready_at FROM readiness ORDER BY ready_at DESC LIMIT 1"))
                return self.respond(200, dict(last_error=store.value("last_error", ""), ready=ready,
                                              imports=ingest.jobs(store)))
            if path == "/v1/prepare":
                try:
                    manifest = with_captions(store, prepare(store))
                    if manifest["book_id"].startswith("auto-") and not manifest.get("captions"):
                        return self.respond(204)
                    return self.respond(200, manifest)
                except ValueError as e:
                    return self.respond(204)
            caption = re.fullmatch(r"/v1/captions/([A-Za-z0-9_-]{1,64})/([a-f0-9]{64})", path)
            if caption:
                try:
                    asset = caption_asset(store, caption[1], caption[2])
                    return self.stream_asset(asset, caption[2], "text/vtt; charset=utf-8")
                except (ValueError, OSError):
                    return self.respond(404, {"error": "Unknown registered asset"})
            match = re.fullmatch(r"/v1/audio/([A-Za-z0-9_-]{1,64})/(\d{1,4})", path)
            if not match:
                return self.respond(404, {"error": "Unknown resource"})
            try:
                asset = store.asset(match[1], int(match[2]))
                sha256 = store.book(match[1])["tracks"][int(match[2])]["sha256"]
                return self.stream_asset(asset, sha256)
            except (ValueError, OSError):
                return self.respond(404, {"error": "Unknown registered asset"})

        def stream_asset(self, asset, sha256, content_type=None, private=False):
            size = asset.stat().st_size
            start, end, status = 0, size-1, 200
            range_value = self.headers.get("Range")
            if range_value:
                r = re.fullmatch(r"bytes=(\d*)-(\d*)", range_value)
                if not r or not any(r.groups()):
                    return self.bad_range(size)
                if not r[1]:
                    length = int(r[2])
                    start = max(0, size-length)
                else:
                    start = int(r[1])
                    end = min(size-1, int(r[2])) if r[2] else size-1
                if not 0 <= start <= end < size:
                    return self.bad_range(size)
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", content_type or mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(max(0, end-start+1)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", '"' + sha256 + '"')
            if private:
                self.send_header("Cache-Control", "no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command != "HEAD":
                with asset.open("rb") as f:
                    f.seek(start)
                    remaining = end-start+1
                    while remaining > 0:
                        block = f.read(min(262144, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)

        def bad_range(self, size):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            if not self.authenticated():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                path = urlsplit(self.path).path
                if path.startswith("/v1/ebooks/"):
                    if self.headers.get("Transfer-Encoding"):
                        raise ValueError("EPUB uploads require Content-Length without transfer encoding")
                    if length > ingest.MAX_EPUB_BYTES:
                        return self.respond(413, {"error": "EPUB upload exceeds 256 MiB"})
                    result = ingest.receive_epub(store, path[len("/v1/ebooks/"):], self.rfile, length)
                    return self.respond(200, result)
                if not 0 < length <= 32768:
                    return self.respond(413, {"error": "Expected a small JSON payload"})
                payload = self.rfile.read(length)
                if len(payload) != length:
                    raise ValueError("JSON upload was interrupted")
                data = json.loads(payload)
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                if path == "/v1/browser/action":
                    try:
                        return self.respond(200, browser().action(data))
                    except ValueError as error:
                        if getattr(error, "code", None) == "stale_frame":
                            return self.respond(409, {"error": str(error), "code": "stale_frame"})
                        raise
                    except RuntimeError as error:
                        return self.respond(503, {"error": str(error)})
                if path == "/v1/progress":
                    if data.get("reader") != "koreader" or not all(isinstance(data.get(k), str) for k in ("content_id", "epub_md5", "locator", "passage", "source_time")):
                        raise ValueError("Expected a KOReader observation with book, edition, locator, text and time")
                    observation = {k: data[k] for k in ("reader", "content_id", "epub_md5", "locator", "passage", "source_time")}
                    observation["sequence"] = data.get("sequence", 0)
                    key = store.observe(observation)
                    return self.respond(200, dict(observation=key, **ingest.progress_status(store, observation)))
                if path == "/v1/ready":
                    store.acknowledge(data["revision"])
                    return self.respond(200, {"ok": True})
                self.respond(404, {"error": "Unknown resource"})
            except (ValueError, KeyError, TypeError, UnicodeError) as e:
                self.respond(400, {"error": str(e)})
            except (TimeoutError, ConnectionError) as e:
                self.respond(400, {"error": "Upload was interrupted"})

    return Server((host, port), Handler)
