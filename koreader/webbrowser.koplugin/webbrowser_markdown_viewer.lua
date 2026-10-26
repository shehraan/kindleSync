local Blitbuffer = require("ffi/blitbuffer")
local ButtonTable = require("ui/widget/buttontable")
local CenterContainer = require("ui/widget/container/centercontainer")
local Device = require("device")
local Geom = require("ui/geometry")
local InfoMessage = require("ui/widget/infomessage")
local InputContainer = require("ui/widget/container/inputcontainer")
local FrameContainer = require("ui/widget/container/framecontainer")
local ScrollHtmlWidget = require("ui/widget/scrollhtmlwidget")
local Size = require("ui/size")
local VerticalGroup = require("ui/widget/verticalgroup")
local TitleBar = require("ui/widget/titlebar")
local UIManager = require("ui/uimanager")
local _ = require("gettext")
local Screen = Device.screen
local Markdown = require("apps/filemanager/lib/md")

local DEFAULT_CSS = [[
@page {
    margin: 0;
}

html, body {
    background-color: #ffffff;
    color: #000000;
    margin: 0;
    padding: 0;
    font-family: 'Noto Sans', 'DejaVu Sans', sans-serif;
    line-height: 1.5;
    word-wrap: break-word;
}

a {
    color: #1a0dab;
    text-decoration: underline;
}

a:hover {
    text-decoration: underline;
}

pre, code {
    background-color: #f5f5f5;
    border-radius: 4px;
    padding: 0.3em 0.5em;
    font-family: 'Fira Code', 'DejaVu Sans Mono', monospace;
    font-size: 0.9em;
    overflow-x: auto;
}

pre {
    padding: 0.8em;
    margin: 1em 0;
}

h1, h2, h3, h4, h5, h6 {
    margin: 1.2em 0 0.6em 0;
    font-weight: 600;
}

ul, ol {
    padding-left: 1.6em;
    margin: 0.4em 0 0.6em 0;
}

blockquote {
    border-left: 4px solid #d0d0d0;
    margin: 1em 0;
    padding-left: 1em;
    color: #555555;
}

img {
    max-width: 100%;
}
]]

local MarkdownViewer = InputContainer:extend{
    title = "",
    markdown = "",
    css = nil,
    on_back = nil,
    on_link = nil,
    on_close = nil,
    on_save = nil,
    on_bookmark = nil,
    browser_mode = false,
    button_table = nil,
}

local function render_markdown(markdown_text)
    local ok, html = pcall(Markdown.renderString, markdown_text or "", { tag = "div" })
    if ok and html then
        return html
    end
    return string.format("<pre>%s</pre>", markdown_text or "")
end

