import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
try:
    from lupa.luajit21 import LuaRuntime
except ImportError:
    LuaRuntime=None

CANDIDATE = Path(os.environ.get("KOBOSYNC_PLUGIN", str(Path(__file__).resolve().parents[1] / "koreader/kobosync.koplugin")))

@unittest.skipUnless(LuaRuntime, "Install the dev extra for plugin tests")
class PluginHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.epub = folder / "Book.epub"
        self.epub.write_bytes(b"same ebook file")
        self.plugin_dir = folder / "kobosync.koplugin"
        shutil.copytree(CANDIDATE, self.plugin_dir)
        (self.plugin_dir / "config.lua").write_text('return {server="http://localhost:8787",token="test"}')
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(r"""
clock=1000; connected=false; response_status=200; requests=0; messages={}; saved_settings={}
disk_settings={}; md5_starts=0; flushes=0; lookups=0; extracts=0; max_read=0; whole_reads=0; workers={}; next_worker=0; scheduled={}; ticks={}
os.time=function() return clock end
package.preload['ui/widget/container/widgetcontainer']=function() return {extend=function(_,v) return v end} end
package.preload['ui/uimanager']=function() return {
 show=function(_,v) messages[#messages+1]=v.text end,
 scheduleIn=function(_,delay,fn) scheduled[fn]=clock+delay end,
 unschedule=function(_,fn) scheduled[fn]=nil end,
 nextTick=function(_,fn) ticks[#ticks+1]=fn end} end
package.preload['ui/widget/infomessage']=function() return {new=function(_,v) return v end} end
package.preload['datastorage']=function() return {getSettingsDir=function() return '/tmp' end} end
function clone(v)
 if type(v)~='table' then return v end
 local t={}; for k,x in pairs(v) do t[k]=clone(x) end; return t
end
package.preload['luasettings']=function() return {open=function()
 local data=clone(disk_settings); saved_settings=data
 return {
 readSetting=function(_,k,default) return data[k] or default end,
 saveSetting=function(_,k,v) data[k]=v end,
 flush=function() flushes=flushes+1; disk_settings=clone(data) end} end} end
package.preload['ui/network/manager']=function() return {isConnected=function() return connected end} end
package.preload['socket.http']=function() return {request=function(req)
 requests=requests+1; sent_urls=sent_urls or {}; sent_urls[#sent_urls+1]=req.url
 return 1,response_status end} end
package.preload['ltn12']=function() return {source={string=function(s) return s end},sink={table=function() return function() return 1 end end}} end
package.preload['socketutil']=function() return {set_timeout=function() end,reset_timeout=function() end} end
package.preload['json']=function() return {encode=function(v) return encode_py(v) end,decode=function(v) return decode_py(v) end} end
package.preload['ffi/sha2']=function() return {md5=function(data)
 if data~=nil then return 'hash:'..data end
 md5_starts=md5_starts+1; local parts={}
 return function(chunk) if chunk then parts[#parts+1]=chunk; return end; return 'hash:'..table.concat(parts) end
end} end
package.preload['ffi/util']=function() return {
 runInSubProcess=function(fn,pipe)
  next_worker=next_worker+1; workers[next_worker]={fn=fn,done=false}; return next_worker,next_worker end,
 isSubProcessDone=function(id) return workers[id].done end,
 writeToFD=function(id,value) workers[id].output=value; return true end,
 readAllFromFD=function(id) workers[id].closed=true; return workers[id].output or '' end,
 terminateSubProcess=function(id) workers[id].killed=true; workers[id].done=true end} end
local real_open=io.open
io.open=function(path,mode)
 local f,err=real_open(path,mode); if not f then return nil,err end
 if mode~='rb' then return f end
 return {read=function(_,format)
   if format=='*a' then whole_reads=whole_reads+1 end
   local data=f:read(format); if data then max_read=math.max(max_read,#data) end; return data
  end,seek=function(_,whence,offset) return f:seek(whence,offset) end,close=function() return f:close() end}
end
function run_ticks() local old=ticks; ticks={}; for _,fn in ipairs(old) do fn() end end
function run_due()
 for _=1,1000 do
  local fn,due=nil,math.huge
  for f,t in pairs(scheduled) do if t<=clock and t<due then fn,due=f,t end end
  if not fn then return end; scheduled[fn]=nil; fn()
 end
 error('Unbounded timer loop')
end
function advance(seconds) clock=clock+seconds; run_due() end
function run_workers()
 for id,w in pairs(workers) do if not w.done then w.fn(id,id); w.done=true end end
end
function finish_sync() run_workers(); advance(1) end
function pending_count() local n=0; for _ in pairs(plugin.pending) do n=n+1 end; return n end
function latest_pending()
 local result
 for _,o in pairs(plugin.pending) do if not result or o.sequence>result.sequence then result=o end end
 return result
end
""")
        def unpack(v):
            if hasattr(v, 'items'):
                items = list(v.items())
                if items and all(isinstance(k, int) for k, _ in items) and {k for k, _ in items} == set(range(1,len(items)+1)):
                    return [unpack(v[i]) for i in range(1,len(items)+1)]
                return {str(k):unpack(x) for k,x in items}
            return v
        def pack(v):
            if isinstance(v, dict): return self.lua.table_from({k:pack(x) for k,x in v.items()})
            if isinstance(v, list): return self.lua.table_from([pack(x) for x in v])
            return v
        self.lua.globals().encode_py=lambda v:json.dumps(unpack(v),ensure_ascii=False)
        self.lua.globals().decode_py=lambda v:pack(json.loads(v))
        self.lua.globals().plugin_path=str(self.plugin_dir/'main.lua')
        self.lua.globals().ebook_path=str(self.epub)
        self.lua.execute(r"""
plugin=dofile(plugin_path); plugin.path=plugin_path:match('^(.*)/main.lua$'); page='page1'
plugin.ui={menu={registerToMainMenu=function() end},
 highlight={addToHighlightDialog=function(_,_,factory) highlight_factory=factory end,selected_text={pos0='selected-word'}},
 rolling={getLastProgress=function() return page end},
 document={file=ebook_path,info={has_pages=false},
  getNextVisibleWordEnd=function(_,xp) lookups=lookups+1; return xp..'x' end,
  getTextFromXPointers=function(_,xp) extracts=extracts+1; return xp..' one two three four five six seven eight nine ten eleven twelve' end}}
plugin:init()
""")
    def run_lua(self, source): self.lua.execute(source)
    def value(self, expr): return self.lua.eval(expr)

