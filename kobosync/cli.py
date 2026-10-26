import argparse
import json
import logging
import signal
import threading
from . import audio, ingest
from .kobo import read_latest
from .pipeline import run_once
from .schedule import loop
from .server import make_server
from .store import Store


def parser():
    p = argparse.ArgumentParser(description="Free local Kobo-to-Android audiobook handoffs")
    p.add_argument("--data", default="data", help="Private managed data directory (default: ./data)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create local storage and show device pairing token")
    add = sub.add_parser("add", help="Register matching ebook and ordered audio files")
    add.add_argument("book_id")
    add.add_argument("--content-id", required=True, help="Exact EPUB filename used in KOReader")
    add.add_argument("--epub", required=True)
    add.add_argument("--audio", required=True, nargs="+", help="Audio files in playback order")
    add.add_argument("--title", required=True)
    add.add_argument("--author", default="")
    sub.add_parser("books")
    trans = sub.add_parser("transcribe")
    trans.add_argument("book_id")
    trans.add_argument("--model", default="base.en")
    trans.add_argument("--language")
    imp = sub.add_parser("import-transcript", help="Import word timestamps from another local transcription tool")
    imp.add_argument("book_id")
    imp.add_argument("json_file", help="JSON array of {word,track,start,end}")
    read = sub.add_parser("inspect-kobo", help="Diagnostic: inspect stock Kobo SQLite snapshot")
    read.add_argument("database")
    run = sub.add_parser("run", help="Publish the latest received reading position now")
    run.add_argument("--database", help="Diagnostic: collect from a stock Kobo SQLite snapshot")
    serve = sub.add_parser("serve", help="Run paired-device API and durable morning schedule")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--zone", default="America/Toronto")
    serve.add_argument("--hour", type=int, choices=range(24), default=6)
    serve.add_argument("--database", help="Optional stock database source instead of wireless KOReader observations")
    sub.add_parser("status")
    search = sub.add_parser("search-audio", help="Search free LibriVox narration; review edition before choosing")
    search.add_argument("title")
    fetch = sub.add_parser("fetch-audio", help="Download tracks from a selected LibriVox project")
    fetch.add_argument("project_id")
    fetch.add_argument("destination")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        store = Store(args.data)
        cmd = args.command
        if cmd == "init":
            print("Pairing token (enter in KOReader and Android): " + store.token)
            return
        if cmd == "add":
            result = store.register(args.book_id, args.content_id, args.epub, args.audio, args.title, args.author)
        elif cmd == "books":
            result = store.books()
        elif cmd == "transcribe":
            result = dict(words=audio.transcribe(store, args.book_id, args.model, args.language))
        elif cmd == "import-transcript":
            from pathlib import Path
            store.save_transcript(args.book_id, json.loads(Path(args.json_file).read_text()))
            result = {"ok": True}
        elif cmd == "inspect-kobo":
            result = read_latest(args.database)
        elif cmd == "run":
            result = run_once(store, args.database)
        elif cmd == "search-audio":
            result = audio.search_librivox(args.title)
        elif cmd == "fetch-audio":
            result = [str(p) for p in audio.fetch_librivox(args.project_id, args.destination)]
        elif cmd == "status":
            result = dict(latest=store.latest(), last_error=store.value("last_error", ""), books=len(store.books()),
                          last_scheduled_run=json.loads(store.value("last_scheduled_run", "null")),
                          imports=ingest.jobs(store))
        elif cmd == "serve":
            from zoneinfo import ZoneInfo
            ZoneInfo(args.zone)
            server = make_server(store, args.host, args.port)
            stop = threading.Event()
            worker = threading.Thread(target=loop, args=(store, args.database, args.zone, args.hour, stop), daemon=True)
            worker.start()
            import_worker = threading.Thread(target=ingest.loop, args=(store, stop), daemon=True)
            import_worker.start()
            print(f"KoboSync listening on {args.host}:{server.server_port}; daily handoff {args.hour:02d}:00 {args.zone}", flush=True)
            def terminate(signum, frame):
                raise KeyboardInterrupt
            previous_sigterm = signal.signal(signal.SIGTERM, terminate)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                stop.set()
                try:
                    server.server_close()
                finally:
                    signal.signal(signal.SIGTERM, previous_sigterm)
            return
        else:
            raise ValueError("Unknown command")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as e:
        p.exit(1, "KoboSync: " + str(e) + "\n")


if __name__ == "__main__":
    main()
