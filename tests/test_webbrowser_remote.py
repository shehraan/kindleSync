import ctypes
import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
try:
    import lupa.luajit21
    from lupa.luajit21 import LuaRuntime
except ImportError:
    LuaRuntime = None
ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'koreader/webbrowser.koplugin'
RUNTIME = ROOT / '.tools/browser-ui-linux/runtime/lib/koreader'


def png(width=600, height=800):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * width) * height)) + chunk(b'IEND', b''))


@unittest.skipUnless(LuaRuntime and (RUNTIME/'common/lpeg.so').is_file(), 'Local Linux KOReader runtime needed for transport checks')
class RemoteTransportTests(unittest.TestCase):
    def setUp(self):
        # Native KOReader LPeg uses Lua symbols exported by the Lupa runtime.
        ctypes.CDLL(lupa.luajit21.__file__, mode=ctypes.RTLD_GLOBAL)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
        g = self.lua.globals()
        g.plugin_dir, g.runtime_dir, g.cache_home = str(PLUGIN).encode(), str(RUNTIME).encode(), self.tmp.name.encode()
        self.body = png()
        g.frame_data = self.body
        g.reply_json = json.dumps(self.metadata()).encode()
        self.lua.execute(b'''
package.path=plugin_dir..'/?.lua;'..runtime_dir..'/?.lua;'..runtime_dir..'/common/?.lua;'..runtime_dir..'/frontend/?.lua;'..package.path
package.cpath=runtime_dir..'/common/?.so;'..package.cpath
package.preload.datastorage=function() return {getDataDir=function() return cache_home end} end
package.preload['libs/libkoreader-lfs']=function() return {symlinkattributes=function() return nil end} end
package.preload.util=function() return {makePath=function(path) os.execute('mkdir -p '..path);return true end} end
package.preload.socketutil=function() return {set_timeout=function() end,reset_timeout=function() end} end
requests={};status=200;frame_status=200;throw_request=false
package.preload.webbrowser_http=function() return {request=function(req)
 requests[#requests+1]=req
 if throw_request then error('sensitive request detail') end
 local bytes=req.method=='POST' and reply_json or frame_data
 if req.source then local t={} while true do local c=req.source();if not c then break end;t[#t+1]=c end;req.body=table.concat(t) end
 for i=1,#bytes,16384 do local ok,err=req.sink(bytes:sub(i,i+16383));if not ok then return nil,err end end
 return 1,req.method=='POST' and status or frame_status,{['content-type']=req.method=='POST' and 'application/json' or 'image/png'}
end} end
''')

    def metadata(self, **changes):
        result = dict(session='a'*32, generation=3, url='https://example.org/', title='Page',
                      can_back=True, can_forward=False, zoom=2, width=600, height=800,
                      frame_url='/v1/browser/frame/'+'a'*32+'/3', frame_bytes=len(self.body),
                      frame_sha256=hashlib.sha256(self.body).hexdigest(), focus=dict(kind='none', value=''))
        result.update(changes)
        return result

    def load(self):
        self.assertTrue((PLUGIN/'webbrowser_remote_transport.lua').is_file(), 'Remote frame transport is missing')
        self.lua.execute(b"Transport=require('webbrowser_remote_transport')")

    def test_authenticated_action_and_frame_publish_only_verified_file(self):
        self.load()
        self.lua.execute(b'''
result,err=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open',width=600,height=800},'abcdef01-1')
assert(result,err);assert(result.path and result.frame_data==nil and #requests==2)
assert(requests[1].headers.Authorization=='Bearer paired-secret' and requests[2].headers.Authorization=='Bearer paired-secret')
assert(requests[1].redirect==false and requests[2].redirect==false)
assert(require('json').decode(requests[1].body).action=='open')
''')
        path = Path(self.lua.globals().result[b'path'].decode())
        self.assertTrue(path.is_relative_to(Path(self.tmp.name)))
        self.assertEqual(path.read_bytes(), self.body)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_frame_url_cannot_send_token_to_another_host_or_escape_cache(self):
        self.load()
        for url in ('https://evil.example/image.png', '/v1/browser/frame/../3', '/v1/browser/frame/'+'b'*32+'/3', '/v1/browser/frame/'+'a'*32+'/4'):
            self.lua.globals().reply_json = json.dumps(self.metadata(frame_url=url)).encode()
            self.lua.execute(b"requests={};local r=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1');assert(not r and #requests==1)")
        self.assertEqual(list(Path(self.tmp.name).rglob('*.png')), [])

    def test_length_hash_dimensions_and_overflow_reject_incomplete_frames(self):
        self.load()
        for change in ({'frame_bytes':len(self.body)+1}, {'frame_sha256':'f'*64}, {'width':601}, {'frame_bytes':8*1024*1024+1}):
            self.lua.globals().reply_json = json.dumps(self.metadata(**change)).encode()
            self.lua.execute(b"local r=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1');assert(not r)")
            self.assertEqual(list(Path(self.tmp.name).rglob('*.png*')), [])

    def test_streaming_reclaims_distinct_chunks_and_returns_no_binary(self):
        self.load()
        self.lua.globals().prefix = self.body[:24]
        self.lua.execute(b'''
local sha=require('ffi/sha2').sha256;local hash=sha();hash(prefix)
for i=1,384 do hash(string.rep(string.char(i%256),16380)..string.format('%04d',i)) end
local meta=require('json').decode(reply_json);meta.frame_bytes=#prefix+384*16384;meta.frame_sha256=hash();reply_json=require('json').encode(meta)
package.loaded.webbrowser_http.request=function(req)
 if req.method=='POST' then req.sink(reply_json);return 1,200,{} end
 assert(req.sink(prefix))
 for i=1,384 do assert(req.sink(string.rep(string.char(i%256),16380)..string.format('%04d',i))) end
 return 1,200,{['content-type']='image/png'}
end
collectgarbage('collect');collectgarbage('stop');local before=collectgarbage('count')
local result,err=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1')
assert(result,err);assert(result.frame_data==nil and result.path and #require('json').encode(result)<16384)
assert(collectgarbage('count')-before<2048,'Streaming retained binary chunks');collectgarbage('restart')
''')

    def test_http_errors_do_not_publish_frame_or_leak_request_details(self):
        self.load()
        self.lua.execute(b'''
status=409;local r,e,code=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='click'},'abcdef01-1')
assert(not r and code==409 and #requests==1)
throw_request=true;r,e=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1')
assert(not r and not e:find('sensitive',1,true))
''')

    def test_lan_requests_bypass_proxy_and_restore_it_after_failure(self):
        self.load()
        self.lua.execute(b'''
package.loaded['socket.http']={PROXY='http://proxy.example:8888'}
local send=package.loaded.webbrowser_http.request
package.loaded.webbrowser_http.request=function(req) assert(package.loaded['socket.http'].PROXY==nil);return send(req) end
local r,e=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1')
assert(r,e);assert(package.loaded['socket.http'].PROXY=='http://proxy.example:8888')
throw_request=true;r,e=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-2')
assert(not r and package.loaded['socket.http'].PROXY=='http://proxy.example:8888')
''')

    def test_cancel_cleanup_cannot_delete_files_outside_owned_cache(self):
        self.load()
        self.lua.execute(b'''
local r=assert(Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1'))
Transport.discard('abcdef01-1')
assert(io.open(r.path,'rb')==nil)
local file=assert(io.open(cache_home..'/unrelated.png','wb'));file:write('keep');file:close()
Transport.remove(cache_home..'/unrelated.png');Transport.discard('../unrelated')
assert(io.open(cache_home..'/unrelated.png','rb'))
''')

    def test_cache_works_on_filesystems_without_unix_permissions(self):
        self.lua.execute(b'''
require('ffi/sha2')
package.loaded.ffi={cdef=function() end,C={chmod=function() return -1 end}}
''')
        self.load()
        self.lua.execute(b"local r,e=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1');assert(r,e)")

    def test_disk_flush_failure_never_publishes_cached_frame(self):
        self.load()
        self.lua.execute(b'''
local native_open=io.open
io.open=function(path,mode)
 local file,err=native_open(path,mode)
 if not file or not path:match('%.png%.part$') then return file,err end
 return {write=function(_,chunk) return file:write(chunk) end,close=function() file:close();return nil,'Disk full' end}
end
local r=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='open'},'abcdef01-1');assert(not r)
''')
        self.assertEqual(list(Path(self.tmp.name).rglob('*.png*')), [])

    def test_country_dropdown_frame_accepts_249_options_with_bounded_metadata(self):
        self.load()
        options = [dict(label=f'Country {i:03d}', value=f'C{i:03d}') for i in range(1, 250)]
        encoded = json.dumps(self.metadata(focus=dict(kind='select', value='C001', options=options))).encode()
        self.assertLess(len(encoded), 32768)
        self.lua.globals().reply_json = encoded
        self.lua.execute(b'''
local r,e=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='click'},'abcdef01-1')
assert(r,e);assert(#r.focus.options==249 and r.focus.options[249].value=='C249')
''')

    def test_dropdown_over_512_options_or_metadata_budget_is_rejected(self):
        self.load()
        for count, label in ((513, 'Country'), (249, 'x' * 256)):
            options = [dict(label=label, value=str(i)) for i in range(count)]
            self.lua.globals().reply_json = json.dumps(self.metadata(focus=dict(kind='select', value='0', options=options))).encode()
            self.lua.execute(b'''
requests={};local r=Transport.exchange('http://192.168.1.5:8787','paired-secret',{action='click'},'abcdef01-1')
assert(not r and #requests==1,'Invalid dropdown metadata fetched a binary frame')
''')
        self.assertEqual(list(Path(self.tmp.name).rglob('*.png*')), [])


