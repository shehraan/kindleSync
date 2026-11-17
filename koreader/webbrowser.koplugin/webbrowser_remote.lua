local DataStorage = require('datastorage')
local LuaSettings = require('luasettings')
local UIManager = require('ui/uimanager')
local InfoMessage = require('ui/widget/infomessage')
local InputDialog = require('ui/widget/inputdialog')
local Menu = require('ui/widget/menu')
local NetworkMgr = require('ui/network/manager')
local Trapper = require('ui/trapper')
local View = require('webbrowser_remote_view')
local Transport = require('webbrowser_remote_transport')
local Navigation = require('webbrowser_navigation')

local Remote = {}
Remote.__index = Remote
local NO_PC = 'Could not find your paired PC. Check that it is awake, KoboSync is running, and both devices are on the same Wi-Fi.'
local ACTIONS={open=true,navigate=true,click=true,scroll=true,text=true,select=true,key=true,back=true,forward=true,home=true,reload=true,refresh=true,zoom=true}

function Remote.new(owner)
    local self=setmetatable({owner=owner,ticket=0,frames={},zoom=2},Remote)
    local sibling=DataStorage:getDataDir()..'/plugins/kobosync.koplugin'
    local ok,config=pcall(dofile,sibling..'/config.lua')
    self.config=ok and type(config)=='table' and config or {}
    local found,discovery=pcall(dofile,sibling..'/discovery.lua')
    self.discovery=found and type(discovery)=='table' and discovery or nil
    self.settings=LuaSettings:open(DataStorage:getSettingsDir()..'/webbrowser_remote.lua')
    self.engine=self.settings:readSetting('engine','duckduckgo')
    if self.engine~='google' then self.engine='duckduckgo' end
    self.zoom=tonumber(self.settings:readSetting('zoom',2)) or 2
    self.zoom=math.max(1,math.min(3,self.zoom))
    self.endpoint=self.config.server
    if self.settings:readSetting('paired_origin')==self.config.server then
        self.endpoint=self.settings:readSetting('endpoint',self.endpoint)
    end
    self.id=Transport.newId()
    return self
end

function Remote:message(text)
    UIManager:show(InfoMessage:new{text=text,timeout=7})
end

function Remote:draw()
    if not self.visible then return false end
    local ok,view=pcall(View.new,View,{
        page=self.page,on_action=function(name,args) self:action(name,args) end,
        on_address=function() self:editAddress() end,on_text=function() self:editFocus() end,
        on_bookmarks=function() self:bookmarks() end,on_bookmark=function() self:bookmark() end,
        on_zoom=function() self:chooseZoom() end,on_close=function() self:close() end,
    })
    if not ok then self:message('Could not display this browser frame. Try Refresh.');return false end
    if self.view then UIManager:close(self.view) end
    self.view=view
    self.width,self.height=view.frame_width,view.frame_height
    UIManager:show(view)
    return true
end

function Remote:open(initial_input)
    if self.visible then
        if not self.busy then self:navigate(initial_input) end
        return
    end
    self.visible=true
    self.verified=false -- DHCP may have reassigned the cached address since close.
    Transport.clear()
    if not self:draw() then self.visible=false;return end
    if not self.id or not self.discovery or type(self.config.server)~='string' or type(self.config.token)~='string' or self.config.token=='' then
        self:message('Set up KoboSync pairing with your PC before opening the browser.')
        return
    end
    if type(initial_input)=='string' and initial_input~='' then
        -- With no displayed page, navigate includes the query and selected
        -- engine in the first open RPC, before its worker becomes busy.
        self:navigate(initial_input)
    else
        self:action('open')
    end
end

function Remote:bestEffortClose(payload)
    if not self.verified or not self.endpoint or not payload then return end
    local endpoint,token,id=self.endpoint,self.config.token,self.id..'-'..tostring(self.ticket)
    UIManager:nextTick(function()
        Trapper:wrap(function()
            Trapper:dismissableRunInSubprocess(function()
                return Transport.exchange(endpoint,token,payload,id)
            end,false)
        end)
    end)
end

