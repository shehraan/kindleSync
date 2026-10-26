-- Same-subnet recovery for a paired LAN server. Probes never send the token.
local Discovery = {}
local prefix = "kobosync-discovery-v1\n"

function Discovery.private_origin(origin)
    if type(origin) ~= "string" then return end
    origin=origin:gsub("/$","")
    local a,b,c,d,port = origin:match("^http://(%d+)%.(%d+)%.(%d+)%.(%d+):(%d+)$")
    if not a then
        a,b,c,d=origin:match("^http://(%d+)%.(%d+)%.(%d+)%.(%d+)$")
        port="80"
    end
    if not a then return end
    local parts={a,b,c,d}
    for _,part in ipairs(parts) do
        if #part > 3 or tostring(tonumber(part)) ~= part or tonumber(part)>255 then return end
    end
    a,b,c,d,port=tonumber(a),tonumber(b),tonumber(c),tonumber(d),tonumber(port)
    if port<1 or port>65535 or not (a==10 or (a==172 and b>=16 and b<=31) or (a==192 and b==168)) then return end
    return {host=table.concat(parts,"."), subnet=table.concat({parts[1],parts[2],parts[3]},".")..".", last=d, port=port}
end

function Discovery.verify(body, nonce, token)
    if type(body)~="string" or #body>1024 then return false end
    local received,identity,proof=body:match("^kobosync%-discovery%-v1\n([a-f0-9]+)\n([a-f0-9]+)\n([a-f0-9]+)\n$")
    if received~=nonce or #received~=32 or #identity~=32 or #proof~=64 then return false end
    local sha2=require("ffi/sha2")
    local expected=sha2.hmac(sha2.sha256,token,prefix..nonce.."\n"..identity)
    local equal=true
    for i=1,64 do if proof:byte(i)~=expected:byte(i) then equal=false end end
    return equal
end

local function nonce()
    local file=assert(io.open("/dev/urandom","rb"))
    local bytes=file:read(16); file:close()
    assert(bytes and #bytes==16,"Random source unavailable")
    return (bytes:gsub(".",function(c) return string.format("%02x",c:byte()) end))
end

local function scan(address, hosts, token, challenge, total)
    local socket=require("socket")
    local active, next_host={},1
    local deadline=socket.gettime()+total
    local function close(client)
        active[client]=nil
        client:close()
    end
    local function cleanup() for client in pairs(active) do client:close() end end
    local ok,result=pcall(function()
        while socket.gettime()<deadline do
            local now=socket.gettime()
            local count=0
            for client,state in pairs(active) do
                if now>=state.deadline then close(client) else count=count+1 end
            end
            while count<16 and next_host<=#hosts do
                local host=hosts[next_host]; next_host=next_host+1
                local client=socket.tcp()
                if client then
                    client:settimeout(0)
                    local connected,err=client:connect(host,address.port)
                    if connected or err=="timeout" or err=="Operation already in progress" then
                        active[client]={host=host,deadline=now+0.6,offset=1,data="",writing=true,
                            request="GET /v1/discovery?nonce="..challenge.." HTTP/1.0\r\nHost: "..host..":"..address.port.."\r\nConnection: close\r\n\r\n"}
                        count=count+1
                    else client:close() end
                end
            end
            if count==0 and next_host>#hosts then return end
            local readers,writers={},{}
            for client,state in pairs(active) do
                local list=state.writing and writers or readers
                list[#list+1]=client
            end
            local ready_read,ready_write=socket.select(readers,writers,math.min(0.05,math.max(0,deadline-socket.gettime())))
            for _,client in ipairs(ready_write) do
                local state=active[client]
                if state then
                    if not client:getpeername() then close(client)
                    else
                        local sent,err,partial=client:send(state.request,state.offset)
                        state.offset=(sent or partial or state.offset-1)+1
                        if state.offset>#state.request then state.writing=false
                        elseif err and err~="timeout" then close(client) end
                    end
                end
            end
            for _,client in ipairs(ready_read) do
                local state=active[client]
                if state then
                    local chunk,err,partial=client:receive(1024)
                    state.data=state.data..(chunk or partial or "")
                    if #state.data>4096 then close(client)
                    else
                        local boundary=state.data:find("\r\n\r\n",1,true)
                        if boundary then
                            local header=state.data:sub(1,boundary+1)
                            local body=state.data:sub(boundary+4)
                            local size=header:lower():match("\r\ncontent%-length:%s*(%d+)\r\n")
                            if not header:match("^HTTP/1%.[01] 200 ") or not size or tonumber(size)>1024 or header:lower():find("\r\ntransfer-encoding:",1,true) then close(client)
                            elseif #body>=tonumber(size) then
                                if #body==tonumber(size) and Discovery.verify(body,challenge,token) then
                                    return "http://"..state.host..":"..address.port
                                end
                                close(client)
                            elseif err and err~="timeout" then close(client) end
                        elseif err and err~="timeout" then close(client) end
                    end
                end
            end
        end
    end)
    cleanup()
    if ok then return result end
end

function Discovery.resolve(origin, token)
    local address=Discovery.private_origin(origin)
    if not address then return origin end
    if type(token)~="string" or token=="" then return end
    local ok,challenge=pcall(nonce)
    if not ok then return end
    local current=scan(address,{address.host},token,challenge,0.7)
    if current then return current end
    local hosts={}
    for last=1,254 do
        if last~=address.last then hosts[#hosts+1]=address.subnet..last end
    end
    return scan(address,hosts,token,challenge,10)
end

return Discovery
