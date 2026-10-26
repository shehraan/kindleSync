import unittest
from pathlib import Path
try:
    from lupa.luajit21 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(LuaRuntime, 'Install dev extra for browser navigation tests')
class BrowserNavigationTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().plugin_dir = str(ROOT/'koreader/webbrowser.koplugin')
        self.lua.globals().socket_dir = str(ROOT/'.tools/lua-test-runtime/usr/share/lua/5.1')
        self.lua.execute(r'''
package.path=plugin_dir..'/?.lua;'..socket_dir..'/?.lua;'..package.path
package.preload['socket']=function() return {} end
package.preload['util']=function() return {} end
navigation_available=package.searchpath and package.searchpath('webbrowser_navigation',package.path)
''')

    def module(self):
        module = ROOT/'koreader/webbrowser.koplugin/webbrowser_navigation.lua'
        self.assertTrue(module.is_file(), 'Normal browser navigation is not implemented')
        self.lua.execute("Navigation=require('webbrowser_navigation');session=Navigation.new()")

    def test_single_box_accepts_addresses_and_search_terms(self):
        self.module()
        self.lua.execute(r'''
local kind,target=Navigation.input('  example.org/articles  ')
assert(kind=='url' and target=='https://example.org/articles')
kind,target=Navigation.input('a good free ebook')
assert(kind=='search' and target=='a good free ebook')
kind,target=Navigation.input('https://example.org/a?q=one')
assert(kind=='url' and target=='https://example.org/a?q=one')
kind,target=Navigation.input('version 1.2')
assert(kind=='search')
assert(Navigation.input('javascript:alert(1)')==nil)
kind,target=Navigation.input('example.org:8080/articles')
assert(kind=='url' and target=='https://example.org:8080/articles')
kind,target=Navigation.input('site:example.org free books')
assert(kind=='search' and target=='site:example.org free books')
kind,target=Navigation.input('HTTPS://example.org')
assert(kind=='url' and target=='https://example.org')
''')

    def test_back_forward_preserve_cached_content_and_scroll(self):
        self.module()
        self.lua.execute(r'''
session:push({kind='home',html='Home'})
session:push({kind='search',input='books',html='Results',ratio=.4})
session:push({kind='url',input='https://example.org',html='Article'})
assert(session:back().html=='Results' and session:current().ratio==.4)
assert(session:forward().html=='Article')
assert(session:back().html=='Results')
session:push({kind='url',input='https://other.example',html='New branch'})
assert(not session:canForward() and #session.entries==3)
''')

    def test_reload_replaces_page_without_losing_forward_history(self):
        self.module()
        self.lua.execute(r'''
session:push({html='A'});session:push({html='B'});session:back()
session:replace({html='A updated'})
assert(#session.entries==2 and session:current().html=='A updated')
assert(session:forward().html=='B')
''')

    def test_html_removes_scripts_styles_and_resolves_relative_links(self):
        self.module()
        self.lua.execute(r'''
local html=Navigation.cleanHtml('<html><head><style>BAD_CSS</style></head><body><script>BAD_SCRIPT</script><h1>Hello</h1><a href="../next">Next</a><img src="remote.png"/><a href="javascript:alert(1)">Bad</a></body></html>','https://example.org/books/start')
assert(html:find('Hello',1,true) and html:find('https://example.org/next',1,true))
assert(not html:find('BAD_SCRIPT',1,true) and not html:find('BAD_CSS',1,true))
assert(not html:find('remote.png',1,true) and not html:find('javascript:',1,true))
assert(Navigation.resolve('https://example.org/a','/b')=='https://example.org/b')
''')

    def test_results_are_clickable_and_escape_query_text(self):
        self.module()
        self.lua.execute(r'''
local html=Navigation.resultsHtml('<book>',{{title='A & B',url='https://example.org/?a=1&b=2',snippet='Useful <script>text</script>'}})
assert(html:find('&lt;book&gt;',1,true))
assert(html:find('A &amp; B',1,true) and html:find('href=',1,true))
assert(not html:find('<script>',1,true))
''')

    def controller(self):
        self.module()
        self.assertTrue((ROOT/'koreader/webbrowser.koplugin/webbrowser_simple.lua').is_file(), 'Unified browser window is missing')
        self.lua.execute(r'''
searches=0;loads=0;messages={};cancelled=false;load_error=false;opened_download=nil
package.preload['ui/uimanager']=function() return {show=function(_,v) messages[#messages+1]=v end,close=function() end,setDirty=function() end,nextTick=function(_,fn) fn() end} end
package.preload['ui/widget/infomessage']=function() return {new=function(_,t) return t end} end
package.preload['ui/widget/inputdialog']=function() return {} end
package.preload['ui/network/manager']=function() return {runWhenOnline=function(_,fn) fn() end} end
package.preload['ui/trapper']=function() return {wrap=function(_,fn) fn() end,dismissableRunInSubprocess=function(_,task) if cancelled then return false end;return true,task() end} end
package.preload['webbrowser_markdown_viewer']=function() return {new=function(_,opts)
 opts.scroll_widget={getCurrentRatio=function() return .3 end,scrollToRatio=function() end}
 opts.onClose=function() opts.on_close() end
 return opts
end} end
package.preload['webbrowser_page']=function() return {load=function(target)
 loads=loads+1;if load_error then return nil,'Network unavailable' end
 return {kind='url',input=target,url=target,title='A website',html='<p>Page</p>'}
end} end
owner={ui={document={file='Original book.epub'}},getSearchEngineModule=function() return {search=function() searches=searches+1;return {{title='Result',url='https://example.org',snippet='A result'}} end},{} end,
 getBookmarks=function() return {} end,openDirectUrl=function(_,url) opened_download=url end}
Browser=require('webbrowser_simple');browser=Browser.new(owner);browser:open()
''')

    def test_one_window_search_links_and_cached_back_forward(self):
        self.controller()
        self.lua.execute(r'''
browser:submit('a free book');assert(searches==1 and browser.session:current().kind=='search')
browser:submit('example.org');assert(loads==1 and browser.session:current().url=='https://example.org')
browser:follow('/chapter');assert(loads==2 and browser.session:current().url=='https://example.org/chapter')
browser:back();browser:back();assert(browser.session:current().kind=='search')
browser:forward();assert(browser.session:current().url=='https://example.org')
assert(loads==2 and searches==1 and owner.ui.document.file=='Original book.epub')
''')

    def test_reload_and_error_preserve_history_and_current_page(self):
        self.controller()
        self.lua.execute(r'''
browser:submit('example.org');browser:submit('other.example');browser:back()
local count=#browser.session.entries;browser:reload()
assert(#browser.session.entries==count and browser.session:canForward())
load_error=true;browser:submit('bad.example')
assert(browser.session:current().url=='https://example.org')
assert(#browser.session.entries==count and browser.visible and not browser.busy)
''')

    def test_cancelled_load_keeps_current_view(self):
        self.controller()
        self.lua.execute(r'''
browser:submit('example.org');cancelled=true;browser:submit('other.example')
assert(browser.session:current().url=='https://example.org' and not browser.busy)
''')

    def test_epub_links_keep_existing_book_download_workflow(self):
        self.controller()
        self.lua.execute(r'''
browser:submit('https://example.org/book.epub')
assert(opened_download=='https://example.org/book.epub' and loads==0 and not browser.visible)
''')

    def page(self):
        self.module()
        self.assertTrue((ROOT/'koreader/webbrowser.koplugin/webbrowser_page.lua').is_file(), 'Browser page loader is missing')
        self.lua.execute(r'''
page_requests=0;page_responses={}
package.preload['socketutil']=function() return {set_timeout=function() end,reset_timeout=function() end} end
package.preload['webbrowser_http']=function() return {request=function(req)
 page_requests=page_requests+1;local r=page_responses[page_requests]
 local ok,err=req.sink(r.body or '');if not ok then return nil,err end
 return 1,r.code or 200,r.headers or {['content-type']='text/html'}
end} end
Page=require('webbrowser_page')
''')

    def test_page_loader_follows_redirect_and_returns_readable_linked_body(self):
        self.page()
        self.lua.execute(r'''
page_responses={{code=302,headers={location='/article'}},{body='<html><title>An article</title><body><script>bad()</script><a href="/next">Next</a></body></html>'}}
local page,err=Page.load('https://example.org/start')
assert(page and not err and page.url=='https://example.org/article')
assert(page.html:find('https://example.org/next',1,true) and not page.html:find('bad()',1,true))
''')

    def test_page_loader_rejects_oversized_page_instead_of_rendering_partial_data(self):
        self.page()
        self.lua.execute(r'''
page_responses={{body=string.rep('x',2*1024*1024+1)}}
local page,err=Page.load('https://example.org/huge')
assert(not page and err:lower():find('large',1,true))
''')

    def test_opaque_book_download_is_handed_to_existing_downloader(self):
        self.page()
        self.lua.execute(r'''
page_responses={{body='PK\003\004'..string.rep('x',2*1024*1024+1)}}
local page,err=Page.load('https://example.org/download?id=book')
assert(page and page.kind=='download' and page.url=='https://example.org/download?id=book')
''')

    def test_new_search_default_is_persisted_across_subsequent_restarts(self):
        import re
        source=ROOT/'koreader/webbrowser.koplugin/main.lua'
        self.lua.globals().imports=self.lua.table_from(sorted(set(re.findall(r'require\s*\(?\s*["\']([^"\']+)',source.read_text()))))
        self.lua.execute(r'''
local class={extend=function(_,t) return t end}
for _,name in ipairs(imports) do package.preload[name]=function() return class end end
package.preload['device']=function() return {screen={}} end
package.preload['gettext']=function() return function(s) return s end end
package.preload['datastorage']=function() return {getSettingsDir=function() return '/tmp' end} end
package.preload['libs/libkoreader-lfs']=function() return {attributes=function() return {modification=2} end} end
saved={engine='duckduckgo',config_last_mtime=1}
package.preload['luasettings']=function() return {open=function() return {readSetting=function(_,k) return saved[k] end,saveSetting=function(_,k,v) saved[k]=v end,flush=function() end} end} end
package.preload['webbrowser_configuration']=function() return {engine='bing_rss',engines={bing_rss={name='bing_rss'}}} end
local browser=dofile(plugin_dir..'/main.lua')
browser:loadSettings()
assert(saved.engine=='bing_rss','Old search preference would return after another restart')
browser:loadSettings()
assert(browser:getSelectedEngineName()=='bing_rss')
''')

    def test_browser_view_has_address_bar_and_visible_history_controls(self):
        self.lua.execute(r'''
local class={}
function class:extend(t) return setmetatable(t,{__index=self}) end
function class:new(t) t=setmetatable(t or {},{__index=self});if t.init then t:init() end;return t end
function class:getSize() return {w=self.width or 1080,h=50} end
function class:getHeight() return 50 end
function class:getButtonById(id)
 for _,row in ipairs(self.buttons or {}) do for _,button in ipairs(row) do if button.id==id then
  button.enable=function(t) t.enabled=true end;button.disable=function(t) t.enabled=false end;button.refresh=function() end;return button
 end end end
end
for _,name in ipairs({'ui/widget/buttontable','ui/widget/container/centercontainer','ui/geometry','ui/widget/infomessage','ui/widget/container/inputcontainer','ui/widget/container/framecontainer','ui/widget/scrollhtmlwidget','ui/widget/verticalgroup','ui/widget/titlebar'}) do package.preload[name]=function() return class end end
package.preload['device']=function() return {screen={getWidth=function() return 1080 end,getHeight=function() return 1440 end},hasKeys=function() return false end} end
package.preload['ffi/blitbuffer']=function() return {COLOR_WHITE=1} end
package.preload['ui/size']=function() return {padding={large=10}} end
package.preload['ui/uimanager']=function() return {setDirty=function() end} end
package.preload['gettext']=function() return function(s) return s end end
package.preload['apps/filemanager/lib/md']=function() return {renderString=function() error('Raw browser HTML was sent through Markdown') end} end
Viewer=require('webbrowser_markdown_viewer')
view=Viewer:new{browser_mode=true,html='<h1>Web page</h1>',address='example.org',can_back=false,can_forward=true,on_address=function() address_taps=(address_taps or 0)+1 end}
assert(view.navigation_table,'Browser navigation controls are missing')
assert(view.navigation_table:getButtonById('browser_back').enabled==false)
assert(view.navigation_table:getButtonById('browser_forward').enabled==true)
assert(view.navigation_table:getButtonById('browser_home'))
assert(view.navigation_table:getButtonById('browser_reload'))
view.address_table:getButtonById('browser_address').callback()
assert(address_taps==1 and view.scroll_widget.html_body=='<h1>Web page</h1>')
''')
