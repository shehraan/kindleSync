-- Public RSS results keep web search usable without a JavaScript challenge.
local Http = require("webbrowser_http")
local socketutil = require("socketutil")
local url = require("socket.url")
local Utils = require("webbrowser_utils")
local Bing = {}

local function text(value)
    value = (value or ""):gsub("<!%[CDATA%[(.-)%]%]>", "%1")
    return (Utils.clean_text(value):gsub("<[^>]*>", ""):gsub("%s+", " "))
end

function Bing.search(query, settings)
    settings = settings or {}
    local parts, size = {}, 0
    socketutil:set_timeout(10,30)
    local ok, received, code = pcall(Http.request, {
        url="https://www.bing.com/search?format=rss&q=" .. url.escape(query),
        headers={ ["user-agent"]="Mozilla/5.0 (compatible; KOReader)", accept="application/rss+xml,text/xml" },
        sink=function(chunk)
            if chunk then
                size = size + #chunk
                if size > 512*1024 then return nil, "Search response is too large." end
                parts[#parts+1] = chunk
            end
            return 1
        end,
    })
    socketutil:reset_timeout()
    if not ok then return nil,tostring(received) end
    if not received then return nil,tostring(code or "Search unavailable.") end
    if tonumber(code) ~= 200 then return nil,"Search unavailable (HTTP " .. tostring(code) .. ")." end
    local body = table.concat(parts)
    if not body:find("<rss",1,true) then return nil,"The search service returned an unreadable response." end
    local results = {}
    for item in body:gmatch("<item>(.-)</item>") do
        local target = text(item:match("<link>(.-)</link>"))
        local title = text(item:match("<title>(.-)</title>"))
        if target:match("^https?://") and title ~= "" then
            results[#results+1] = {url=target,title=title,snippet=text(item:match("<description>(.-)</description>"))}
        end
        if #results >= (settings.max_results or 10) then break end
    end
    return results
end

return Bing
