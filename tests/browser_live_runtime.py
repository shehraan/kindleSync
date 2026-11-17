"""Opt-in native KOReader reproduction using a private device snapshot and live PC.

No book observation is submitted. The runner opens/closes browser sessions and
copies pairing only into a temporary private KOReader home, then removes it.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.tools/browser-ui-linux/runtime/lib/koreader'


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--snapshot', type=Path, default=ROOT/'data/setup/browser-failure-device')
    parser.add_argument('--client', type=Path, default=ROOT/'koreader/webbrowser.koplugin')
    parser.add_argument('--cycles', type=int, default=3)
    parser.add_argument('--expect-empty-home-input', action='store_true')
    args = parser.parse_args()
    output = ROOT/'.tools/browser-pc-runtime/live-device'
    output.mkdir(parents=True, exist_ok=True)
    script = r'''
require('setupkoenv')
package.path=CLIENT..'/?.lua;'..PROJECT..'/koreader/webbrowser.koplugin/?.lua;'..package.path
G_defaults=require('luadefaults'):open()
G_reader_settings=require('luasettings'):open(os.getenv('KO_HOME')..'/settings.reader.lua')
G_reader_settings:saveSetting('virtual_keyboard_enabled',true)
local Device=require('device')
require('document/canvascontext'):init(Device)
local UIManager=require('ui/uimanager')
require('ui/network/manager').runWhenOnline=function(_,fn) fn() end
local Remote=require('webbrowser_remote')
local json=require('json')
local socket=require('socket')
local http=require('webbrowser_http')
local original_request=http.request
http.request=function(request,...)
 local started=socket.gettime()
 local ok,a,b,c,d=pcall(original_request,request,...)
 local known={timeout=true,closed=true,['connection refused']=true}
 local event={method=request.method,path=request.url:match('/v1/browser/[^/]+'),seconds=socket.gettime()-started,
  returned=ok and not not a,status=tonumber(b),network_error=not a and known[b] and b or nil}
 local f=assert(io.open(OUTPUT..'requests.jsonl','a'));f:write(json.encode(event)..'\n');f:close()
 if not ok then error(a) end
 return a,b,c,d
end
local owner={ui={document={file='Unchanged book.epub'}},getBookmarks=function() return {} end,
 addBookmarkEntry=function() return true end,openDirectUrl=function() error('Unexpected download') end}
local browser
local messages={}
local original_show=UIManager.show
UIManager.show=function(self,widget,...)
 if widget.text and widget.name~='trap_widget' then messages[#messages+1]=tostring(widget.text) end
 return original_show(self,widget,...)
end
local results={}
local ended=false
local cycle,stage=1,1
local began=socket.gettime()
local function finish(ok,err)
 if ended then return end
 ended=true
 local report={passed=ok,error=err,cycles=cycle,results=results,messages=messages,seconds=socket.gettime()-began}
 local f=assert(io.open(OUTPUT..'results.json','w'));f:write(json.encode(report));f:close()
 if browser and browser.visible then browser:close() end
 UIManager:scheduleIn(1,function() UIManager:quit() end)
end
local function record(name)
 local page=browser.page
 results[#results+1]={cycle=cycle,stage=name,title=page and page.title,notice=page and page.notice,
  has_frame=page and page.path~=nil,verified=browser.verified}
 UIManager:forceRePaint()
 Device.screen:shot(OUTPUT..tostring(cycle)..'-'..name..'.png')
end
local function start()
 browser=Remote.new(owner)
 browser:open()
 stage=1
end
local function step()
 if ended then return end
 if socket.gettime()-began>180 then return finish(false,'Live native check timed out') end
 if browser.busy then return UIManager:scheduleIn(.1,step) end
 local ok,err=pcall(function()
  if stage==1 then
   assert(browser.page and browser.page.session,'Initial connection failed: '..table.concat(messages,' / '))
   browser:action('home');stage=2
  elseif stage==2 then
   assert(browser.page and browser.page.url=='about:blank','Browser home failed')
   browser:editAddress()
   local input=browser.dialog:getInputText()
   results[#results+1]={cycle=cycle,stage='home-address-input',input=input}
   if EXPECT_EMPTY then assert(input=='','Home address contains internal URI: '..input) end
   browser.dialog:setInputText('https://google.com')
   browser.dialog.buttons[1][3].callback();stage=3
  elseif stage==3 then
   assert(browser.page and browser.page.url:match('^https://[w.]*google%.com'),
    'Google address did not load: '..table.concat(messages,' / '))
   assert(not browser.page.notice,'Google returned a load notice: '..tostring(browser.page.notice))
   record('google')
   assert(owner.ui.document.file=='Unchanged book.epub')
   if cycle>=CYCLES then return finish(true) end
   browser:close()
   cycle=cycle+1
   start() -- Deliberately reopen before the old close worker has finished.
  end
 end)
 if not ok then return finish(false,tostring(err)) end
 if not ended then UIManager:scheduleIn(.15,step) end
end
start();UIManager:scheduleIn(.15,step)
UIManager:scheduleIn(185,function() finish(false,'Native UI timeout') end)
UIManager:run()
'''
    with tempfile.TemporaryDirectory(prefix='kobosync-live-native-') as folder:
        home = Path(folder)
        (home/'plugins/kobosync.koplugin').mkdir(parents=True)
        (home/'settings').mkdir()
        for name in ('config.lua', 'discovery.lua'):
            target = home/'plugins/kobosync.koplugin'/name
            shutil.copyfile(args.snapshot/'plugins/kobosync.koplugin'/name, target)
            target.chmod(0o600)
        saved = args.snapshot/'settings/webbrowser_remote.lua'
        if saved.exists():
            shutil.copyfile(saved, home/'settings/webbrowser_remote.lua')
        header = ('PROJECT='+json.dumps(str(ROOT))+'\nCLIENT='+json.dumps(str(args.client.resolve()))
                  +'\nOUTPUT='+json.dumps(str(output)+'/')+'\nCYCLES='+str(args.cycles)
                  +'\nEXPECT_EMPTY='+str(args.expect_empty_home_input).lower()+'\n')
        path = home/'check.lua'
        path.write_text(header+script)
        (output/'results.json').unlink(missing_ok=True)
        (output/'requests.jsonl').unlink(missing_ok=True)
        env = {**os.environ, 'KO_HOME':str(home), 'SDL_VIDEODRIVER':'dummy',
               'EMULATE_READER_W':'1264', 'EMULATE_READER_H':'1680', 'EMULATE_READER_DPI':'300'}
        result = subprocess.run([str(RUNTIME/'luajit'), str(path)], cwd=RUNTIME, env=env,
                                capture_output=True, text=True, timeout=200)
        (output/'stdout.log').write_text(result.stdout)
        (output/'stderr.log').write_text(result.stderr)
        report = json.loads((output/'results.json').read_text()) if (output/'results.json').exists() else {'passed':False,'error':'No native result'}
        # Never print the pairing config, website fields or full native logs.
        print(json.dumps(report), flush=True)
        assert result.returncode==0 and report['passed'], 'Native live browser check failed; see private runtime results'


if __name__ == '__main__':
    main()
