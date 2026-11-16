"""Real KOReader LuaSocket checks for recovery after moving Wi-Fi networks."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest

from kobosync.server import make_server
from kobosync.store import Store


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.tools/browser-ui-linux/runtime/lib/koreader'
PLUGIN = Path(os.environ.get('KOBOSYNC_PLUGIN', ROOT / 'koreader/kobosync.koplugin'))


@unittest.skipUnless((RUNTIME / 'luajit').is_file(), 'Local Linux KOReader runtime needed')
class DiscoveryClientNetworkTests(unittest.TestCase):
    def setUp(self):
        # UDP connect selects the local route without sending a packet.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            try:
                route.connect(('192.0.2.1', 9))
                self.host = route.getsockname()[0]
            except OSError:
                self.skipTest('A private IPv4 interface is needed')
        parts = [int(part) for part in self.host.split('.')]
        if not (parts[0] == 10 or parts[0] == 172 and 16 <= parts[1] <= 31
                or parts[:2] == [192, 168]):
            self.skipTest('A private IPv4 interface is needed')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = Store(temporary.name)
        store.set_value('token', 'network-move-test-pairing')
        self.server = make_server(store, self.host, 0)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.expected = f'http://{self.host}:{self.server.server_port}'

    def run_client(self, old_host, setup=''):
        script = r'''
require('setupkoenv')
local discovery=dofile(arg[1])
local socket=require('socket')
''' + setup + r'''
local resolved=discovery.resolve(arg[2],'network-move-test-pairing')
assert(resolved==arg[3],'Could not find paired PC after network change: '..tostring(resolved))
print('Paired server rediscovered and HMAC verified')
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'check.lua'
            path.write_text(script)
            result = subprocess.run(
                [str(RUNTIME / 'luajit'), str(path), str(PLUGIN / 'discovery.lua'),
                 f'http://{old_host}:{self.server.server_port}', self.expected],
                cwd=RUNTIME, env=os.environ, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def same_network_old_host(self):
        prefix, last = self.host.rsplit('.', 1)
        return prefix + ('.254' if last != '254' else '.253')

    def test_saved_address_on_previous_network_recovers_on_current_interface(self):
        old_host = '192.168.254.254' if self.host.startswith('10.') else '10.254.254.253'
        self.run_client(old_host)

    def test_failed_route_probe_preserves_previous_subnet_recovery(self):
        self.run_client(self.same_network_old_host(),
                        "socket.udp=function() error('Route probe unavailable') end\n")

    def test_public_route_address_is_not_used_for_discovery(self):
        self.run_client(self.same_network_old_host(), r'''
socket.udp=function() return {
 setpeername=function() return true end,
 getsockname=function() return '8.8.8.8',12345 end,
 close=function() end,
} end
local client=socket.tcp()
local methods=debug.getmetatable(client).__index
local connect=methods.connect
methods.connect=function(self,host,port)
 assert(host:sub(1,6)~='8.8.8.','Public route address was scanned')
 return connect(self,host,port)
end
client:close()
''')