@unittest.skipUnless(LuaRuntime and (RUNTIME/'common/socket/url.lua').is_file(), 'Local KOReader LuaSocket runtime needed for client checks')
class RemoteControllerTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().plugin_dir=str(PLUGIN)
        self.lua.globals().runtime_dir=str(RUNTIME)
        self.lua.execute(r'''
package.path=plugin_dir..'/?.lua;'..runtime_dir..'/common/?.lua;'..package.path
package.preload.socket=function() return {} end
package.preload.util=function() return {} end
settings={};dialogs={};messages={};requests={};cancelled=false;late_close=false;failure=false;conflict=false;removed={}
package.preload.datastorage=function() return {getDataDir=function() return '/unused' end,getSettingsDir=function() return '/unused/settings' end} end
package.preload.luasettings=function() return {open=function() return {readSetting=function(_,k,d) if settings[k]~=nil then return settings[k] end;return d end,saveSetting=function(_,k,v) settings[k]=v end,flush=function() end} end} end
package.preload['ui/uimanager']=function() return {show=function(_,v) messages[#messages+1]=v end,close=function() end,nextTick=function(_,fn) fn() end,scheduleIn=function(_,_,fn) fn() end,setDirty=function() end} end
package.preload['ui/widget/infomessage']=function() return {new=function(_,v) return v end} end
package.preload['ui/widget/inputdialog']=function() return {new=function(_,v) v.getInputText=function() return typed or 'full book question' end;v.onShowKeyboard=function() end;dialogs[#dialogs+1]=v;return v end} end
package.preload['ui/widget/menu']=function() return {new=function(_,v) dialogs[#dialogs+1]=v;return v end} end
package.preload['ui/network/manager']=function() return {runWhenOnline=function(_,fn) fn() end} end
package.preload['ui/trapper']=function() return {wrap=function(_,fn) fn() end,dismissableRunInSubprocess=function(_,fn) if cancelled then return false end;local r,e,s=fn();if late_close then late_close=false;browser:close() end;return true,r,e,s end} end
package.preload['webbrowser_remote_view']=function() return {new=function(_,v) v.frame_width=600;v.frame_height=800;v.onClose=function() v.on_close() end;return v end} end
package.preload['webbrowser_remote_transport']=function() return {
 exchange=function(origin,token,payload,id)
  requests[#requests+1]=payload
  if failure then return nil,'PC unavailable' end
  if conflict then conflict=false;return nil,'stale',409 end
  if payload.action=='close' then return {closed=true} end
  return {session=string.rep('a',32),generation=#requests,url='https://example.org/',title='Page',can_back=true,can_forward=false,zoom=2,width=600,height=800,path='/owned/frame-'..id..'.png',focus=focus or {kind='none'}}
 end,remove=function(path) removed[#removed+1]=path end,clear=function() end,newId=function() return 'abcdef01' end,
} end
local native_dofile=dofile
dofile=function(path)
 if path=='/unused/plugins/kobosync.koplugin/config.lua' then return {server='http://192.168.1.5:8787',token='paired-secret'} end
 if path=='/unused/plugins/kobosync.koplugin/discovery.lua' then return {resolve=function(server,token) assert(token=='paired-secret');return server end} end
 return native_dofile(path)
end
owner={ui={document={file='Original book.epub'}},getBookmarks=function() return {{title='Saved',source_url='https://saved.example/'}} end,
 addBookmarkEntry=function(_,url,title) saved_bookmark=url;return true end,openDirectUrl=function(_,url) download=url end}
''')

    def load(self):
        self.assertTrue((PLUGIN/'webbrowser_remote.lua').is_file(), 'Remote browser controller is missing')
        self.lua.execute("Remote=require('webbrowser_remote');browser=Remote.new(owner);browser:open()")

    def test_actions_carry_displayed_generation_and_normalized_coordinates(self):
        self.load()
        self.lua.execute(r'''
assert(browser.page and requests[1].action=='open' and requests[1].width==600 and requests[1].height==800)
browser:action('click',{x=.25,y=.75})
assert(requests[2].session==string.rep('a',32) and requests[2].generation==1 and requests[2].x==.25 and requests[2].y==.75)
browser:action('scroll',{delta=1});assert(requests[3].generation==2 and requests[3].delta==1)
browser:action('back');assert(requests[4].action=='back')
assert(owner.ui.document.file=='Original book.epub')
''')

    def test_cancel_failure_and_close_never_publish_late_result(self):
        self.load()
        self.lua.execute(r'''
local path=browser.page.path;cancelled=true;browser:action('reload');assert(browser.page.path==path and not browser.busy)
cancelled=false;failure=true;browser:action('refresh');assert(browser.page.path==path and browser.visible and not browser.busy)
failure=false;late_close=true;browser:action('forward');assert(not browser.visible and browser.view==nil and not browser.busy)
''')

    def test_conflict_refreshes_without_replaying_click(self):
        self.load()
        self.lua.execute(r'''
conflict=true;browser:action('click',{x=.5,y=.5})
assert(#requests==3 and requests[2].action=='click' and requests[3].action=='open')
assert(requests[3].session==string.rep('a',32) and browser.page.generation==3)
''')

    def test_keyboard_masks_password_and_dropdown_sends_selected_value(self):
        self.load()
        self.lua.execute(r'''
focus={kind='password',value='must-not-display'};browser:action('click',{x=.2,y=.2})
local d=dialogs[#dialogs];assert(d.text_type=='password' and d.input=='')
typed='private';d.buttons[1][3].callback();assert(requests[#requests].action=='text' and requests[#requests].value=='private' and requests[#requests].submit==true)
focus={kind='select',value='a',options={{label='First',value='a'},{label='Second',value='b'}}};browser:action('click',{x=.2,y=.2})
d=dialogs[#dialogs];d.item_table[2].callback();assert(requests[#requests].action=='select' and requests[#requests].value=='b')
''')

    def test_country_dropdown_native_choices_can_select_the_249th_option(self):
        self.load()
        self.lua.execute(r'''
local options={}
for i=1,249 do options[i]={label=string.format('Country %03d',i),value=string.format('C%03d',i)} end
focus={kind='select',value='C001',options=options};browser:action('click',{x=.2,y=.2})
local menu=dialogs[#dialogs];assert(#menu.item_table==249)
menu.item_table[249].callback()
assert(requests[#requests].action=='select' and requests[#requests].value=='C249' and requests[#requests].generation==2)
''')

    def test_address_preserves_query_and_direct_books_keep_downloader(self):
        self.load()
        self.lua.execute(r'''
browser:navigate('Why does my full book question matter?')
assert(requests[#requests].input=='Why does my full book question matter?')
browser:bookmark();assert(saved_bookmark=='https://example.org/')
browser:navigate('https://example.org/book.epub?download=1')
assert(download=='https://example.org/book.epub?download=1' and not browser.visible)
''')

    def test_only_two_frames_retained_and_close_releases_files(self):
        self.load()
        self.lua.execute(r'''
browser:action('refresh');browser:action('refresh');browser:action('refresh')
assert(#browser.frames==2 and #removed==2)
browser:close();assert(#browser.frames==0 and #removed==4 and owner.ui.document.file=='Original book.epub')
''')

    def test_search_buttons_send_full_input_and_persist_remote_engine(self):
        self.load()
        self.lua.execute(r'''
typed='Why is the full sentence important for this book?';browser:editAddress()
dialogs[#dialogs].buttons[2][2].callback()
assert(requests[#requests].input==typed and requests[#requests].engine=='google' and settings.engine=='google')
browser:editAddress();dialogs[#dialogs].buttons[1][3].callback()
assert(requests[#requests].engine=='google')
''')

    def test_address_works_after_initial_pc_failure(self):
        self.lua.execute('failure=true')
        self.load()
        self.lua.execute(r'''
assert(not browser.page);failure=false
browser:navigate('How does this complete question work?')
assert(requests[#requests].action=='open' and requests[#requests].input=='How does this complete question work?')
''')

    def test_initial_search_is_part_of_the_first_busy_open_request(self):
        self.lua.execute(r'''
settings.engine='google';Remote=require('webbrowser_remote');browser=Remote.new(owner)
local work
package.loaded['ui/trapper'].wrap=function(_,fn) work=fn end
browser:open('Why does this complete book question include every word?')
assert(browser.busy and #requests==0 and work,'Initial RPC was not queued')
work()
assert(#requests==1 and requests[1].action=='open')
assert(requests[1].input=='Why does this complete book question include every word?' and requests[1].engine=='google')
assert(browser.page and not browser.busy)
''')

    def test_visible_browser_accepts_search_entry_when_idle(self):
        self.load()
        self.lua.execute(r'''
browser:open('Why is this entire selection a useful question?')
assert(#requests==2 and requests[2].action=='navigate')
assert(requests[2].input=='Why is this entire selection a useful question?' and requests[2].engine=='duckduckgo')
browser:open();assert(#requests==2,'Plain open navigated an existing page')
browser.busy=true;browser:open('Another selection');assert(#requests==2,'Busy browser started a parallel search')
''')

    def test_horizontal_frame_swipes_forward_panning_without_vertical_scroll(self):
        self.load()
        self.lua.execute(r'''
local class={extend=function(_,t) return t end}
for _,name in ipairs({'ui/widget/container/inputcontainer','ui/widget/container/centercontainer','ui/widget/container/framecontainer',
 'ui/widget/imagewidget','ui/widget/textboxwidget','ui/widget/buttontable','ui/widget/verticalgroup','ui/widget/titlebar',
 'ui/geometry','ui/gesturerange','ui/font','ffi/blitbuffer'}) do package.preload[name]=function() return class end end
package.preload.device=function() return {screen={}} end
local ActualView=dofile(plugin_dir..'/webbrowser_remote_view.lua')
local view=setmetatable({page=browser.page,on_action=function(a,p) browser:action(a,p) end},{__index=ActualView})
assert(view:onFrameSwipe(nil,{direction='west'}))
assert(requests[2].action=='scroll' and requests[2].delta==0 and requests[2].delta_x==1 and requests[2].generation==1)
assert(view:onFrameSwipe(nil,{direction='east'}))
assert(requests[3].action=='scroll' and requests[3].delta==0 and requests[3].delta_x==-1 and requests[3].generation==2)
assert(view:onFrameSwipe(nil,{direction='north'}))
assert(requests[4].delta==1 and requests[4].delta_x==nil)
assert(view:onFrameSwipe(nil,{direction='south'}))
assert(requests[5].delta==-1 and requests[5].delta_x==nil)
''')

    def test_discovery_exception_is_private_and_releases_busy_state(self):
        self.load()
        self.lua.execute(r'''
browser.verified=false;browser.discovery.resolve=function() error('paired-secret entered-value') end
browser:action('refresh');assert(not browser.busy)
local message=messages[#messages].text
assert(message and not message:find('paired-secret',1,true) and not message:find('entered-value',1,true))
''')

    def test_reopening_reverifies_cached_pc_identity_before_authorization(self):
        self.load()
        self.lua.execute(r'''
browser:close();local verified=0
browser.discovery.resolve=function(server,token) verified=verified+1;return server end
browser:open();assert(verified==1,'Reopening reused an old PC identity proof')
''')

    def test_home_address_box_is_ready_for_a_website_without_internal_uri(self):
        self.load()
        self.lua.execute(r'''
browser.page.url='about:blank'
browser:editAddress()
assert(dialogs[#dialogs].input=='','Home prefilled an internal URI that rejects an ordinary website address')
''')