class PluginPerformanceTests(PluginHarness):
    def test_scroll_burst_does_no_capture_or_disk_io_until_idle(self):
        self.run_lua("for i=1,1000 do page='page'..i; plugin:onPageUpdate(); run_ticks(); advance(0.01) end")
        self.assertEqual(self.value('lookups'),0)
        self.assertEqual(self.value('flushes'),0)
        self.run_lua('advance(2)')
        self.assertEqual(self.value('extracts'),1)
        self.assertEqual(self.value('pending_count()'),1)
        self.assertEqual(self.value('latest_pending().locator'),'page1000')
        self.assertEqual(self.value('flushes'),0)
        self.run_lua('advance(31)')
        self.assertEqual(self.value('flushes'),1)

    def test_periodic_checkpoint_waits_if_scrolling_is_in_progress(self):
        self.run_lua('plugin:onPageUpdate(); advance(2); for i=1,5000 do page="p"..i; plugin:onPageUpdate(); advance(0.01) end')
        self.assertEqual(self.value('flushes'),0)
        self.run_lua('advance(2); advance(3)')
        self.assertEqual(self.value('flushes'),1)
        self.assertEqual(self.value('latest_pending().locator'),'p5000')

    def test_debounce_keeps_original_pre_cutoff_event_timestamp(self):
        self.run_lua("clock=21599; plugin:onPageUpdate(); run_ticks(); advance(2)")
        self.assertEqual(self.value('latest_pending().source_time'),'1970-01-01T05:59:59Z')

    def test_offline_queue_compacts_pages_but_preserves_hour_boundaries_and_books(self):
        self.run_lua("for i=1,200 do page='p'..i; plugin:capture() end")
        self.assertEqual(self.value('pending_count()'),1)
        self.run_lua("clock=3599; page='before'; plugin:capture(); clock=3600; page='exact'; plugin:capture(); clock=3601; page='after'; plugin:capture()")
        self.assertEqual(self.value('pending_count()'),3)
        self.run_lua("plugin.ui.document.file=ebook_path:gsub('Book.epub','Other.epub'); page='other'; plugin.epub_md5=nil")
        (Path(self.tmp.name)/'Other.epub').write_bytes(b'other edition')
        self.run_lua('plugin:capture()')
        self.assertEqual(self.value('pending_count()'),4)

    def test_suspend_and_close_save_latest_pending_position_immediately(self):
        self.run_lua("plugin:onPageUpdate(); run_ticks(); advance(2); page='final'; clock=clock+1; plugin:onPageUpdate(); plugin:onSuspend()")
        self.assertEqual(self.value('latest_pending().locator'),'final')
        self.assertEqual(self.value('flushes'),1)
        self.run_lua("page='closed-page'; plugin:onPageUpdate(); plugin:onCloseDocument()")
        self.assertEqual(self.value('latest_pending().locator'),'closed-page')
        self.assertEqual(self.value('flushes'),2)

    def test_explicit_marker_is_durable_and_not_replaced_by_idle_capture(self):
        self.run_lua('plugin:onPageUpdate(); highlight_factory(plugin.ui.highlight).callback(); advance(3)')
        self.assertEqual(self.value('latest_pending().locator'),'selected-word')
        self.assertEqual(self.value('flushes'),1)
        self.assertEqual(self.value('requests'),0)

    def test_hash_reads_bounded_chunks_and_is_cached_during_book_session(self):
        self.epub.write_bytes(b'a'*(2*1024*1024+31))
        self.run_lua('plugin:capture(); page="second"; plugin:capture()')
        self.assertEqual(self.value('whole_reads'),0)
        self.assertLessEqual(self.value('max_read'),65536)
        self.assertEqual(self.value('extracts'),2)
        self.assertEqual(self.value('md5_starts'),1)

    def test_network_work_waits_for_worker_and_failed_sync_keeps_queue(self):
        self.run_lua('plugin:capture(); connected=true; response_status=503; plugin:flush(true)')
        self.assertEqual(self.value('requests'),0)
        self.assertEqual(self.value('next_worker'),1)
        self.run_lua('finish_sync()')
        self.assertEqual(self.value('pending_count()'),1)
        self.run_lua('response_status=200; plugin:flush(true); finish_sync()')
        self.assertEqual(self.value('pending_count()'),0)
        self.assertIn('Reading position sent',self.value('messages[#messages]'))

    def test_worker_ack_does_not_drop_a_newer_scroll_position(self):
        self.run_lua('plugin:capture(); connected=true; plugin:flush(); page="new"; clock=clock+1; plugin:capture(); finish_sync()')
        self.assertEqual(self.value('pending_count()'),1)
        self.assertEqual(self.value('latest_pending().locator'),'new')

    def test_sync_is_single_worker_even_when_requested_repeatedly(self):
        self.run_lua('plugin:capture(); connected=true; for i=1,100 do plugin:flush() end')
        self.assertEqual(self.value('next_worker'),1)
        self.assertEqual(self.value('requests'),0)

    def test_closed_document_cannot_overwrite_a_new_reader_settings(self):
        self.run_lua('plugin:capture(); connected=true; plugin:flush(); plugin:onCloseDocument(); checkpoint=flushes; finish_sync()')
        self.assertEqual(self.value('flushes'),self.value('checkpoint'))
        self.assertEqual(self.value('workers[1].killed'),True)
        self.assertEqual(self.value('workers[1].closed'),True)

    def test_cancel_retries_if_child_process_group_was_not_ready(self):
        self.run_lua("plugin:capture(); connected=true; plugin:flush(); kills=0; package.loaded['ffi/util'].terminateSubProcess=function(id) kills=kills+1; if kills>1 then workers[id].done=true end end; plugin:onCloseDocument(); advance(1)")
        self.assertTrue(self.value('workers[1].done'))
        self.run_lua('advance(1)')
        self.assertTrue(self.value('workers[1].closed'))

    def test_hung_worker_is_killed_without_removing_pending_progress(self):
        self.run_lua('plugin:capture(); connected=true; plugin:flush(); advance(901); advance(1)')
        self.assertTrue(self.value('workers[1].killed'))
        self.assertTrue(self.value('workers[1].closed'))
        self.assertEqual(self.value('pending_count()'),1)



    def test_switching_book_with_same_locator_rehashes_exact_edition(self):
        (Path(self.tmp.name)/'Other.epub').write_bytes(b'other exact edition')
        self.run_lua('plugin:capture("page1",true); plugin.ui.document.file=ebook_path:gsub("Book.epub","Other.epub"); plugin:capture()')
        self.assertEqual(self.value('latest_pending().content_id'),'Other.epub')
        self.assertEqual(self.value('md5_starts'),2)

    def test_worker_batches_are_bounded_and_drain_without_losing_history(self):
        self.run_lua('for i=1,70 do clock=clock+3600; page="p"..i; plugin:capture() end; connected=true; plugin:flush(); finish_sync()')
        self.assertEqual(self.value('requests'),32)
        self.assertEqual(self.value('pending_count()'),38)
        self.run_lua('advance(1); finish_sync(); advance(1); finish_sync()')
        self.assertEqual(self.value('pending_count()'),0)
        self.assertEqual(self.value('requests'),70)

    def test_late_suspend_event_after_close_cannot_save_old_settings(self):
        self.run_lua('plugin:capture(); plugin:onCloseDocument(); before=flushes; plugin:onSuspend()')
        self.assertEqual(self.value('flushes'),self.value('before'))

    def test_legacy_queue_compacts_at_load_and_keeps_latest_sequence(self):
        self.run_lua("for i=1,200 do disk_settings.pending=disk_settings.pending or {}; disk_settings.pending[tostring(i)]={reader='koreader',content_id='Book.epub',epub_md5='same',locator='p'..i,passage='one two three four five six seven eight nine ten',source_time='1970-01-01T00:16:40Z',sequence=i} end; disk_settings.sequence=200; plugin:init()")
        self.assertEqual(self.value('pending_count()'),1)
        self.assertEqual(self.value('latest_pending().sequence'),200)
        self.assertEqual(self.value('flushes'),0)

    def test_real_koreader_sha2_matches_registered_fingerprint(self):
        runtime=Path(os.environ.get('KOREADER_RUNTIME','data/setup/kobo/.adds/koreader')).resolve()
        if not (runtime/'ffi/sha2.lua').is_file(): self.skipTest('Local KOReader runtime needed for native hash check')
        data=(b'unchanged exact EPUB bytes\x00' * 5000)+b'final'
        self.epub.write_bytes(data)
        self.lua.globals().sha2_path=str(runtime/'ffi/sha2.lua')
        self.run_lua("package.loaded['ffi/sha2']=dofile(sha2_path); package.preload['ffi/sha2']=nil; local ui=plugin.ui; plugin=dofile(plugin_path); plugin.path=plugin_path:match('^(.*)/main.lua$'); plugin.ui=ui; plugin:init(); plugin:capture()")
        self.assertEqual(self.value('latest_pending().epub_md5'),hashlib.md5(data).hexdigest())
        self.assertEqual(self.value('whole_reads'),0)
        self.assertLessEqual(self.value('max_read'),32768)

    def test_actual_koreader_fork_returns_before_slow_job_and_reaps_pipe(self):
        runtime=Path(os.environ.get('KOREADER_RUNTIME','data/setup/kobo/.adds/koreader')).resolve()
        if not (runtime/'ffi/util.lua').is_file(): self.skipTest('Local KOReader runtime needed for native worker check')
        self.lua.globals().runtime_path=str(runtime)
        self.run_lua(r"""
package.path=runtime_path..'/?.lua;'..package.path
package.preload['libs/libkoreader-lfs']=function() return {} end
package.loaded['ffi/util']=nil; package.preload['ffi/util']=nil
native_ffiutil=require('ffi/util')
package.loaded['json']={encode=function() return '{"sent":["1"]}' end,decode=function() return {sent={'1'}} end}
package.loaded['socket.http']={request=function(request)
 assert(request.headers.Authorization=='Bearer test')
 native_ffiutil.usleep(500000); return 1,200
end}
package.loaded['kobosync.worker']=nil
local ui=plugin.ui; plugin=dofile(plugin_path); plugin.path=plugin_path:match('^(.*)/main.lua$'); plugin.ui=ui; plugin:init()
""")
        start=time.monotonic()
        self.run_lua('plugin:capture(); connected=true; plugin:flush(true)')
        self.assertLess(time.monotonic()-start,0.2,'Network worker blocked caller')
        start=time.monotonic()
        self.run_lua('advance(1)')
        self.assertLess(time.monotonic()-start,0.1,'Polling blocked while child was busy')
        self.assertEqual(self.value('pending_count()'),1)
        self.run_lua('page="new"; clock=clock+1; plugin:capture()')
        time.sleep(0.55)
        self.run_lua('advance(1)')
        self.assertEqual(self.value('pending_count()'),1)
        self.assertEqual(self.value('latest_pending().locator'),'new')
        self.run_lua('plugin:onCloseDocument(); advance(1)')

