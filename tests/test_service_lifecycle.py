"""A normal service termination must release its browser/profile resources."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'posix', 'WSL/Linux coordinator lifecycle')
class ServiceLifecycleTests(unittest.TestCase):
    def test_sigterm_runs_server_cleanup(self):
        code = '''
import signal,sys
from pathlib import Path
from kobosync import cli
root=Path(sys.argv[1])
class Server:
 server_port=0
 def serve_forever(self):
  (root/'ready').write_text('ready')
  while True: signal.pause()
 def server_close(self): (root/'closed').write_text('closed')
cli.make_server=lambda *args: Server()
cli.loop=lambda *args: None
cli.ingest.loop=lambda *args: None
cli.main(['--data',str(root/'data'),'serve'])
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = subprocess.Popen([sys.executable, '-c', code, directory],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 5
                while not (root / 'ready').exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root / 'ready').exists(), 'Service did not start')
                child.send_signal(signal.SIGTERM)
                child.wait(timeout=5)
                self.assertEqual(child.returncode, 0)
                self.assertEqual((root / 'closed').read_text(), 'closed')
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