@unittest.skipUnless(LuaRuntime, 'Install dev extra for remote browser tests')
class RemoteViewTests(unittest.TestCase):
    def test_taps_map_from_the_actual_frame_rectangle_and_swipes_scroll(self):
        self.assertTrue((PLUGIN/'webbrowser_remote_view.lua').is_file(), 'Remote image view is missing')
        lua=LuaRuntime(unpack_returned_tuples=True)
        lua.globals().plugin_dir=str(PLUGIN)
        lua.execute(r'''
package.path=plugin_dir..'/?.lua;'..package.path
local class={extend=function(_,t) return t end}
for _,name in ipairs({'ui/widget/container/inputcontainer','ui/widget/container/centercontainer','ui/widget/container/framecontainer',
 'ui/widget/imagewidget','ui/widget/textboxwidget','ui/widget/buttontable','ui/widget/verticalgroup','ui/widget/titlebar',
 'ui/geometry','ui/gesturerange','ui/font','ui/uimanager','ui/size','ffi/blitbuffer'}) do package.preload[name]=function() return class end end
package.preload.device=function() return {screen={}} end
View=require('webbrowser_remote_view')
local x,y=View.normalize({x=20,y=140,w=600,h=800},170,740)
assert(x==.25 and y==.75)
assert(View.normalize({x=20,y=140,w=600,h=800},170,139)==nil)
assert(View.normalize({x=20,y=140,w=600,h=800},620,140)==nil)
local action,args
local view=setmetatable({page={},frame_area={dimen={x=20,y=140,w=600,h=800}},on_action=function(a,p) action,args=a,p end},{__index=View})
assert(view:onFrameTap(nil,{pos={x=170,y=740}}));assert(action=='click' and args.x==.25 and args.y==.75)
assert(view:onFrameSwipe(nil,{direction='north'}));assert(action=='scroll' and args.delta==1)
assert(view:onFrameSwipe(nil,{direction='south'}));assert(args.delta==-1)
''')


if __name__=='__main__':
    unittest.main()