class PluginTests(PluginHarness):
    def test_offline_progress_survives_and_flushes_only_on_success(self):
        self.lua.execute('plugin:capture(); plugin:flush(); finish_sync()')
        self.assertIsNotNone(self.lua.eval('latest_pending()'))
        self.lua.execute(' connected=true; response_status=401; plugin:flush(); finish_sync()')
        self.assertIsNotNone(self.lua.eval('latest_pending()'))
        self.lua.execute('response_status=200; plugin:flush(); finish_sync()')
        self.assertIsNone(self.lua.eval('latest_pending()'))

    def test_selected_resume_anchor_survives_suspend_until_page_changes(self):
        self.lua.execute('plugin:capture("selected-word", true); plugin:onSuspend()')
        self.assertEqual(self.lua.eval('latest_pending().locator'), "selected-word")
        self.lua.execute('page="page2"; plugin:onPageUpdate(); run_ticks(); advance(2)')
        self.assertEqual(self.lua.eval('latest_pending().locator'), "page2")

    def test_unchanged_page_keeps_actual_reading_event_time(self):
        self.lua.execute('plugin:capture(); initial_time=latest_pending().source_time; clock=2000; plugin:capture()')
        self.assertEqual(self.lua.eval('latest_pending().source_time'), self.lua.eval('initial_time'))

    def test_page_event_captures_after_reader_updates_xpointer(self):
        self.lua.execute('plugin:capture(); plugin:onPageUpdate(); page="page2"; run_ticks(); advance(2)')
        self.assertEqual(self.lua.eval('latest_pending().locator'), "page2")

    def test_offline_queue_preserves_pre_morning_capture(self):
        self.lua.execute('plugin:capture(); clock=clock+3600; page="page2"; plugin:capture()')
        self.assertEqual(self.lua.eval('pending_count()'), 2)

    def test_manual_send_does_not_replace_selected_anchor_or_time(self):
        self.lua.execute('plugin:capture("selected-word",true); initial_time=latest_pending().source_time; clock=clock+3600; menu={}; plugin:addToMainMenu(menu); menu.kobosync.callback()')
        self.assertEqual(self.lua.eval('latest_pending().locator'), "selected-word")
        self.assertEqual(self.lua.eval('latest_pending().source_time'), self.lua.eval('initial_time'))

    def test_network_failure_does_not_block_once_per_queued_page(self):
        self.lua.execute('for i=1,25 do clock=clock+3600; page="page"..i; plugin:capture() end;  connected=true; response_status=503; plugin:flush(); finish_sync()')
        self.assertEqual(self.lua.eval('requests'), 1)
        self.assertEqual(self.lua.eval('pending_count()'), 25)

    def test_resume_button_reports_that_offline_marker_is_saved(self):
        self.lua.execute('highlight_factory(plugin.ui.highlight).callback(); finish_sync()')
        self.assertEqual(self.lua.eval('latest_pending().locator'), 'selected-word')
        self.assertGreater(self.lua.eval('#messages'), 0, 'Tapping Resume audio here must not be silent when Wi-Fi is off')
        self.assertIn('saved locally', self.lua.eval('messages[#messages]'))
        self.assertEqual(self.lua.eval('requests'), 0)

    def test_resume_button_sends_selected_anchor_and_confirms_when_online(self):
        self.lua.execute(' connected=true; highlight_factory(plugin.ui.highlight).callback(); finish_sync()')
        self.assertEqual(self.lua.eval('requests'), 1)
        self.assertEqual(self.lua.eval('pending_count()'), 0)
        self.assertIn('Reading position sent', self.lua.eval('messages[#messages]'))

    def test_resume_button_sends_over_lan_without_public_dns(self):
        self.lua.execute('connected=true; online=false; highlight_factory(plugin.ui.highlight).callback(); finish_sync()')
        self.assertEqual(self.lua.eval('requests'), 1, 'Local server sync must not require public DNS')
        self.assertEqual(self.lua.eval('pending_count()'), 0)
        self.assertIn('Reading position sent', self.lua.eval('messages[#messages]'))

    def test_failed_identity_check_preserves_pending_and_sends_no_token(self):
        self.lua.execute('plugin.discovery.resolve=function() return nil end; connected=true; plugin:capture(); plugin:flush(true); finish_sync()')
        self.assertEqual(self.lua.eval('requests'), 0)
        self.assertEqual(self.lua.eval('pending_count()'), 1)
        self.assertIn('paired server', self.lua.eval('messages[#messages]'))

    def test_changed_address_is_used_and_cached_without_losing_marker(self):
        self.lua.execute('plugin.discovery.resolve=function() return "http://10.0.0.49:8787" end; connected=true; plugin:capture("selected-word",true); plugin:flush(); finish_sync()')
        self.assertEqual(self.lua.eval('plugin.config.server'), 'http://10.0.0.49:8787')
        self.assertEqual(self.lua.eval('saved_settings.endpoint_cache.server'), 'http://10.0.0.49:8787')
        self.assertEqual(self.lua.eval('pending_count()'), 0)

    def test_network_reconnect_schedules_queued_handoff(self):
        self.lua.execute('plugin:capture(); plugin:onNetworkConnected(); connected=true; advance(1); finish_sync()')
        self.assertEqual(self.lua.eval('requests'), 1)
        self.assertEqual(self.lua.eval('pending_count()'), 0)

    def test_verified_address_cache_survives_restart_but_not_pairing_edit(self):
        self.lua.execute('plugin.discovery.resolve=function() return "http://10.0.0.49:8787" end; connected=true; plugin:capture(); plugin:flush(); finish_sync(); plugin:init()')
        self.assertEqual(self.lua.eval('plugin.config.server'), 'http://10.0.0.49:8787')
        (self.plugin_dir / 'config.lua').write_text('return {server="http://localhost:8787",token="changed"}')
        self.lua.execute('plugin:init()')
        self.assertEqual(self.lua.eval('plugin.config.server'), 'http://localhost:8787')

