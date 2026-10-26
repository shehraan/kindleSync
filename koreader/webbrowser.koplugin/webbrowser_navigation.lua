-- Small browser history and page formatting, independent from reader documents.
local url = require("socket.url")
local Navigation = {}
Navigation.__index = Navigation

function Navigation.escape(text)
    return tostring(text or ""):gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"):gsub('"', "&quot;")
end

function Navigation.input(text)
    text = (text or ""):gsub("^%s+", ""):gsub("%s+$", "")
    if text == "" then return nil, "Enter a website address or search words." end
    local scheme = text:match("^([%a][%w+.-]*):")
    if scheme and (scheme:lower() == "http" or scheme:lower() == "https") then
        return "url", scheme:lower() .. text:sub(#scheme+1):gsub("%s", "%%20")
    end
    if scheme then
        local blocked = {javascript=true, data=true, file=true, ftp=true, mailto=true, blob=true, about=true}
        if blocked[scheme:lower()] or text:match("^[%a][%w+.-]*://") then
            return nil, "Use an http or https website address."
        end
    end
    if not text:find("%s") then
        local host = text:match("^([^/:?#]+)") or ""
        if host == "localhost" or host:match("^[%w.-]+%.%a%a+$") or host:match("^%d+%.%d+%.%d+%.%d+$") then
            return "url", "https://" .. text
        end
    end
    return "search", text
end

function Navigation.resolve(base, target)
    if type(target) ~= "string" or target == "" then return nil end
    local absolute = url.absolute(base or "", target)
    if absolute and absolute:match("^https?://") then return absolute end
end

function Navigation.new()
    return setmetatable({entries={}, index=0}, Navigation)
end
function Navigation:current() return self.entries[self.index] end
function Navigation:canBack() return self.index > 1 end
function Navigation:canForward() return self.index < #self.entries end
function Navigation:push(entry)
    while #self.entries > self.index do table.remove(self.entries) end
    self.entries[#self.entries+1] = entry
    if #self.entries > 12 then table.remove(self.entries, 1) end
    self.index = #self.entries
    return entry
end
function Navigation:replace(entry) self.entries[self.index] = entry; return entry end
function Navigation:back() if self:canBack() then self.index = self.index-1 end; return self:current() end
function Navigation:forward() if self:canForward() then self.index = self.index+1 end; return self:current() end

function Navigation.resultsHtml(query, results)
    local chunks = {"<h2>" .. Navigation.escape(query) .. "</h2>"}
    if #results == 0 then chunks[#chunks+1] = "<p>No results found. Try different search words.</p>" end
    for _, result in ipairs(results) do
        chunks[#chunks+1] = '<p><a href="' .. Navigation.escape(result.url) .. '"><b>' .. Navigation.escape(result.title) .. '</b></a><br/>' .. Navigation.escape(result.snippet) .. '</p>'
    end
    return table.concat(chunks)
end

local function tagPattern(name)
    return name:gsub("%a", function(c) return "[" .. c:lower() .. c:upper() .. "]" end)
end

function Navigation.cleanHtml(html, base)
    -- Keep a readable document and links; the renderer does not execute scripts.
    for _, tag in ipairs({"script", "style", "noscript", "iframe", "svg", "object", "head"}) do
        local pattern = tagPattern(tag)
        html = html:gsub("<" .. pattern .. "[^>]*>.-</" .. pattern .. "%s*>", "")
    end
    for _, tag in ipairs({"img", "link", "input", "button", "meta", "base"}) do
        html = html:gsub("<" .. tagPattern(tag) .. "[^>]*>", "")
    end
    html = html:gsub("<[hH][tT][mM][lL][^>]*>", ""):gsub("</[hH][tT][mM][lL]%s*>", "")
    html = html:gsub("<[bB][oO][dD][yY][^>]*>", ""):gsub("</[bB][oO][dD][yY]%s*>", "")
    html = html:gsub("<([aA])([^>]-)[hH][rR][eE][fF]%s*=%s*([\"'])(.-)%3([^>]*)>", function(tag, before, quote, target, after)
        local absolute = Navigation.resolve(base, target:gsub("&amp;", "&"))
        return absolute and ('<a href="' .. Navigation.escape(absolute) .. '">') or "<span>"
    end)
    html = html:gsub("%s+[sS][tT][yY][lL][eE]%s*=%s*([\"']).-%1", "")
    html = html:gsub("%s+[wW][iI][dD][tT][hH]%s*=%s*([\"']).-%1", "")
    return html
end

return Navigation