function Remote:close()
    if not self.visible then return end
    self.visible=false
    self.ticket=self.ticket+1
    local page=self.page
    local payload=page and {action='close',session=page.session,generation=page.generation} or nil
    if self.dialog then UIManager:close(self.dialog);self.dialog=nil end
    if self.view then UIManager:close(self.view);self.view=nil end
    self.page=nil
    for _,path in ipairs(self.frames) do Transport.remove(path) end
    self.frames={}
    if self.busy then self.pending_close=payload
    else self:bestEffortClose(payload) end
    self.busy=false
end

function Remote:action(name, arguments)
    if not self.visible or self.busy or not ACTIONS[name] or not self.id or not self.discovery then return end
    local payload={action=name,width=self.width,height=self.height,zoom=self.zoom}
    if self.page then payload.session=self.page.session;payload.generation=self.page.generation end
    for key,value in pairs(arguments or {}) do
        if ({input=true,engine=true,x=true,y=true,delta=true,delta_x=true,value=true,submit=true,key=true,zoom=true,generation=true})[key] then payload[key]=value end
    end
    if not self.page then payload.action='open' end
    if name=='click' and (type(payload.x)~='number' or type(payload.y)~='number' or not (payload.x>=0 and payload.x<=1 and payload.y>=0 and payload.y<=1)) then return end
    self.ticket=self.ticket+1
    local ticket=self.ticket
    local id=self.id..'-'..tostring(ticket)
    NetworkMgr:runWhenOnline(function()
        if not self.visible or ticket~=self.ticket then return end
        self.busy=true
        self.active_ticket=ticket
        Trapper:wrap(function()
            local endpoint,verified=self.endpoint,self.verified
            local completed,result,err,status=Trapper:dismissableRunInSubprocess(function()
                local ran,page,message,code=pcall(function()
                    local server=endpoint
                    if not verified then server=self.discovery.resolve(endpoint or self.config.server,self.config.token) end
                    if not server then return nil,NO_PC end
                    local page,message,code=Transport.exchange(server,self.config.token,payload,id)
                    if not page and not code and verified then
                        local recovered=self.discovery.resolve(server,self.config.token)
                        if recovered and recovered~=server then
                            server=recovered
                            page,message,code=Transport.exchange(server,self.config.token,payload,id)
                        end
                    end
                    if page then page.endpoint=server end
                    return page,message,code
                end)
                if not ran then return nil,NO_PC end
                return page,message,code
            end,'Loading browser… Tap to cancel.')
            if self.active_ticket==ticket then self.busy=false;self.active_ticket=nil end
            if not completed and Transport.discard then Transport.discard(id) end
            if not self.visible or ticket~=self.ticket then
                if result and result.path then Transport.remove(result.path) end
                if result and result.endpoint then self.endpoint=result.endpoint;self.verified=true end
                if self.pending_close or (result and result.session) then
                    local close_payload=self.pending_close or {action='close',session=result.session,generation=result.generation}
                    if result and result.session then close_payload.session=result.session;close_payload.generation=result.generation end
                    self.pending_close=nil
                    self:bestEffortClose(close_payload)
                end
                return
            end
            if not completed then return end
            if not result then
                if status==409 then return self:action('open') end
                return self:message(err or NO_PC)
            end
            self.endpoint,self.verified=result.endpoint or endpoint,true
            result.endpoint=nil
            local previous=self.page
            self.page=result
            if not self:draw() then
                self.page=previous;Transport.remove(result.path);return
            end
            self.frames[#self.frames+1]=result.path
            while #self.frames>2 do Transport.remove(table.remove(self.frames,1)) end
            self.zoom=result.zoom
            self.settings:saveSetting('paired_origin',self.config.server)
            self.settings:saveSetting('endpoint',self.endpoint)
            self.settings:saveSetting('zoom',self.zoom)
            self.settings:flush()
            if result.download_url then
                local url=result.download_url
                self:close()
                return self.owner:openDirectUrl(url)
            end
            if result.notice then self:message(result.notice) end
            if name=='click' then self:editFocus() end
        end)
    end)
end

function Remote:navigate(input, engine)
    if type(input)~='string' or input=='' then return end
    if #input>4096 then return self:message('Use an address or search up to 4096 characters.') end
    local kind,target=Navigation.input(input)
    if not kind then return self:message(target) end
    if kind=='url' then
        local path=target:match('^[^?#]+') or target
        if path:lower():match('%.epub$') or path:lower():match('%.pdf$') then
            self:close();return self.owner:openDirectUrl(target)
        end
    end
    self.engine=engine or self.engine
    self.settings:saveSetting('engine',self.engine);self.settings:flush()
    self:action('navigate',{input=input,engine=self.engine})
end

function Remote:editAddress()
    if not self.visible or self.busy then return end
    local address=self.page and self.page.url or ''
    if not address:match('^https?://') then address='' end
    local dialog
    local function go(engine)
        local value=dialog:getInputText()
        UIManager:close(dialog);self.dialog=nil
        self:navigate(value,engine)
    end
    dialog=InputDialog:new{
        title='Search or enter address',input=address,input_hint='Search words or example.org',
        buttons={
            {{text='Cancel',callback=function() UIManager:close(dialog);self.dialog=nil end},
             {text='Clear',callback=function() dialog:setInputText('') end},
             {text='Go',is_enter_default=true,callback=function() go(self.engine) end}},
            {{text='DuckDuckGo',callback=function() go('duckduckgo') end},
             {text='Google',callback=function() go('google') end}},
        },
    }
    self.dialog=dialog;UIManager:show(dialog);dialog:onShowKeyboard()
end

function Remote:editFocus()
    if not self.visible or self.busy or not self.page then return end
    local focus=self.page.focus or {kind='none'}
    local generation=self.page.generation
    if focus.kind=='text' or focus.kind=='password' then
        local dialog
        local function send(submit)
            local value=dialog:getInputText()
            UIManager:close(dialog);self.dialog=nil
            self:action('text',{value=value,submit=submit,generation=generation})
        end
        dialog=InputDialog:new{
            title=focus.kind=='password' and 'Enter password' or 'Enter website text',
            input=focus.kind=='password' and '' or focus.value or '',text_type=focus.kind=='password' and 'password' or nil,
            buttons={{{text='Cancel',callback=function() UIManager:close(dialog);self.dialog=nil end},
                {text='Apply',callback=function() send(false) end},
                {text='Enter',is_enter_default=true,callback=function() send(true) end}}},
        }
        self.dialog=dialog;UIManager:show(dialog);dialog:onShowKeyboard()
    elseif focus.kind=='select' then
        local menu,items
        items={}
        for _,option in ipairs(focus.options or {}) do
            local value=option.value
            items[#items+1]={text=option.label,callback=function()
                UIManager:close(menu);self.dialog=nil
                self:action('select',{value=value,generation=generation})
            end}
        end
        menu=Menu:new{title='Choose website option',item_table=items}
        self.dialog=menu;UIManager:show(menu)
    end
end

function Remote:chooseZoom()
    local menu,items
    items={}
    for _,zoom in ipairs({1,1.5,2,2.5,3}) do
        local value=zoom
        items[#items+1]={text=tostring(value)..'×',callback=function()
            UIManager:close(menu);self.dialog=nil
            self:action('zoom',{zoom=value})
        end}
    end
    menu=Menu:new{title='Website text size',item_table=items}
    self.dialog=menu;UIManager:show(menu)
end

function Remote:bookmark()
    if self.page and self.page.url:match('^https?://') then
        local ok,message=self.owner:addBookmarkEntry(self.page.url,self.page.title)
        self:message(message or (ok and 'Bookmark added.' or 'Could not add bookmark.'))
    end
end

function Remote:bookmarks()
    local menu,items
    items={}
    for _,bookmark in ipairs(self.owner:getBookmarks()) do
        local url=bookmark.source_url or bookmark.url
        if type(url)=='string' then items[#items+1]={text=bookmark.title or url,callback=function()
            UIManager:close(menu);self.dialog=nil;self:navigate(url)
        end} end
    end
    menu=Menu:new{title='Browser bookmarks',item_table=items}
    self.dialog=menu;UIManager:show(menu)
end

return Remote
