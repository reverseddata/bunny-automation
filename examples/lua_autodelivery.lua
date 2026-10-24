--[[
  Lua side of autodelivery: runs on your trade account in the private server.

  It asks bunny for orders whose buyer confirmed their Roblox name (flow state
  "ready"), waits for that player, runs YOUR trade function, and reports back:
    traded  -> POST /orders/{id}/complete   (marked delivered + "Thank you!")
    failed  -> POST /orders/{id}/problem    (one "something went wrong" line)

  Needs an executor with an HTTP function (request / http_request / syn.request).
  bunny runs on the same machine at 127.0.0.1:8787. Fill in API_KEY and
  tradeItems() below; how you trade is up to your own script.
]]

local BUNNY = "http://127.0.0.1:8787"
local API_KEY = "paste-your-BUNNY_API_KEY"
local POLL_SECONDS = 10
local WAIT_FOR_PLAYER = 300 -- seconds before giving up on a buyer

local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local http = request or http_request or (syn and syn.request)

local function call(method, path, body)
    local res = http({
        Url = BUNNY .. path,
        Method = method,
        Headers = { ["X-API-Key"] = API_KEY, ["Content-Type"] = "application/json" },
        Body = body and HttpService:JSONEncode(body) or nil,
    })
    if res.StatusCode >= 300 then
        warn("bunny " .. method .. " " .. path .. ": " .. tostring(res.StatusCode))
        return nil
    end
    return HttpService:JSONDecode(res.Body)
end

-- Replace with your own trading. Return true when everything was handed over.
local function tradeItems(player, items)
    -- items: { { name = "Harvester", quantity = 1 }, ... }
    return false
end

local busy = {} -- order ids being worked on (or finished) in this session

local function deliver(order)
    local name = order.flow.roblox_name
    local deadline = os.clock() + WAIT_FOR_PLAYER
    local player = Players:FindFirstChild(name)
    while not player and os.clock() < deadline do
        task.wait(2)
        player = Players:FindFirstChild(name)
    end
    if not player then
        print("buyer " .. name .. " never joined, order " .. order.id .. " left for you")
        return
    end
    local ok, traded = pcall(tradeItems, player, order.items)
    if ok and traded then
        call("POST", "/orders/" .. order.id .. "/complete", { thanks = true, archive = true })
    else
        call("POST", "/orders/" .. order.id .. "/problem")
    end
end

while true do
    local data = call("GET", "/orders?state=open")
    for _, order in ipairs((data and data.orders) or {}) do
        if order.flow and order.flow.state == "ready" and not busy[order.id] then
            busy[order.id] = true
            task.spawn(deliver, order)
        end
    end
    task.wait(POLL_SECONDS)
end
