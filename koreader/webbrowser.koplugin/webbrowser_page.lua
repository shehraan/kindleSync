local Http = require("webbrowser_http")
local socketutil = require("socketutil")
local Navigation = require("webbrowser_navigation")
local Page = {}
local MAX_BYTES = 2 * 1024 * 1024

function Page.load(target)
    for _ = 1, 4 do
        local chunks, size, document_download = {}, 0, false
        socketutil:set_timeout(10, 30)
        local ok, received, code, headers = pcall(Http.request, {
            url=target, method="GET", redirect=false,
            headers={ ["user-agent"]="Mozilla/5.0 (compatible; KOReader)", accept="text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1" },
            sink=function(chunk)
                if chunk then
                    if size == 0 and (chunk:sub(1,4) == "PK\003\004" or chunk:sub(1,4) == "%PDF") then
                        document_download = true
                        return nil, "Document download"
                    end
                    size = size + #chunk
                    if size > MAX_BYTES then return nil, "This page is too large for the text browser." end
                    chunks[#chunks+1] = chunk
                end
                return 1
            end,
        })
        socketutil:reset_timeout()
        if document_download then return {kind="download", url=target} end
        if not ok then return nil, tostring(received) end
        if not received then return nil, tostring(code or "Could not load this website.") end
        code, headers = tonumber(code), headers or {}
        if code == 301 or code == 302 or code == 303 or code == 307 or code == 308 then
            target = Navigation.resolve(target, headers.location)
            if not target then return nil, "The website returned an invalid redirect." end
        elseif code and code >= 200 and code < 300 then
            local body = table.concat(chunks)
            local content_type = (headers["content-type"] or "text/html"):lower()
            if content_type:find("epub", 1, true) or content_type:find("pdf", 1, true) then
                return {kind="download", url=target}
            end
            if not content_type:find("html", 1, true) and not content_type:find("text/plain", 1, true) then
                return nil, "This link is not a readable web page."
            end
            local title = body:match("<[tT][iI][tT][lL][eE][^>]*>(.-)</[tT][iI][tT][lL][eE]%s*>") or target
            title = title:gsub("<[^>]*>", ""):gsub("%s+", " "):sub(1, 200)
            if content_type:find("text/plain", 1, true) then body = "<pre>" .. Navigation.escape(body) .. "</pre>"
            else body = Navigation.cleanHtml(body, target) end
            return {kind="url", input=target, url=target, title=title, html=body}
        else
            return nil, "Website returned HTTP " .. tostring(code or "error") .. "."
        end
    end
    return nil, "This website redirects too many times."
end

return Page
