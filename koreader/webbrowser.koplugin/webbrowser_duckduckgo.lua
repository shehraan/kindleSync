local HtmlParser = require("htmlparser")
local WebBrowserHttp = require("webbrowser_http")
local socket_url = require("socket.url")
local socket = require("socket")
local ltn12 = require("ltn12")
local socketutil = require("socketutil")
local Utils = require("webbrowser_utils")

local DuckDuckGo = {}

local DEFAULT_TIMEOUT = 15
local DEFAULT_MAXTIME = 30

local function fetch(url, timeout, maxtime, accept_language)
    local response_chunks = {}
    socketutil:set_timeout(timeout or DEFAULT_TIMEOUT, maxtime or DEFAULT_MAXTIME)
    
    local headers = {
        ["user-agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    if accept_language then
        headers["accept-language"] = accept_language
    end
    
    local request = {
        url = url,
        method = "GET",
        sink = ltn12.sink.table(response_chunks),
        headers = headers
    }

    local ok, received, code, response_headers = pcall(WebBrowserHttp.request, request)
    socketutil:reset_timeout()
    if not ok then
        return nil, tostring(received)
    end
    if not received then
        return nil, tostring(code or "Search request failed")
    end
    return table.concat(response_chunks), response_headers, tonumber(code) or 0
end

local function extract_text(node)
    local raw = node:getcontent() or node:gettext()
    return (Utils.clean_text(raw:gsub("<[^>]*>", "")):gsub("%s+", " "))
end

local function find_nodes_by_class(node, class_name, found)
    found = found or {}
    if node.attributes and node.attributes.class then
        for cls in node.attributes.class:gmatch("%S+") do
            if cls == class_name then
                table.insert(found, node)
                break
            end
        end
    end
    if node.nodes then
        for _, child in ipairs(node.nodes) do
            find_nodes_by_class(child, class_name, found)
        end
    end
    return found
end

local function find_first_child_with_class(node, tag_name, class_name)
    if not node.nodes then
        return nil
    end
    for _, child in ipairs(node.nodes) do
        local matches_tag = not tag_name or child.name == tag_name
        local matches_class = false
        if child.attributes and child.attributes.class then
            for cls in child.attributes.class:gmatch("%S+") do
                if cls == class_name then
                    matches_class = true
                    break
                end
            end
        end
        if matches_tag and matches_class then
            return child
        end
        local nested = find_first_child_with_class(child, tag_name, class_name)
        if nested then
            return nested
        end
    end
    return nil
end

local function parse_results(html)
    local results = {}
    local root = HtmlParser.parse(html)
    local nodes = find_nodes_by_class(root, "result")
    for _, node in ipairs(nodes) do
        local link_node = find_first_child_with_class(node, "a", "result__a")
        local snippet_node = find_first_child_with_class(node, "a", "result__snippet")
        if link_node then
            local href = link_node.attributes and link_node.attributes.href or ""
            local title = extract_text(link_node)
            local snippet = snippet_node and extract_text(snippet_node) or ""
            if href ~= "" and title ~= "" then
                href = Utils.decode_result_url(href)
                table.insert(results, {
                    url = href,
                    title = title,
                    snippet = snippet,
                })
            end
        end
        if #results >= DuckDuckGo.max_results then
            break
        end
    end
    -- DuckDuckGo Lite uses a compact table instead of HTML's result cards.
    -- Both are valid result pages; an empty card list is not proof of no hits.
    if #results == 0 then
        local links = find_nodes_by_class(root, "result-link")
        local snippets = find_nodes_by_class(root, "result-snippet")
        for index, link in ipairs(links) do
            local href = link.attributes and link.attributes.href or ""
            local title = extract_text(link)
            if href ~= "" and title ~= "" then
                href = Utils.decode_result_url(Utils.clean_text(href))
                if href:sub(1, 2) == "//" then href = "https:" .. href end
                if href:match("^https?://") then
                    results[#results+1] = {
                        url = href, title = title,
                        snippet = snippets[index] and extract_text(snippets[index]) or "",
                    }
                end
            end
            if #results >= DuckDuckGo.max_results then break end
        end
    end
    return results
end

function DuckDuckGo.search(query, opts)
    local settings = opts or {}
    DuckDuckGo.base_url = settings.base_url or "https://lite.duckduckgo.com"
    DuckDuckGo.search_path = settings.search_path or "/lite/"
    
    local country = (settings.country or "us"):lower()
    local language = (settings.language or "en"):lower()
    local kl = country .. "-" .. language
    
    local accept_language = language .. "-" .. country:upper() .. "," .. language .. ";q=0.9"
    
    DuckDuckGo.max_results = settings.max_results or 50

    local primary = DuckDuckGo.base_url:gsub("/$", "") .. DuckDuckGo.search_path
    local fallback = primary:find("lite.duckduckgo.com", 1, true)
        and "https://html.duckduckgo.com/html/" or "https://lite.duckduckgo.com/lite/"
    local last_error
    for _, endpoint in ipairs({primary, fallback}) do
        local query_url = string.format("%s?q=%s&kl=%s&kp=1&kz=1", endpoint, socket_url.escape(query), kl)
        local html, headers_or_error, code = fetch(query_url, nil, nil, accept_language)
        if not html then
            return nil, headers_or_error or "Failed to fetch search results"
        end
        local parsed, results = pcall(parse_results, html)
        if code >= 200 and code < 300 and parsed and #results > 0 then
            return results
        end
        local lower = html:lower()
        if code == 202 or code == 403 or code == 429
            or lower:find("<form[^>]+id=[\"']challenge%-form[\"']")
            or lower:find("<form[^>]+id=[\"']anomaly%-form[\"']") then
            last_error = "DuckDuckGo needs browser verification or is rate-limiting this connection. Retry later, or open a website address with Go."
        elseif code >= 200 and code < 300 and parsed and lower:find("no results found", 1, true) then
            return {}
        elseif code < 200 or code >= 300 then
            last_error = "DuckDuckGo search failed (HTTP " .. tostring(code) .. ")."
        else
            last_error = "DuckDuckGo returned an unreadable search response. Open a website address with Go, or retry later."
        end
    end
    return nil, last_error
end

return DuckDuckGo