function MarkdownViewer:init()
    local screen_w = Screen:getWidth()
    local screen_h = Screen:getHeight()

    self.align = "center"
    self.region = Geom:new{ x = 0, y = 0, w = screen_w, h = screen_h }

    if Device:hasKeys() then
        self.key_events = {
            Close = { { Device.input.group.Back } },
        }
        if self.browser_mode then
            self.key_events = { BrowserBack = { { Device.input.group.Back } } }
        end
    end

    local titlebar = TitleBar:new {
        width = screen_w,
        align = "left",
        with_bottom_line = true,
        title = self.title or "Web Browser",
        left_icon = self.browser_mode and "appbar.search" or "appbar.navigation",
        left_icon_tap_callback = function()
            if self.browser_mode then self.on_address() else self:handleBack() end
        end,
        close_callback = function()
            self:onClose()
        end,
        show_parent = self,
    }

    local html_body = self.html or render_markdown(self.markdown)
    local css = self.css or DEFAULT_CSS
    if self.browser_mode then
        css = css .. [[
body { padding: 0.3em 0.6em; font-family: sans-serif; line-height: 1.3; }
p { margin: 0.65em 0; }
h1 { font-size: 1.4em; margin: 0.4em 0; }
h2, h3 { font-size: 1.2em; margin: 0.4em 0; }
pre { white-space: pre-wrap; }
]]
    end

    local action_buttons = {
        {
            {
                text = "⇱",
                id = "prev_page",
                callback = function()
                    self:scrollPreviousPage()
                end,
            },
            {
                text = "⇲",
                id = "next_page",
                callback = function()
                    self:scrollNextPage()
                end,
            },
            {
                text = _("Back"),
                background = Blitbuffer.COLOR_WHITE,
                callback = function()
                    self:handleBack()
                end,
            },
            {
                text = _("Save"),
                background = Blitbuffer.COLOR_WHITE,
                enabled = self.on_save ~= nil,
                callback = function()
                    self:handleSave()
                end,
            },
            {
                text = _("Bookmark"),
                background = Blitbuffer.COLOR_WHITE,
                enabled = self.on_bookmark ~= nil,
                callback = function()
                    self:handleBookmark()
                end,
            },
            {
                text = _("Close"),
                background = Blitbuffer.COLOR_WHITE,
                callback = function()
                    self:onClose()
                end,
            },
        },
    }

    local navigation_height = 0
    if self.browser_mode then
        self.navigation_table = ButtonTable:new{
            width=screen_w - 2 * Size.padding.large, zero_sep=true, show_parent=self,
            buttons={{
                {id="browser_back", text=_("Back"), enabled=self.can_back == true, callback=function() self:handleBack() end},
                {id="browser_forward", text=_("Forward"), enabled=self.can_forward == true, callback=function() self.on_forward() end},
                {id="browser_home", text=_("Home"), callback=function() self.on_home() end},
                {id="browser_reload", text=_("Reload"), enabled=self.can_reload == true, callback=function() self.on_reload() end},
            }},
        }
        local address = self.address or ""
        if #address > 80 then address = address:sub(1,80):gsub("[\128-\255]+$", "") .. "..." end
        if address == "" then address = _("Search or enter address") end
        self.address_table = ButtonTable:new{
            width=screen_w - 2 * Size.padding.large, zero_sep=true, show_parent=self,
            buttons={{{id="browser_address", text=address, font_bold=false, font_size=18, callback=function() self.on_address() end}}},
        }
        navigation_height = self.navigation_table:getSize().h + self.address_table:getSize().h
        action_buttons = {{
            {id="prev_page", text=_("Page up"), callback=function() self:scrollPreviousPage() end},
            {id="next_page", text=_("Page down"), callback=function() self:scrollNextPage() end},
            {text=_("Bookmarks"), callback=function() self.on_bookmarks() end},
            {text=_("Bookmark"), enabled=self.can_bookmark == true, callback=function() self:handleBookmark() end},
        }}
    end

    self.button_table = ButtonTable:new {
        width = screen_w - 2 * Size.padding.large,
        buttons = action_buttons,
        zero_sep = true,
        show_parent = self,
    }

    local buttons_height = self.button_table:getSize().h

    local content_height = screen_h - titlebar:getHeight() - buttons_height - navigation_height
    if content_height < 0 then
        content_height = screen_h
    end

    self.scroll_widget = ScrollHtmlWidget:new {
        html_body = html_body,
        css = css,
        width = screen_w,
        height = content_height,
        dialog = self,
        html_link_tapped_callback = function(link)
            self:onLinkTapped(link)
        end,
    }

    self:attachScrollWidgetObservers()
    self:updatePrevNextButtonState()

    local layout_items = {titlebar}
    if self.browser_mode then
        layout_items[#layout_items+1] = self.navigation_table
        layout_items[#layout_items+1] = self.address_table
    end
    layout_items[#layout_items+1] = self.scroll_widget
    layout_items[#layout_items+1] = CenterContainer:new {
            dimen = Geom:new {
                w = screen_w,
                h = buttons_height,
            },
            self.button_table,
    }
    local layout = VerticalGroup:new(layout_items)

    local frame = FrameContainer:new {
        padding = 0,
        margin = 0,
        background = Blitbuffer.COLOR_WHITE,
        layout,
    }

    self[1] = frame
end

function MarkdownViewer:onBrowserBack()
    self:handleBack()
    return true
end

function MarkdownViewer:handleBack()
    if self.on_back then
        self.on_back()
    else
        self:onClose()
    end
end

function MarkdownViewer:handleSave()
    if self.on_save then
        self.on_save()
    else
        self:showUnavailableMessage(_("Save"))
    end
end

function MarkdownViewer:handleBookmark()
    if self.on_bookmark then
        self.on_bookmark()
    else
        self:showUnavailableMessage(_("Bookmark"))
    end
end

function MarkdownViewer:showUnavailableMessage(action_label)
    UIManager:show(InfoMessage:new {
        text = string.format(_("%s is not available yet."), action_label),
        timeout = 2,
    })
end

function MarkdownViewer:onLinkTapped(link)
    if not self.on_link or not link then
        return
    end

    local target = link
    if type(link) == "table" then
        target = link.uri or link.url or link.href or ""
    end

    if type(target) ~= "string" or target == "" then
        return
    end

    self.on_link(target)
end

function MarkdownViewer:onClose()
    UIManager:close(self)
    if self.on_close then
        self.on_close()
    end
    UIManager:setDirty("all", "full")
end

function MarkdownViewer:refreshAfterNavigation()
    if not self.scroll_widget then
        UIManager:setDirty("all", "full")
        return
    end

    local scroll_widget = self.scroll_widget
    if scroll_widget.resetScroll then
        scroll_widget:resetScroll()
    end

    if scroll_widget.scrollText and scroll_widget.htmlbox_widget and scroll_widget.htmlbox_widget.page_count > 1 then
        scroll_widget:scrollText(1)
        scroll_widget:resetScroll()
    end

    UIManager:setDirty(self, function()
        local frame = self[1]
        if frame and frame.dimen then
            return "partial", frame.dimen
        end
        if scroll_widget.dimen then
            return "partial", scroll_widget.dimen
        end
        return "ui", Screen:getRect()
    end)

    UIManager:setDirty("all", "full")
    self:updatePrevNextButtonState()
end

function MarkdownViewer:attachScrollWidgetObservers()
    if not self.scroll_widget then
        return
    end

    local widget = self.scroll_widget

    if widget.scrollText and not widget._webbrowser_scrollTextWrapped then
        local originalScrollText = widget.scrollText
        widget.scrollText = function(this, direction)
            local result = originalScrollText(this, direction)
            self:updatePrevNextButtonState()
            return result
        end
        widget._webbrowser_scrollTextWrapped = true
    end

    if widget.scrollToRatio and not widget._webbrowser_scrollToRatioWrapped then
        local originalScrollToRatio = widget.scrollToRatio
        widget.scrollToRatio = function(this, ratio)
            local result = originalScrollToRatio(this, ratio)
            self:updatePrevNextButtonState()
            return result
        end
        widget._webbrowser_scrollToRatioWrapped = true
    end

    if widget.resetScroll and not widget._webbrowser_resetScrollWrapped then
        local originalResetScroll = widget.resetScroll
        widget.resetScroll = function(this)
            local result = originalResetScroll(this)
            self:updatePrevNextButtonState()
            return result
        end
        widget._webbrowser_resetScrollWrapped = true
    end

    if widget.scrollToPage and not widget._webbrowser_scrollToPageWrapped then
        local originalScrollToPage = widget.scrollToPage
        widget.scrollToPage = function(this, page_num)
            local result = originalScrollToPage(this, page_num)
            self:updatePrevNextButtonState()
            return result
        end
        widget._webbrowser_scrollToPageWrapped = true
    end
end

function MarkdownViewer:scrollPreviousPage()
    if not self.scroll_widget or not self.scroll_widget.scrollText then
        return
    end
    self.scroll_widget:scrollText(-1)
end

function MarkdownViewer:scrollNextPage()
    if not self.scroll_widget or not self.scroll_widget.scrollText then
        return
    end
    self.scroll_widget:scrollText(1)
end

function MarkdownViewer:updatePrevNextButtonState()
    if not self.button_table or not self.scroll_widget or not self.scroll_widget.htmlbox_widget then
        return
    end

    local page_number = self.scroll_widget.htmlbox_widget.page_number or 1
    local page_count = self.scroll_widget.htmlbox_widget.page_count or 1

    self:setButtonEnabled("prev_page", page_number > 1)
    self:setButtonEnabled("next_page", page_number < page_count)
end

function MarkdownViewer:setButtonEnabled(button_id, enabled)
    local button = self.button_table:getButtonById(button_id)
    if not button then
        return
    end

    if enabled then
        button:enable()
    else
        button:disable()
    end
    button:refresh()
end

return MarkdownViewer
