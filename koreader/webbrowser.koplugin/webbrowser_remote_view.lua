local Blitbuffer = require('ffi/blitbuffer')
local ButtonTable = require('ui/widget/buttontable')
local CenterContainer = require('ui/widget/container/centercontainer')
local Device = require('device')
local Font = require('ui/font')
local FrameContainer = require('ui/widget/container/framecontainer')
local Geom = require('ui/geometry')
local GestureRange = require('ui/gesturerange')
local ImageWidget = require('ui/widget/imagewidget')
local InputContainer = require('ui/widget/container/inputcontainer')
local TextBoxWidget = require('ui/widget/textboxwidget')
local TitleBar = require('ui/widget/titlebar')
local UIManager = require('ui/uimanager')
local VerticalGroup = require('ui/widget/verticalgroup')
local Screen = Device.screen

local View=InputContainer:extend{covers_fullscreen=true,modal=true,name='pc_web_browser'}

function View.normalize(rect,x,y)
    if not rect or type(x)~='number' or type(y)~='number' or rect.w<=0 or rect.h<=0
        or x<rect.x or y<rect.y or x>=rect.x+rect.w or y>=rect.y+rect.h then return end
    return (x-rect.x)/rect.w,(y-rect.y)/rect.h
end

function View:init()
    local width,height=Screen:getWidth(),Screen:getHeight()
    self.dimen=Geom:new{x=0,y=0,w=width,h=height}
    local page=self.page
    local titlebar=TitleBar:new{
        width=width,title=page and page.title~='' and page.title or 'PC Web Browser',
        title_shrink_font_to_fit=true,align='left',with_bottom_line=true,
        close_callback=function() self:onClose() end,show_parent=self,
    }
    local function button(id,text,callback,enabled)
        return {id=id,text=text,callback=callback,enabled=enabled~=false}
    end
    self.navigation_table=ButtonTable:new{
        width=width,zero_sep=true,show_parent=self,
        buttons={{
            button('remote_back','Back',function() self.on_action('back') end,page and page.can_back or false),
            button('remote_forward','Forward',function() self.on_action('forward') end,page and page.can_forward or false),
            button('remote_home','Home',function() self.on_action('home') end),
            button('remote_reload','Reload',function() self.on_action('reload') end,page~=nil),
            button('remote_refresh','Refresh',function() self.on_action('refresh') end),
        }},
    }
    self.address_table=ButtonTable:new{
        width=width,zero_sep=true,show_parent=self,
        buttons={{button('remote_address',page and page.url~='' and page.url or 'Search or enter address',self.on_address)}},
    }
    self.controls=ButtonTable:new{
        width=width,zero_sep=true,show_parent=self,
        buttons={
            {button('remote_page_up','Page up',function() self.on_action('scroll',{delta=-1}) end,page~=nil),
             button('remote_page_down','Page down',function() self.on_action('scroll',{delta=1}) end,page~=nil),
             button('remote_keyboard','Keyboard',self.on_text,page~=nil),
             button('remote_zoom',tostring(page and page.zoom or 2)..'×',self.on_zoom)},
            {button('remote_bookmarks','Bookmarks',self.on_bookmarks),
             button('remote_bookmark','Bookmark',self.on_bookmark,page~=nil)},
        },
    }
    local chrome=titlebar:getHeight()+self.navigation_table:getSize().h+self.address_table:getSize().h+self.controls:getSize().h
    self.frame_width=math.min(2048,width)
    self.frame_height=math.min(2048,math.max(320,height-chrome))
    local image
    if page then
        image=ImageWidget:new{
            file=page.path,file_do_cache=false,width=self.frame_width,height=self.frame_height,
            scale_for_dpi=false,original_in_nightmode=true,
        }
    else
        image=TextBoxWidget:new{
            text='Browse with your paired PC.\n\nTap the address bar to search or open a website.\nKeep your PC awake and KoboSync running.',
            width=math.floor(self.frame_width*.85),face=Font:getFace('infofont'),alignment='center',
        }
    end
    self.frame_area=InputContainer:new{
        dimen=Geom:new{x=0,y=0,w=self.frame_width,h=self.frame_height},
        CenterContainer:new{dimen=Geom:new{w=self.frame_width,h=self.frame_height},image},
    }
    self[1]=FrameContainer:new{
        background=Blitbuffer.COLOR_WHITE,padding=0,margin=0,bordersize=0,
        VerticalGroup:new{
            titlebar,self.navigation_table,self.address_table,
            CenterContainer:new{dimen=Geom:new{w=width,h=self.frame_height},self.frame_area},
            self.controls,
        },
    }
    if Device:isTouchDevice() then
        self.ges_events={
            FrameTap={GestureRange:new{ges='tap',range=function() return self.frame_area.dimen end}},
            FrameSwipe={GestureRange:new{ges='swipe',range=function() return self.frame_area.dimen end}},
        }
    end
    if Device:hasKeys() then
        self.key_events={
            RemotePageUp={{Device.input.group.PgBack}},
            RemotePageDown={{Device.input.group.PgFwd}},
            RemoteBack={{Device.input.group.Back}},
        }
    end
end

function View:onFrameTap(_,ges)
    if not self.page or not ges or not ges.pos then return false end
    local x,y=View.normalize(self.frame_area.dimen,ges.pos.x,ges.pos.y)
    if x then self.on_action('click',{x=x,y=y});return true end
    return false
end

function View:onFrameSwipe(_,ges)
    if not self.page then return false end
    if ges.direction=='north' then self.on_action('scroll',{delta=1});return true end
    if ges.direction=='south' then self.on_action('scroll',{delta=-1});return true end
    if ges.direction=='west' then self.on_action('scroll',{delta=0,delta_x=1});return true end
    if ges.direction=='east' then self.on_action('scroll',{delta=0,delta_x=-1});return true end
    return false
end

function View:onRemotePageUp() self.on_action('scroll',{delta=-1});return true end
function View:onRemotePageDown() self.on_action('scroll',{delta=1});return true end
function View:onRemoteBack()
    if self.page and self.page.can_back then self.on_action('back') else self:onClose() end
    return true
end
function View:onClose() if self.on_close then self.on_close() end end
function View:onShow() UIManager:setDirty(self,'full');return true end
function View:onCloseWidget()
    self:free()
    UIManager:setDirty('all','full')
    return true
end

return View
