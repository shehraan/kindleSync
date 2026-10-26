"""Opt-in native Lua 5.1/LuaSocket discovery check, using KOReader's sha2 module."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from kobosync.server import make_server
from kobosync.store import Store


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--lua', required=True)
    p.add_argument('--old-host', default='172.31.127.198')
    p.add_argument('--actual-host', default='172.31.127.199')
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        store = Store(Path(temp))
        store.set_value('token', 'lua-runtime-test-token')
        server = make_server(store, '0.0.0.0', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://{args.old_host}:{server.server_port}'
        expected = f'http://{args.actual_host}:{server.server_port}'
        try:
            script = '''
local discovery = dofile('koreader/kobosync.koplugin/discovery.lua')
local sha2 = require('ffi/sha2')
local nonce=string.rep('a',32)
local identity=string.rep('b',32)
local message='kobosync-discovery-v1\\n'..nonce..'\\n'..identity
local body=message..'\\n'..sha2.hmac(sha2.sha256,'test-token',message)..'\\n'
assert(discovery.verify(body,nonce,'test-token'))
assert(not discovery.verify(body,nonce,'wrong-token'))
assert(not discovery.verify(body,string.rep('c',32),'test-token'))
assert(not discovery.verify(body..'extra',nonce,'test-token'))
assert(not discovery.private_origin('http://8.8.8.8:8787'))
assert(not discovery.private_origin('http://10.0.0.999:8787'))
assert(not discovery.private_origin('http://10.0.0.1:8787/path'))
assert(not discovery.private_origin('https://10.0.0.1:8787'))
assert(not discovery.private_origin('http://user@10.0.0.1:8787'))
assert(discovery.private_origin('http://10.0.0.1:8787/').port==8787)
assert(discovery.private_origin('http://10.0.0.1/').port==80)
local origin, expected=arg[1],arg[2]
assert(discovery.resolve(origin,'lua-runtime-test-token')==expected,'Could not rediscover paired server')
assert(discovery.resolve(expected,'lua-runtime-test-token')==expected,'Could not verify current server')
assert(discovery.resolve(expected..'/','lua-runtime-test-token')==expected,'Trailing slash skipped verification')
print('Lua 5.1 real-socket discovery passed: nonce/HMAC checks, private subnet restriction and changed address recovery')
'''
            subprocess.run([args.lua, '-', origin, expected], input=script, text=True, check=True, timeout=25, env=os.environ)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
