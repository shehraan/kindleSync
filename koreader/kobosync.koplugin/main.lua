local WidgetContainer = require("ui/widget/container/widgetcontainer")
local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local DataStorage = require("datastorage")
local LuaSettings = require("luasettings")
local NetworkMgr = require("ui/network/manager")
local md5 = require("ffi/sha2").md5

local function createNetworkWorker()
    -- One short-lived, low-priority network process; the reader never waits on sockets.
    local UIManager = require("ui/uimanager")
    local ffiutil = require("ffi/util")
    local json = require("json")
    local Worker = {}
    local active
    local poll

    poll = function()
        local job = active
        if not job then return end
        if not ffiutil.isSubProcessDone(job.pid) then
            job.polls = job.polls + 1
            -- A cancel immediately after fork can precede child setpgid().
            -- Retry while it is alive, so that race cannot leave a worker behind.
            if job.cancelled or os.time() - job.started >= job.timeout or job.polls >= job.timeout * 4 then
                ffiutil.terminateSubProcess(job.pid)
                job.cancelled = true
            end
            UIManager:scheduleIn(0.25, poll)
            return
        end
        local bytes = ffiutil.readAllFromFD(job.fd)
        active = nil
        if job.callback then
            local ok, result = pcall(json.decode, bytes)
            if job.cancelled or not ok or type(result) ~= "table" then
                result = {sent={}, message="Server unavailable. Position remains saved locally."}
            end
            job.callback(result)
        end
    end

    function Worker.busy() return active ~= nil end

    function Worker.start(owner, task, callback, may_upload)
        if active then return false end
        local pid, fd = ffiutil.runInSubProcess(function(_, output)
            -- Avoid garbage collection dirtying the reader's shared heap after fork.
            collectgarbage("stop")
            local ok, result = pcall(task)
            if not ok then result = {sent={}, message="Server unavailable. Position remains saved locally."} end
            local encoded_ok, encoded = pcall(json.encode, result)
            if not encoded_ok or #encoded > 3500 then encoded = json.encode({sent={},message="Unexpected sync response. Position remains saved locally."}) end
            ffiutil.writeToFD(output, encoded, true)
        end, true)
        if not pid then return nil, "Cannot start background sync. Position remains saved locally." end
        active = {owner=owner, pid=pid, fd=fd, callback=callback, started=os.time(), polls=0, timeout=may_upload and 900 or 60}
        UIManager:scheduleIn(0.25, poll)
        return true
    end

    function Worker.cancel(owner)
        if active and active.owner == owner then
            -- Release the old reader object immediately; do not write its settings
            -- after a different book has opened. Unacknowledged records stay queued.
            active.owner, active.callback = nil, nil
            ffiutil.terminateSubProcess(active.pid)
            active.cancelled = true
        end
    end

    function Worker.send(server, token, entries, resolve)
        local http = require("socket.http")
        local ltn12 = require("ltn12")
        local socketutil = require("socketutil")
        local result = {sent={}}
        result.server = resolve(server, token)
        if not result.server then
            result.message = "Could not find your paired server. Check that the PC is awake and on the same home Wi-Fi."
            return result
        end
        local function request(path, source, length, content_type, upload)
            local response, size = {}, 0
            -- Allow a large EPUB to make steady progress over Kobo Wi-Fi.
            -- Individual stalled socket operations still fail after 5 seconds.
            socketutil:set_timeout(upload and 5 or 3, upload and 840 or 8)
            -- Distinct binary chunks must be reclaimed while streaming. Keeping
            -- the fork's GC paused here would retain every EPUB byte in RAM.
            if upload then collectgarbage("restart") end
            local ok, _, status = pcall(http.request, {
                url=result.server:gsub("/$", "") .. path, method="POST",
                headers={Authorization="Bearer " .. token, ["Content-Type"]=content_type, ["Content-Length"]=tostring(length)},
                source=source,
                sink=function(chunk)
                    if chunk then
                        size = size + #chunk
                        if size > 4096 then return nil, "Oversized sync response" end
                        response[#response+1] = chunk
                    end
                    return 1
                end,
            })
            if upload then collectgarbage("stop") end
            socketutil:reset_timeout()
            local decoded_ok, decoded = pcall(json.decode, table.concat(response))
            if not decoded_ok or type(decoded) ~= "table" then decoded = {} end
            if not ok or (tonumber(status) ~= 200 and tonumber(status) ~= 202) then
                local message = tonumber(status) == 401 and "Pairing token rejected." or decoded.error
                if type(message) ~= "string" or message == "" then message = "Server unavailable. Position remains saved locally." end
                return nil, message:sub(1,400), tonumber(status)
            end
            return decoded
        end
        for index, item in ipairs(entries) do
            local name = tostring(item.observation.content_id or "EPUB")
            if #name > 160 then
                name = name:sub(1,160):gsub("[\194-\244][\128-\191]*$", "") .. "..."
            end
            local body = json.encode(item.observation)
            local response, message, status = request("/v1/progress", ltn12.source.string(body), #body, "application/json")
            if response and response.needs_epub then
                local file = type(item.ebook_path) == "string" and io.open(item.ebook_path, "rb")
                if not file then
                    response, message = nil, name .. " is unavailable. Its position is saved on the PC; reopen the EPUB to upload it."
                else
                    local length = file:seek("end")
                    file:seek("set",0)
                    if not length then
                        response, message = nil, "Cannot read the file size of " .. name .. ". Its position remains saved."
                    elseif length == 0 then
                        response, message = nil, name .. " is empty. Its position remains saved."
                    elseif length > 256 * 1024 * 1024 then
                        response, message = nil, string.format("%s is %.1f MiB; the upload limit is 256 MiB. Its position remains saved.", name, length / 1024 / 1024)
                    else
                        response, message, status = request("/v1/ebooks/" .. item.observation.epub_md5,
                            function() return file:read(32768) end, length, "application/epub+zip", true)
                    end
                    file:close()
                end
            end
            if response then
                result.sent[#result.sent+1] = item.key
                if index == 1 then result.latest_sent = true end
                if index == 1 and response.book_state and response.book_state ~= "ready" then
                    result.preparation_message = type(response.message) == "string" and response.message:sub(1,400)
                        or "Reading position sent. Narration is being prepared on the PC."
                end
            else
                result.message = message
                if not status or status ~= 400 then break end
            end
        end
        return result
    end

    return Worker
end

local worker_key = "kobosync.worker"
if not package.loaded[worker_key] then package.loaded[worker_key] = createNetworkWorker() end
local networkWorker = package.loaded[worker_key]

local KoboSync = WidgetContainer:extend{ name = "kobosync", is_doc_only = true }

function KoboSync:init()
    -- The singleton also prevents overlapping workers when switching books.
    self.worker = networkWorker
    self.worker.cancel(self)
    self.closed, self.dirty, self.save_scheduled, self.interactive_pending = false, false, nil, nil
    self.marked_viewport, self.epub_md5, self.epub_path, self.last_sync_message = nil, nil, nil, nil
    self.capture_pending, self.pending_event_time = false, nil
    self.checkpoints = {}
    self.settings = LuaSettings:open(DataStorage:getSettingsDir() .. "/kobosync.lua")
    self.pending = self.settings:readSetting("pending", {})
    self.sequence = self.settings:readSetting("sequence", 0)
    self.last_locator = nil
    self.last_event = os.time()
    self:compactPending()
    local ok, config = pcall(dofile, self.path .. "/config.lua")
    self.config = ok and config or {}
    self.discovery = dofile(self.path .. "/discovery.lua")
    self.endpoint_key = md5((self.config.server or "") .. "\n" .. (self.config.token or ""))
    local cached = self.settings:readSetting("endpoint_cache", {})
    if cached.key == self.endpoint_key and self.discovery.private_origin(cached.server) then
        self.config.server = cached.server
    end
    self.ui.menu:registerToMainMenu(self)
    self.flush_task = function() if not self.closed then self:flush() end end
    self.save_task = function()
        if self.capture_pending then UIManager:scheduleIn(2, self.save_task)
        else self:persist() end
    end
    self.capture_task = function()
        if self.closed then return end
        self.capture_pending = false
        self:capture(nil, false, true, self.pending_event_time)
        self.pending_event_time = nil
        self:flush()
    end
    if self.dirty then self:deferSave() end
    if self.ui.highlight then
        self.ui.highlight:addToHighlightDialog("99_kobosync", function(highlight)
            return {
                text = "Resume audio here",
                callback = function()
                    local selection = highlight.selected_text
                    if selection and type(selection.pos0) == "string" then
                        self:capture(selection.pos0, true)
                        self:flush(true)
                    end
                end,
            }
        end)
    end
end

function KoboSync:addToMainMenu(menu)
    menu.kobosync = {
        text = "Send audio to phone",
        callback = function()
            self:capture()
            if NetworkMgr:isConnected() then self:flush(true)
            else
                UIManager:show(InfoMessage:new{ text = "Position saved locally. Connect Wi-Fi and send again before morning." })
            end
        end,
    }
end

-- One latest record per book and UTC hour preserves the 06:00 Toronto
-- cutoff across DST. Exact-hour observations have their own slot because the
-- server includes observations at the cutoff itself. Older days are retained.
local function checkpointKey(observation, fallback)
    local timestamp = observation.source_time
    if type(timestamp) ~= "string" or not timestamp:match("^%d%d%d%d%-%d%d%-%d%dT%d%d:%d%d:%d%dZ$") then return fallback end
    local bucket = timestamp:sub(15,19) == "00:00" and timestamp or timestamp:sub(1,13)
    return tostring(observation.content_id) .. "\0" .. tostring(observation.epub_md5) .. "\0" .. bucket
end

function KoboSync:compactPending()
    local compact = {}
    for key, observation in pairs(self.pending) do
        local bucket = checkpointKey(observation, key)
        local old_key = self.checkpoints[bucket]
        local old = old_key and compact[old_key]
        if not old or observation.source_time > old.source_time or
            (observation.source_time == old.source_time and (observation.sequence or 0) > (old.sequence or 0)) then
            if old_key then compact[old_key] = nil; self.dirty = true end
            compact[key], self.checkpoints[bucket] = observation, key
        else self.dirty = true end
    end
    self.pending = compact
    self.settings:saveSetting("pending", compact)
end

function KoboSync:persist()
    if not self.dirty then return end
    UIManager:unschedule(self.save_task)
    self.settings:saveSetting("pending", self.pending)
    self.settings:saveSetting("sequence", self.sequence)
    self.settings:flush()
    self.dirty, self.save_scheduled = false, false
end

function KoboSync:deferSave()
    if not self.save_scheduled then
        self.save_scheduled = true
        UIManager:scheduleIn(30, self.save_task)
    end
end

function KoboSync:capture(anchor, explicit, deferred, event_time)
    local doc = self.ui.document
    if not doc or doc.info.has_pages or not self.ui.rolling then return end
    local filename = doc.file:match("([^/]+)$")
    if not filename or not filename:lower():match("%.epub$") then return end
    if self.epub_path and self.epub_path ~= doc.file then
        self.last_locator, self.marked_viewport = nil, nil
    end
    local xp = anchor or self.ui.rolling:getLastProgress()
    if not xp then return end
    if anchor then
        self.marked_viewport = self.ui.rolling:getLastProgress()
    elseif not explicit and self.marked_viewport == xp then
        return
    else
        self.marked_viewport = nil
    end
    -- A selected word supplies an exact anchor; ordinary capture uses the
    -- saved viewport position. Never infer the last sentence the user read.
    if not explicit and xp == self.last_locator then return end
    if xp ~= self.last_locator or explicit then self.last_event = event_time or os.time() end
    local finish = xp
    for _ = 1, 65 do
        local next_end = doc:getNextVisibleWordEnd(finish)
        if not next_end or next_end == finish then break end
        finish = next_end
    end
    local text = doc:getTextFromXPointers(xp, finish)
    if type(text) ~= "string" or #text < 20 then return end
    if not self.epub_md5 or self.epub_path ~= doc.file then
        local f = io.open(doc.file, "rb")
        if not f then return end
        local append = md5()
        while true do
            local chunk, read_error = f:read(32768)
            if not chunk then
                f:close()
                if read_error then return end
                break
            end
            append(chunk)
        end
        self.epub_md5, self.epub_path = append(), doc.file
    end
    self.sequence = self.sequence + 1
    local observation = {
        reader = "koreader", content_id = filename, epub_md5 = self.epub_md5,
        ebook_path = doc.file,
        locator = xp, passage = text,
        sequence = self.sequence,
        source_time = os.date("!%Y-%m-%dT%H:%M:%SZ", self.last_event),
    }
    local key = tostring(self.sequence)
    local bucket = checkpointKey(observation, key)
    local old_key = self.checkpoints[bucket]
    if old_key then self.pending[old_key] = nil end
    self.pending[key], self.checkpoints[bucket] = observation, key
    self.last_locator = xp
    self.dirty = true
    if deferred then self:deferSave() else self:persist() end
end

function KoboSync:flush(interactive)
    if self.closed then return end
    if interactive then self.interactive_pending = true end
    -- This is a LAN service; public DNS reachability is not required.
    if not NetworkMgr:isConnected() then
        if interactive then
            UIManager:show(InfoMessage:new{ text = "Position saved locally. Connect Wi-Fi and send again before morning." })
        end
        return
    end
    if not self.config.server or not self.config.token then
        if interactive then UIManager:show(InfoMessage:new{ text = "Copy config.example.lua to config.lua and set your server and token." }) end
        return
    end
    if next(self.pending) and self.worker.busy() then
        UIManager:unschedule(self.flush_task)
        UIManager:scheduleIn(1, self.flush_task)
        return
    end
    local ordered = {}
    for key, observation in pairs(self.pending) do
        -- File paths remain local; only the paired server receives EPUB bytes.
        local payload = {}
        for _, name in ipairs({"reader","content_id","epub_md5","locator","passage","sequence","source_time"}) do payload[name] = observation[name] end
        local ebook_path = observation.ebook_path
        if not ebook_path and self.epub_md5 == observation.epub_md5 and self.ui.document
            and self.ui.document.file:match("([^/]+)$") == observation.content_id then
            ebook_path = self.ui.document.file
        end
        ordered[#ordered+1] = {key=key, observation=payload, ebook_path=ebook_path}
    end
    table.sort(ordered, function(a,b)
        if a.observation.source_time == b.observation.source_time then
            return (a.observation.sequence or 0) > (b.observation.sequence or 0)
        end
        return a.observation.source_time > b.observation.source_time
    end)
    if #ordered == 0 then
        if interactive then
            UIManager:show(InfoMessage:new{ text = self.last_sync_message or (self.last_locator and "Reading position already sent. Audio handoff is scheduled for morning." or "Open a supported EPUB and select a passage to send.") })
        end
        return
    end
    -- Keep IPC below a pipe buffer, and give the latest reading priority.
    while #ordered > 32 do ordered[#ordered] = nil end
    local may_upload = false
    for _, item in ipairs(ordered) do if item.ebook_path then may_upload = true; break end end
    local server, token = self.config.server, self.config.token
    local resolve = self.discovery.resolve
    local started, message = self.worker.start(self, function()
        return self.worker.send(server, token, ordered, resolve)
    end, function(result)
        if self.closed or self.config.server ~= server or self.config.token ~= token then return end
        local address_changed = result.server and result.server ~= server
        if address_changed then
            self.config.server = result.server
            self.settings:saveSetting("endpoint_cache", {key=self.endpoint_key,server=result.server})
            self.dirty = true
        end
        local count = 0
        for _, key in ipairs(result.sent or {}) do
            if self.pending[key] then self.pending[key] = nil; self.dirty = true end
            count = count + 1
        end
        if address_changed then self:persist()
        elseif self.dirty then self:deferSave() end
        if count > 0 then
            self.last_sync_message = result.preparation_message or "Reading position sent. Audio handoff is scheduled for morning."
        end
        if self.interactive_pending then
            if not next(self.pending) then
                UIManager:show(InfoMessage:new{ text = self.last_sync_message })
                self.interactive_pending = nil
            elseif result.latest_sent and result.message then
                UIManager:show(InfoMessage:new{ text = self.last_sync_message .. "\nAnother saved book is still queued: " .. result.message })
                self.interactive_pending = nil
            elseif count == 0 or result.message then
                UIManager:show(InfoMessage:new{ text = "Position still queued. " .. (result.message or "Try again on home Wi-Fi.") })
                self.interactive_pending = nil
            end
        end
        if count > 0 and next(self.pending) then UIManager:scheduleIn(1, self.flush_task) end
    end, may_upload)
    if started == nil and interactive then
        UIManager:show(InfoMessage:new{ text = message })
        self.interactive_pending = nil
    elseif started == false then
        UIManager:unschedule(self.flush_task)
        UIManager:scheduleIn(1, self.flush_task)
    end
end


function KoboSync:onPageUpdate()
    self.capture_pending = true
    self.pending_event_time = os.time()
    UIManager:unschedule(self.capture_task)
    UIManager:scheduleIn(2, self.capture_task)
end

function KoboSync:onReaderReady()
    self:onPageUpdate()
end

function KoboSync:onSuspend()
    if self.closed then return end
    UIManager:unschedule(self.capture_task)
    self.capture_pending = false
    self:capture(nil, false, false, self.pending_event_time)
    self.pending_event_time = nil
    self:persist()
    self:flush()
end

function KoboSync:onResume()
    UIManager:scheduleIn(1, self.flush_task)
end

function KoboSync:onNetworkConnected()
    UIManager:unschedule(self.flush_task)
    UIManager:scheduleIn(1, self.flush_task)
end

function KoboSync:onCloseDocument()
    UIManager:unschedule(self.capture_task)
    UIManager:unschedule(self.flush_task)
    self.capture_pending = false
    self:capture(nil, false, false, self.pending_event_time)
    self:persist()
    UIManager:unschedule(self.save_task)
    self.closed = true
    self.worker.cancel(self)
end

return KoboSync
