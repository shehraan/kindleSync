"""Manual Chromium-to-HTTP-to-native-KOReader acceptance runner (no physical device)."""
import json,os,subprocess,sys,threading,time
from pathlib import Path
from http.server import ThreadingHTTPServer
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests'))
sys.path.insert(0,str(root))
from test_browser import FixtureHandler
from kobosync.store import Store
from kobosync.server import make_server
work=root/'.tools/browser-pc-runtime'
home=work/'native-home'; screenshots=work/'screenshots'
for folder in [home/'settings',home/'plugins/kobosync.koplugin',screenshots]: folder.mkdir(parents=True,exist_ok=True)
store=Store(work/'server-data')
engines=store.root/'browser/engines';engines.parent.mkdir(parents=True,exist_ok=True)
if not engines.exists():engines.symlink_to(root/'data/browser/engines',target_is_directory=True)
site=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
site.image_requests=0
api=make_server(store,'127.0.0.1',0)
for server in [site,api]:threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{site.server_port}'
(home/'plugins/kobosync.koplugin/config.lua').write_text('return {server="http://127.0.0.1:'+str(api.server_port)+'",token="'+store.token+'"}')
(home/'plugins/kobosync.koplugin/discovery.lua').write_bytes((root/'koreader/kobosync.koplugin/discovery.lua').read_bytes())
script=r'''
require('setupkoenv')
package.path=PROJECT..'/koreader/webbrowser.koplugin/?.lua;'..package.path
G_defaults=require('luadefaults'):open()
G_reader_settings=require('luasettings'):open(os.getenv('KO_HOME')..'/settings.reader.lua')
G_reader_settings:saveSetting('virtual_keyboard_enabled',true)
local Device=require('device')
require('document/canvascontext'):init(Device)
local UIManager=require('ui/uimanager')
local NetworkMgr=require('ui/network/manager')
NetworkMgr.runWhenOnline=function(_,fn) fn() end
local json=require('json')
local socket=require('socket')
local Remote=require('webbrowser_remote')
local owner={ui={document={file='Original book.epub'}},getBookmarks=function() return {} end,
 addBookmarkEntry=function() return true,'Bookmark added.' end,openDirectUrl=function() error('Unexpected file download') end}
local browser=Remote.new(owner)
local reports={}
local function save(name)
 UIManager:forceRePaint()
 Device.screen:shot(OUTPUT..name..'.png')
 local page=browser.page
 local rect=browser.view and browser.view.frame_area.dimen or {}
 reports[#reports+1]={scenario=name,title=page and page.title,url=page and page.url,
  frame_bytes=page and page.frame_bytes,frame={x=rect.x,y=rect.y,w=rect.w,h=rect.h}}
 print('Native scenario: '..name)
end
local function finish(ok,message)
 local f=assert(io.open(OUTPUT..'results.json','w'));f:write(json.encode({passed=ok,error=message,scenarios=reports,width=Device.screen:getWidth(),height=Device.screen:getHeight()}));f:close()
 if browser.visible then browser:close() end
 UIManager:scheduleIn(.8,function() UIManager:quit() end)
end
local function tap(x,y)
 local rect=browser.view.frame_area.dimen
 browser.view:onFrameTap(nil,{pos={x=rect.x+x*browser.page.zoom,y=rect.y+y*browser.page.zoom}})
end
local step=0
local stages={
 function() assert(browser.page and browser.page.session,'No initial frame');save('home');browser:navigate(BASE..'/') end,
 function() assert(browser.page.title=='JavaScript active','JS fixture did not load');save('image-and-js');tap(60,155) end,
 function() assert(browser.dialog and browser.page.focus.kind=='text','Text field keyboard missing');save('text-keyboard');browser.dialog:setInputText('Clara');browser.dialog.buttons[1][3].callback() end,
 function() assert(browser.page.title=='Submitted Clara','Form did not submit');save('submitted-form');browser:action('back') end,
 function() assert(browser.page.url==BASE..'/','Back did not restore article');tap(60,205) end,
 function() assert(browser.dialog and browser.page.focus.kind=='password' and (browser.page.focus.value==nil or browser.page.focus.value==''),'Password focus unsafe');assert(browser.dialog:getInputText()=='','Password value leaked');browser.dialog:setInputText('sample-secret');save('password-keyboard');browser.dialog.buttons[1][2].callback() end,
 function() assert(browser.page.focus.kind=='password' and (browser.page.focus.value==nil or browser.page.focus.value==''));save('password-applied');tap(60,255) end,
 function() assert(browser.dialog and browser.page.focus.kind=='select','Dropdown missing');save('dropdown');browser.dialog.item_table[2].callback() end,
 function() assert(browser.page.title=='Selected two','Dropdown did not select');save('selected-option');tap(60,305) end,
 function() assert(browser.page.title=='Second page','Link tap missed');save('linked-page');browser:action('back') end,
 function() assert(browser.page.url==BASE..'/','History back failed');save('back');browser:action('forward') end,
 function() assert(browser.page.title=='Second page','History forward failed');save('forward');browser:editAddress();browser.dialog:setInputText('what do you think of The Pathless Path book compared with Deep Work?');save('complete-question-keyboard');assert(browser.dialog:getInputText():find('compared with Deep Work',1,true));browser.dialog.buttons[1][2].callback();assert(browser.dialog:getInputText()=='');browser.dialog.buttons[1][1].callback();browser:action('back') end,
 function() browser:action('scroll',{delta=1}) end,
 function() browser:action('scroll',{delta=1}) end,
 function() save('scrolled-page');assert(owner.ui.document.file=='Original book.epub');finish(true) end,
}
local began=socket.gettime()
local function nextStep()
 if socket.gettime()-began>100 then return finish(false,'Timed out waiting for browser action') end
 if browser.busy then return UIManager:scheduleIn(.1,nextStep) end
 step=step+1
 local fn=stages[step]
 if not fn then return end
 local ok,err=pcall(fn)
 if not ok then return finish(false,tostring(err)) end
 if step<#stages then UIManager:scheduleIn(.25,nextStep) end
end
browser:open();UIManager:scheduleIn(.25,nextStep)
UIManager:scheduleIn(105,function() finish(false,'Native UI timeout') end)
UIManager:run()
'''
script='PROJECT='+json.dumps(str(root))+'\nOUTPUT='+json.dumps(str(screenshots)+'/')+'\nBASE='+json.dumps(base)+'\n'+script
path=work/'native-check.lua';path.write_text(script)
(screenshots/'results.json').unlink(missing_ok=True)
env={**os.environ,'KO_HOME':str(home),'SDL_VIDEODRIVER':'dummy','EMULATE_READER_W':'1264','EMULATE_READER_H':'1680','EMULATE_READER_DPI':'300'}
try:
 started=time.monotonic()
 result=subprocess.run([str(root/'.tools/browser-ui-linux/runtime/lib/koreader/luajit'),str(path)],cwd=root/'.tools/browser-ui-linux/runtime/lib/koreader',env=env,capture_output=True,text=True,timeout=120)
 (work/'native-stdout.log').write_text(result.stdout);(work/'native-stderr.log').write_text(result.stderr)
 print(result.stdout[-3000:]);print(result.stderr[-2000:])
 proof=json.loads((screenshots/'results.json').read_text()) if (screenshots/'results.json').exists() else {'passed':False,'error':'No results'}
 print('Native runtime:',json.dumps(proof),flush=True)
 assert result.returncode==0 and proof['passed'],proof
 from PIL import Image
 image=Image.open(screenshots/'image-and-js.png').convert('L');rect=proof['scenarios'][1]['frame']
 pixel=image.getpixel((int(rect['x']+80),int(rect['y']+80)))
 assert pixel<30,('Remote website image missing from native rendered screen',pixel)
 print('Verified Chromium SVG image rendered through HTTP onto native KOReader screen; center pixel',pixel)
 print('Total scenario seconds:',round(time.monotonic()-started,2))
finally:
 api.shutdown();api.server_close();site.shutdown();site.server_close()
