"""Exercise the production APK on an explicitly selected Android emulator.

Build both APKs first. This clears only the app data on the selected emulator;
physical devices are deliberately rejected. No Kobo or user's book is simulated
as real acceptance: this verifies Android downloads and playback lifecycle only.
"""
import argparse
import array
import json
import hashlib
import math
import subprocess
import shlex
import tempfile
import threading
import wave
from datetime import datetime, timezone
from pathlib import Path

from kobosync.pipeline import track_manifest
from kobosync.server import make_server
from kobosync.store import Store
from test_core import words


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adb", default=".tools/android-sdk/platform-tools/adb")
    p.add_argument("--adb-port", type=int, default=5039)
    p.add_argument("--serial", required=True)
    p.add_argument("--output", default=".tools/android-runtime-results.json")
    p.add_argument("--skip-install", action="store_true", help="Verify installed APK hashes instead of reinstalling")
    args = p.parse_args()
    journal = {"apk_sha256": hashlib.sha256(Path("android/build/kobosync.apk").read_bytes()).hexdigest(), "phases": {}}
    if not (args.serial.startswith("emulator-") or args.serial.startswith("127.0.0.1:")):
        p.error("Only an emulator serial is permitted; this test clears the app's data")
    adb = [str(Path(args.adb).resolve()), "-P", str(args.adb_port), "-s", args.serial]

    def command(*parts, timeout=180):
        # `adb shell` joins its arguments into a remote shell command; quote
        # there as well so empty values and file paths stay single arguments.
        remote = ["shell", shlex.join(parts[1:])] if parts[0] == "shell" else list(parts)
        try:
            result = subprocess.run(adb + remote, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"ADB operation timed out: {parts[:3]}\n{e.stdout!r}\n{e.stderr!r}") from e
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout

    def verify_installed(package, local):
        lines = command("shell", "pm", "path", package).strip().splitlines()
        if len(lines) != 1 or not lines[0].startswith("package:/data/app/") or not lines[0].endswith("/base.apk"):
            raise RuntimeError("Expected one installed APK for " + package)
        remote = lines[0].removeprefix("package:")
        actual = command("shell", "sha256sum", remote).split()[0]
        expected = hashlib.sha256(Path(local).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError("Installed APK differs from current build: " + package)
        print("Verified installed build: " + package, flush=True)

    def phase(name, server_url=""):
        command("shell", "input", "keyevent", "224")
        command("shell", "wm", "dismiss-keyguard")
        output = command("shell", "am", "instrument", "-w", "-r", "-e", "phase", name,
                         "-e", "server", server_url, "-e", "token", "runtime-test-token",
                         "dev.kobosync.test/dev.kobosync.PlaybackSmoke")
        passed = "PASS " + name in output and "INSTRUMENTATION_CODE: -1" in output
        journal["phases"][name] = dict(passed=passed, output=output)
        Path(args.output + ".journal.json").write_text(json.dumps(journal, indent=2))
        if not passed:
            raise AssertionError(output)
        print(output, flush=True)
        return output

    result = {"apk_sha256": journal["apk_sha256"]}
    if command("shell", "getprop", "ro.kernel.qemu").strip() != "1":
        p.error("The selected target is not an Android emulator")
    with tempfile.TemporaryDirectory(prefix="kobosync-android-runtime-") as temporary:
        root = Path(temporary)
        sources = []
        for index, frequency in enumerate((330, 440)):
            path = root / f"track{index}.wav"
            samples = array.array("h", (int(5000 * math.sin(2 * math.pi * frequency * i / 16000)) for i in range(16000)))
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                for _ in range(60):
                    out.writeframes(samples.tobytes())
            encoded = root / (f"track{index}.mp3" if index == 0 else f"track{index}.m4b")
            codec = ["-c:a", "libmp3lame", "-b:a", "64k"] if index == 0 else ["-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path)] + codec + [str(encoded)], check=True)
            sources.append(encoded)
        ebook = root / "Playback fixture.epub"
        ebook.write_bytes(b"Synthetic Android playback fixture; not a real ebook")
        store = Store(root / "data")
        store.set_value("token", "runtime-test-token")
        book = store.register("runtime", ebook.name, ebook, sources, "Android playback verification")
        second_text = "Second track captions continue while offline playback resumes after a restart safely.".split()
        store.save_transcript("runtime", words() + [dict(word=second_text[i % len(second_text)], track=1, start=i, end=i+1) for i in range(60)])
        manifest = dict(schema=1, revision="runtime-original", book_id=book["id"],
                        title=book["title"], author="", source_time=datetime.now(timezone.utc).isoformat(),
                        position=dict(track=0, seconds=10.0, score=1.0, passage="Synthetic playback fixture"),
                        tracks=track_manifest(book))
        store.publish(manifest)
        gate = dict(armed=False, started=threading.Event(), release=threading.Event())

        def test_server(port):
            server = make_server(store, "127.0.0.1", port)
            original = server.RequestHandlerClass

            class GatedHandler(original):
                def do_GET(self):
                    if self.path in ("/_runtime/audio-started", "/_runtime/release-audio"):
                        if self.path.endswith("release-audio"):
                            gate["release"].set()
                        body = b"1" if gate["started"].is_set() else b"0"
                        self.send_response(200)
                        self.send_header("Content-Length", "1")
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if gate["armed"] and self.path.startswith("/v1/audio/"):
                        gate["started"].set()
                        if not gate["release"].wait(30):
                            self.respond(503)
                            return
                    super().do_GET()

            server.RequestHandlerClass = GatedHandler
            return server

        server = test_server(0)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if args.skip_install:
                verify_installed("dev.kobosync", "android/build/kobosync.apk")
                verify_installed("dev.kobosync.test", "android/build/kobosync-test.apk")
            else:
                print("Installing app and instrumentation APKs", flush=True)
                command("install", "--no-incremental", "-r", "android/build/kobosync.apk")
                command("install", "--no-incremental", "-r", "-t", "android/build/kobosync-test.apk")
            print("Running online download/playback checks", flush=True)
            command("shell", "am", "force-stop", "dev.kobosync")
            command("shell", "pm", "clear", "dev.kobosync")
            command("shell", "settings", "put", "global", "airplane_mode_on", "0")
            command("shell", "svc", "wifi", "enable")
            url = f"http://10.0.2.2:{port}"
            result["online"] = phase("online", url)
            with store.connect() as c:
                assert c.execute("SELECT 1 FROM readiness WHERE revision='runtime-original'").fetchone(), "Phone did not acknowledge verified downloads"
            server.shutdown()
            server.server_close()
            thread.join(5)
            command("shell", "settings", "put", "global", "airplane_mode_on", "1")
            command("shell", "svc", "wifi", "disable")
            command("shell", "svc", "data", "disable")
            result["offline"] = phase("offline")
            command("shell", "settings", "put", "global", "airplane_mode_on", "0")
            command("shell", "svc", "wifi", "enable")
            manifest["revision"] = "runtime-next"
            manifest["position"] = dict(track=0, seconds=20.0, score=1.0, passage="New handoff playback fixture")
            store.publish(manifest)
            server = test_server(port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            result["new_handoff"] = phase("new", url)
            background = root / "background.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / "track1.wav"), "-c:a", "libmp3lame", "-b:a", "64k", str(background)], check=True)
            next_book = store.register("runtime-bg", "Background.epub", ebook, [background], "Background download verification")
            background_words = "This other book has different captions that stay with its own audio during downloads".split()
            store.save_transcript("runtime-bg", [dict(word=background_words[i % len(background_words)], track=0, start=i, end=i+1) for i in range(60)])
            next_manifest = dict(manifest, revision="runtime-background", book_id=next_book["id"], title=next_book["title"], tracks=track_manifest(next_book))
            store.publish(next_manifest)
            result["background_job"] = phase("background", url)
            with store.connect() as c:
                assert c.execute("SELECT 1 FROM readiness WHERE revision='runtime-background'").fetchone(), "Native job did not acknowledge the download"
            store.publish(dict(manifest, revision="runtime-switch-next"))
            result["playing_book_captions"] = phase("book_switch", url)
            command("shell", "settings", "put", "global", "airplane_mode_on", "1")
            command("shell", "svc", "wifi", "disable")
            command("shell", "svc", "data", "disable")
            result["cold_offline_rewind"] = phase("cold_seek")
            # Cancel the job while still offline. Otherwise reconnecting can
            # publish the next revision before the settings-change test starts.
            command("shell", "cmd", "jobscheduler", "cancel", "dev.kobosync", "601")
            command("shell", "settings", "put", "global", "airplane_mode_on", "0")
            command("shell", "svc", "wifi", "enable")
            store.publish(dict(manifest, revision="runtime-pairing-change"))
            gate["armed"] = True
            result["pairing_change"] = phase("pairing_change", url)
            with store.connect() as c:
                assert not c.execute("SELECT 1 FROM readiness WHERE revision='runtime-pairing-change'").fetchone(), "Changed pairing acknowledged an obsolete handoff"
            Path(args.output).write_text(json.dumps(result, indent=2))
        finally:
            server.shutdown()
            server.server_close()
            command("shell", "settings", "put", "global", "airplane_mode_on", "0")
            command("shell", "svc", "wifi", "enable")
            command("shell", "svc", "data", "enable")
            command("shell", "am", "force-stop", "dev.kobosync")
    print("Android runtime checks passed: verified audio/caption download, live captions, seek/pause/track changes, offline restart, new handoff, background job and playing-book caption isolation")


if __name__ == "__main__":
    main()
