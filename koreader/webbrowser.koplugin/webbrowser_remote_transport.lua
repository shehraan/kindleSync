-- Paired browser transport. Only metadata crosses the subprocess pipe; PNG
-- bytes are hashed and streamed straight into this module's private cache.
local DataStorage = require('datastorage')
local Http = require('webbrowser_http')
local json = require('json')
local lfs = require('libs/libkoreader-lfs')
local ltn12 = require('ltn12')
local sha256 = require('ffi/sha2').sha256
local socketutil = require('socketutil')
local util = require('util')
local ffi = require('ffi')
ffi.cdef[[int chmod(const char *path, unsigned int mode);]]

local Transport = {}
local MAX_FRAME = 8 * 1024 * 1024
local MAX_METADATA = 32768
local UNAVAILABLE = 'Could not reach your paired PC. Check that it is awake, KoboSync is running, and both devices are on the same Wi-Fi.'

local function integer(v, low, high)
    return type(v)=='number' and v==math.floor(v) and v>=low and v<=high
end

local function directory()
    return DataStorage:getDataDir()..'/cache/webbrowser-remote'
end

local function owned(path)
    if type(path)~='string' then return false end
    local prefix=directory()..'/frame-'
    return path:sub(1,#prefix)==prefix and path:sub(#prefix+1):match('^[a-f0-9]+%-%d+%.png$')~=nil
end

function Transport.remove(path)
    if owned(path) then os.remove(path);os.remove(path..'.part') end
end

function Transport.discard(frame_id)
    if type(frame_id)=='string' and #frame_id<=64 and frame_id:match('^[a-f0-9]+%-%d+$') then
        Transport.remove(directory()..'/frame-'..frame_id..'.png')
    end
end

function Transport.clear()
    local dir=directory()
    if not lfs.dir then return end
    local attrs=lfs.symlinkattributes(dir)
    if not attrs or attrs.mode~='directory' then return end
    for name in lfs.dir(dir) do
        local base=name:gsub('%.part$','')
        if owned(dir..'/'..base) then os.remove(dir..'/'..name) end
    end
end

function Transport.newId()
    local f=io.open('/dev/urandom','rb')
    if not f then return nil end
    local value=f:read(8);f:close()
    if not value or #value~=8 then return nil end
    return (value:gsub('.',function(c) return string.format('%02x',c:byte()) end))
end

local function frameMetadata(value)
    if type(value)~='table' or type(value.session)~='string' or #value.session~=32 or not value.session:match('^[a-f0-9]+$')
        or not integer(value.generation,1,2147483647) or not integer(value.width,320,2048) or not integer(value.height,320,2048)
        or not integer(value.frame_bytes,24,MAX_FRAME) or type(value.frame_sha256)~='string' or #value.frame_sha256~=64
        or not value.frame_sha256:match('^[a-f0-9]+$')
        or value.frame_url~='/v1/browser/frame/'..value.session..'/'..tostring(value.generation)
        or type(value.url)~='string' or #value.url>8192 or type(value.title)~='string' or #value.title>2048
        or type(value.zoom)~='number' or value.zoom<1 or value.zoom>3
        or type(value.can_back)~='boolean' or type(value.can_forward)~='boolean' then return end
    local focus=value.focus
    if type(focus)~='table' or not ({none=true,text=true,password=true,select=true})[focus.kind] then return end
    local cleaned={kind=focus.kind,value=focus.kind=='password' and '' or tostring(focus.value or ''):sub(1,4096)}
    if focus.kind=='select' then
        if type(focus.options)~='table' or #focus.options>512 then return end
        cleaned.options={}
        for _,option in ipairs(focus.options) do
            if type(option)~='table' or type(option.label)~='string' or #option.label>1024
                or type(option.value)~='string' or #option.value>4096 then return end
            cleaned.options[#cleaned.options+1]={label=option.label,value=option.value}
        end
    end
    -- Return a whitelist so an unexpected server key cannot inject a local path
    -- or enlarge the IPC message with binary data.
    return {session=value.session,generation=value.generation,url=value.url,title=value.title,
        can_back=value.can_back,can_forward=value.can_forward,zoom=value.zoom,width=value.width,height=value.height,
        frame_url=value.frame_url,frame_sha256=value.frame_sha256,frame_bytes=value.frame_bytes,focus=cleaned,
        notice=type(value.notice)=='string' and value.notice:sub(1,400) or nil,
        download_url=type(value.download_url)=='string' and value.download_url:match('^https?://') and value.download_url:sub(1,8192) or nil}
end

local function request(origin, token, method, path, body, sink, closing)
    -- Rendering can take up to 35 seconds on the PC, including a cold engine
    -- start. Waiting only five seconds for response headers falsely marked a
    -- reachable PC offline. Image transfers retain their short stall timeout;
    -- the cancellable UI worker still owns the whole operation.
    local block_timeout=method=='POST' and 40 or 5
    socketutil:set_timeout(closing and 2 or block_timeout,closing and 4 or 55)
    -- A paired LAN endpoint must not inherit the reader's optional website
    -- proxy: that would disclose its bearer token to the proxy operator.
    local socket_http=package.loaded['socket.http']
    local proxy=socket_http and socket_http.PROXY
    if socket_http then socket_http.PROXY=nil end
    local ok, received, code, headers=pcall(Http.request,{
        url=origin..path,method=method,redirect=false,
        headers={Authorization='Bearer '..token,['Content-Type']=body and 'application/json' or nil,
            ['Content-Length']=body and tostring(#body) or nil,Accept=method=='GET' and 'image/png' or 'application/json'},
        source=body and ltn12.source.string(body) or nil,sink=sink,
    })
    if socket_http then socket_http.PROXY=proxy end
    socketutil:reset_timeout()
    if not ok then return nil,'The browser request failed on the Kobo. Close the browser and try again.' end
    if not received then
        if code=='timeout' then return nil,'The PC browser request timed out. Try Refresh.' end
        return nil,UNAVAILABLE
    end
    code=tonumber(code)
    if code~=200 then
        if code==401 then return nil,'The PC rejected your KoboSync pairing token.',code end
        if code==409 then return nil,'The page changed. Refreshing the view.',code end
        if code and code>=500 then return nil,'Your PC is reachable, but its browser could not finish this action. Try Refresh.',code end
        return nil,'The PC browser could not complete this action. Try Refresh.',code
    end
    return true,nil,code,headers
end

local function pngSize(prefix)
    if prefix:sub(1,8)~='\137PNG\13\10\26\10' or prefix:sub(9,16)~='\0\0\0\13IHDR' then return end
    local function number(offset)
        local a,b,c,d=prefix:byte(offset,offset+3)
        if not d then return end
        return ((a*256+b)*256+c)*256+d
    end
    return number(17),number(21)
end

function Transport.exchange(origin, token, payload, frame_id)
    if type(origin)~='string' or not origin:match('^http://[%w%.%-]+:?%d*$')
        or type(token)~='string' or token=='' or token:find('[\r\n]') or type(payload)~='table'
        or type(frame_id)~='string' or not frame_id:match('^[a-f0-9]+%-%d+$') or #frame_id>64 then
        return nil,'Invalid paired browser configuration.'
    end
    local encoded_ok,body=pcall(json.encode,payload)
    if not encoded_ok or #body>MAX_METADATA then return nil,'Browser input is too large.' end
    local response,size={},0
    local ok,err,status=request(origin,token,'POST','/v1/browser/action',body,function(chunk)
        if chunk then size=size+#chunk;if size>MAX_METADATA then return nil,'Browser response is too large.' end;response[#response+1]=chunk end
        return 1
    end,payload.action=='close')
    if not ok then return nil,err,status end
    local decoded_ok,value=pcall(json.decode,table.concat(response))
    response=nil
    if not decoded_ok then return nil,'The PC returned an unreadable browser response.' end
    if payload.action=='close' and type(value)=='table' and value.closed==true then return {closed=true} end
    local page=frameMetadata(value)
    if not page then return nil,'The PC returned invalid browser frame details.' end
    if payload.width and (page.width~=payload.width or page.height~=payload.height) then return nil,'The PC returned the wrong viewport size.' end
    local dir=directory()
    local attributes=lfs.symlinkattributes(dir)
    if attributes and attributes.mode~='directory' then return nil,'Browser cache is not a private directory.' end
    if not util.makePath(dir) then return nil,'Could not create the browser cache.' end
    -- Kobo's onboard FAT filesystem has no Unix permission bits. Tighten
    -- permissions where supported; confinement and symlink checks also apply
    -- on FAT, where this cache is private application data like reader settings.
    ffi.C.chmod(dir,448)
    local path=dir..'/frame-'..frame_id..'.png'
    if lfs.symlinkattributes(path..'.part') or lfs.symlinkattributes(path) then return nil,'Browser frame cache already exists.' end
    local file=io.open(path..'.part','wb')
    if not file then return nil,'Could not save the browser frame.' end
    ffi.C.chmod(path..'.part',384)
    local hash=sha256()
    local bytes,prefix=0,''
    -- The fork may begin with GC stopped. Reclaim distinct network chunks as
    -- they stream, instead of retaining up to eight MiB in the child's heap.
    local was_running=collectgarbage('isrunning')
    collectgarbage('restart')
    local ran,accepted,message,frame_status=pcall(function()
        return request(origin,token,'GET',page.frame_url,nil,function(chunk)
            if chunk then
                bytes=bytes+#chunk
                if bytes>page.frame_bytes or bytes>MAX_FRAME then return nil,'Browser frame exceeds its size limit.' end
                if #prefix<24 then prefix=prefix..chunk:sub(1,24-#prefix) end
                if not file:write(chunk) then return nil,'Could not save browser frame.' end
                hash(chunk)
                collectgarbage('step',32)
            end
            return 1
        end)
    end)
    local flushed=file:close()
    if not was_running then collectgarbage('stop') end
    local width,height=pngSize(prefix)
    if not flushed or not ran or not accepted or bytes~=page.frame_bytes or hash()~=page.frame_sha256 or width~=page.width or height~=page.height then
        os.remove(path..'.part')
        return nil,ran and message or 'The PC returned an invalid browser frame.',frame_status
    end
    if not os.rename(path..'.part',path) then os.remove(path..'.part');return nil,'Could not publish the browser frame.' end
    page.path=path
    return page
end

return Transport
