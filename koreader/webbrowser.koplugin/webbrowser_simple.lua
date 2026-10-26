local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local NetworkMgr = require("ui/network/manager")
local Trapper = require("ui/trapper")
local Viewer = require("webbrowser_markdown_viewer")
local Navigation = require("webbrowser_navigation")
local Page = require("webbrowser_page")
local Browser = {}
Browser.__index = Browser

function Browser.new(owner)
    local self = setmetatable({owner=owner, session=Navigation.new(), ticket=0}, Browser)
    self.session:push({kind="home", title="Web Browser", input="", html="<h2>Search or open a website</h2><p>Tap the address bar above. Enter search words or a website address, then tap Go.</p><p>Back and Forward return to pages you already visited. Close returns to your book.</p>"})
    return self
end

function Browser:rememberScroll()
    if self.view and self.view.scroll_widget then
        self.session:current().ratio = self.view.scroll_widget:getCurrentRatio()
    end
end

function Browser:draw()
    if not self.visible then return end
    if self.view then UIManager:close(self.view) end
    local page = self.session:current()
    self.view = Viewer:new{
        browser_mode=true, title=page.title, html=page.html,
        address=page.input, can_back=self.session:canBack(), can_forward=self.session:canForward(),
        can_reload=page.kind == "search" or page.kind == "url", can_bookmark=page.kind == "url",
        on_back=function() if self.session:canBack() then self:back() else self:close() end end,
        on_forward=function() self:forward() end,
        on_home=function() self:home() end,
        on_reload=function() self:reload() end,
        on_address=function() self:editAddress() end,
        on_link=function(link) self:follow(link) end,
        on_bookmarks=function() self:bookmarks() end,
        on_bookmark=function() self:bookmark() end,
        on_close=function()
            self:rememberScroll()
            self.visible, self.view, self.busy = false, nil, false
            self.ticket = self.ticket + 1
        end,
    }
    local view = self.view
    UIManager:show(view)
    if page.ratio and page.ratio > 0 then
        UIManager:nextTick(function()
            if self.view == view then view.scroll_widget:scrollToRatio(page.ratio) end
        end)
    end
end

function Browser:open() self.visible=true; self:draw() end
function Browser:close() if self.view then self.view:onClose() end end
function Browser:back() self:rememberScroll(); self.session:back(); self:draw() end
function Browser:forward() self:rememberScroll(); self.session:forward(); self:draw() end
function Browser:home()
    self:rememberScroll()
    if self.session:current().kind ~= "home" then
        self.session:push({kind="home", title="Web Browser", input="", html="<h2>Search or open a website</h2><p>Tap the address bar above to get started.</p>"})
    end
    self:draw()
end

function Browser:message(text)
    UIManager:show(InfoMessage:new{text=text, timeout=5})
end

function Browser:editAddress()
    local dialog
    dialog = InputDialog:new{
        title="Search or enter address", input=self.session:current().input or "",
        input_hint="Search words or example.org",
        buttons={{
            {text="Cancel", callback=function() UIManager:close(dialog) end},
            {text="Clear", callback=function() dialog:setInputText("") end},
            {text="Go", is_enter_default=true, callback=function()
                local text = dialog:getInputText()
                UIManager:close(dialog)
                self:submit(text)
            end},
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function Browser:submit(text, replace)
    local kind, target = Navigation.input(text)
    if not kind then return self:message(target) end
    if self.busy then return end
    local path = target:match("^[^?#]+") or target
    if kind == "url" and (path:lower():match("%.epub$") or path:lower():match("%.pdf$")) then
        self:close()
        return self.owner:openDirectUrl(target)
    end
    self:rememberScroll()
    self.ticket = self.ticket + 1
    local ticket = self.ticket
    NetworkMgr:runWhenOnline(function()
        if not self.visible or ticket ~= self.ticket then return end
        self.busy = true
        Trapper:wrap(function()
            local completed, page, err = Trapper:dismissableRunInSubprocess(function()
                if kind == "url" then return Page.load(target) end
                local engine, config = self.owner:getSearchEngineModule()
                local results, search_error = engine.search(target, config)
                if not results then return nil, search_error end
                return {kind="search", input=target, title=target, html=Navigation.resultsHtml(target, results)}
            end, "Loading… Tap to cancel.")
            if not self.visible or ticket ~= self.ticket then return end
            self.busy = false
            if not completed then return end
            if not page then return self:message(err or "Could not load this page.") end
            if page.kind == "download" then
                self:close()
                return self.owner:openDirectUrl(page.url)
            end
            if replace then self.session:replace(page) else self.session:push(page) end
            self:draw()
        end)
    end)
end

function Browser:follow(link)
    if type(link) == "table" then link = link.uri or link.url or link.href end
    local target = Navigation.resolve(self.session:current().url, link)
    if target then self:submit(target) end
end

function Browser:reload()
    local page = self.session:current()
    if page.kind == "search" or page.kind == "url" then self:submit(page.input, true) end
end

function Browser:bookmarks()
    self:rememberScroll()
    local results = {}
    for _, entry in ipairs(self.owner:getBookmarks()) do
        results[#results+1] = {title=entry.title or entry.source_url, url=entry.source_url or entry.url, snippet=entry.source_url or entry.url}
    end
    self.session:push({kind="bookmarks", title="Bookmarks", input="", html=Navigation.resultsHtml("Bookmarks", results)})
    self:draw()
end

function Browser:bookmark()
    local page = self.session:current()
    if page.kind == "url" then
        local ok, message = self.owner:addBookmarkEntry(page.url, page.title)
        self:message(message or (ok and "Bookmark added." or "Could not add bookmark."))
    end
end

return Browser