if __name__ == '__main__': unittest.main()

class PluginImportTests(PluginHarness):
    def setUp(self):
        super().setUp()
        self.run_lua(r'''
upload_status=202; needs_epub=true; uploaded=''; progress_bodies={}; upload_count=0
require('socket.http').request=function(req)
 requests=requests+1
 if req.url:match('/v1/progress$') then
  progress_bodies[#progress_bodies+1]=req.source
  req.sink(needs_epub and '{"needs_epub":true,"book_state":"awaiting_epub"}' or '{"needs_epub":false,"book_state":"preparing","message":"Reading position saved. Preparing narration on the PC."}')
  return 1,200
 end
 upload_count=upload_count+1; upload_url=req.url; upload_length=tonumber(req.headers['Content-Length'])
 while true do local chunk=req.source(); if not chunk then break end; uploaded=uploaded..chunk end
 req.sink(upload_status==202 and '{"needs_epub":false,"book_state":"queued","message":"Reading position sent. Narration queued on the PC."}' or '{"error":"Upload interrupted"}')
 return 1,upload_status
end
''')

    def test_new_ebook_uploads_exact_bytes_off_thread_without_private_path_in_progress(self):
        self.run_lua('plugin:capture(); connected=true; plugin:flush(true)')
        self.assertEqual(self.value('requests'),0)
        self.run_lua('finish_sync()')
        self.assertEqual(self.value('upload_count'),1)
        self.assertEqual(self.value('uploaded'),self.epub.read_text())
        self.assertEqual(self.value('upload_length'),self.epub.stat().st_size)
        self.assertIn('/v1/ebooks/',self.value('upload_url'))
        self.assertNotIn(str(self.epub),self.value('progress_bodies[1]'))
        self.assertEqual(self.value('pending_count()'),0)
        self.assertIn('Narration queued',self.value('messages[#messages]'))

    def test_failed_upload_keeps_position_and_retries_without_recapturing(self):
        self.run_lua('plugin:capture(); connected=true; upload_status=503; plugin:flush(true); finish_sync()')
        self.assertEqual(self.value('upload_count'),1)
        self.assertEqual(self.value('pending_count()'),1)
        self.run_lua('upload_status=202; plugin:flush(true); finish_sync()')
        self.assertEqual(self.value('pending_count()'),0)
        self.assertEqual(self.value('upload_count'),2)

    def test_upload_streams_bounded_binary_chunks(self):
        data=(b'epub bytes\x00' * 20000) + b'end'
        self.epub.write_bytes(data)
        self.run_lua("plugin.epub_md5=string.rep('a',32); plugin.epub_path=ebook_path; plugin:capture(); connected=true; plugin:flush(); finish_sync()")
        self.assertEqual(self.value('uploaded').encode(),data)
        self.assertLessEqual(self.value('max_read'),32768)
        self.assertEqual(self.value('whole_reads'),0)

    def test_background_upload_can_progress_for_more_than_a_minute(self):
        self.run_lua('plugin:capture(); connected=true; plugin:flush(true); advance(130)')
        self.assertFalse(bool(self.value('workers[1].killed')))
        self.run_lua('finish_sync()')
        self.assertEqual(self.value('upload_count'),1)
        self.assertEqual(self.value('pending_count()'),0)

    def test_oversized_epub_stays_queued_without_starting_upload(self):
        with self.epub.open('wb') as f:
            f.truncate(256*1024*1024+1)
        self.run_lua("plugin.epub_md5=string.rep('a',32); plugin.epub_path=ebook_path; plugin:capture(); connected=true; plugin:flush(true); finish_sync()")
        self.assertEqual(self.value('upload_count'),0)
        self.assertEqual(self.value('pending_count()'),1)
        self.assertIn('256 MiB',self.value('messages[#messages]'))

    def test_81_mib_epub_streams_in_chunks_instead_of_being_rejected(self):
        with self.epub.open('wb') as f:
            f.truncate(85239102)
        self.run_lua(r'''
local send=require('socket.http').request
large_upload_bytes=0
require('socket.http').request=function(req)
 if req.url:match('/v1/ebooks/') then
  upload_count=upload_count+1
  while true do local data=req.source(); if not data then break end; large_upload_bytes=large_upload_bytes+#data end
  req.sink('{"book_state":"queued","message":"Narration queued on the PC."}')
  return 1,202
 end
 return send(req)
end
plugin.epub_md5=string.rep('a',32); plugin.epub_path=ebook_path; plugin:capture(); connected=true; plugin:flush(true); finish_sync()
''')
        self.assertEqual(self.value('large_upload_bytes'),85239102)
        self.assertEqual(self.value('pending_count()'),0)
        self.assertLessEqual(self.value('max_read'),32768)

    def test_distinct_upload_chunks_do_not_accumulate_the_book_in_lua_memory(self):
        size=2600*32768
        with self.epub.open('wb') as f:
            for index in range(2600):
                f.write(index.to_bytes(4,'little')+b'x'*(32768-4))
        self.run_lua(r'''
local send=require('socket.http').request
collectgarbage('collect'); upload_base_kb=collectgarbage('count'); upload_peak_kb=upload_base_kb
large_upload_bytes=0
require('socket.http').request=function(req)
 if req.url:match('/v1/ebooks/') then
  while true do
   local data=req.source(); upload_peak_kb=math.max(upload_peak_kb,collectgarbage('count'))
   if not data then break end
   large_upload_bytes=large_upload_bytes+#data
  end
  req.sink('{"book_state":"queued","message":"Narration queued."}'); return 1,202
 end
 return send(req)
end
plugin.epub_md5=string.rep('a',32); plugin.epub_path=ebook_path; plugin:capture(); connected=true; plugin:flush(); finish_sync()
''')
        self.assertEqual(self.value('large_upload_bytes'),size)
        self.assertLess(self.value('upload_peak_kb-upload_base_kb'),8*1024,'The upload retained the whole book in Lua memory')

    def test_old_oversized_book_does_not_hide_success_for_current_book(self):
        old=Path(self.tmp.name)/'Older illustrated book.epub'
        with old.open('wb') as f:
            f.truncate(300*1024*1024)
        self.lua.globals().older_ebook_path=str(old)
        self.run_lua(r'''
plugin.ui.document.file=older_ebook_path; plugin.epub_path=older_ebook_path; plugin.epub_md5=string.rep('a',32); plugin:capture()
clock=clock+1; page='current'; plugin.ui.document.file=ebook_path; plugin.epub_path=ebook_path; plugin.epub_md5=string.rep('b',32); plugin:capture()
local send=require('socket.http').request
require('socket.http').request=function(req)
 if req.url:match('/v1/progress$') and req.source:find('Book.epub',1,true) then
  req.sink('{"needs_epub":false,"book_state":"preparing","message":"Reading position sent. Preparing narration on the PC."}')
  return 1,200
 end
 return send(req)
end
connected=true; plugin:flush(true); finish_sync()
''')
        self.assertEqual(self.value('pending_count()'),1)
        message=self.value('messages[#messages]')
        self.assertIn('Preparing narration',message)
        self.assertIn('Older illustrated book.epub',message)
        self.assertIn('300.0 MiB',message)

    def test_preparing_book_acknowledges_position_and_reports_preparation(self):
        self.run_lua('needs_epub=false; plugin:capture(); connected=true; plugin:flush(true); finish_sync()')
        self.assertEqual(self.value('upload_count'),0)
        self.assertEqual(self.value('pending_count()'),0)
        self.assertIn('Preparing narration',self.value('messages[#messages]'))

    def test_pending_local_path_survives_restart_for_background_upload(self):
        self.run_lua('plugin:capture(); plugin:init(); connected=true; plugin:flush(); finish_sync()')
        self.assertEqual(self.value('upload_count'),1)
        self.assertEqual(self.value('pending_count()'),0)

    def test_legacy_pending_current_book_can_be_uploaded_without_new_marker(self):
        self.run_lua('plugin:capture(); latest_pending().ebook_path=nil; connected=true; plugin:flush(); finish_sync()')
        self.assertEqual(self.value('upload_count'),1)
        self.assertEqual(self.value('pending_count()'),0)

    def test_missing_ebook_does_not_discard_pending_position(self):
        self.run_lua('plugin:capture(); latest_pending().ebook_path="/missing/book.epub"; plugin.ui.document.file="/different/book.epub"; connected=true; plugin:flush(true); finish_sync()')
        self.assertEqual(self.value('upload_count'),0)
        self.assertEqual(self.value('pending_count()'),1)
        self.assertIn('EPUB',self.value('messages[#messages]'))

    def test_server_error_shows_message_without_raw_json(self):
        self.run_lua("require('socket.http').request=function(req) req.sink('{\"error\":\"Unsupported EPUB\"}'); return 1,400 end; plugin:capture(); connected=true; plugin:flush(true); finish_sync()")
        self.assertIn('Unsupported EPUB',self.value('messages[#messages]'))
        self.assertNotIn('{',self.value('messages[#messages]'))

