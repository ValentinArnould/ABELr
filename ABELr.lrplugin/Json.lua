--[[
    Json.lua — compact JSON encoder/decoder for Lua 5.1 (Lr SDK).

    Lua has no native JSON lib. Minimal module covering the project's needs:
    objects, arrays, strings (full escaping), numbers, bool, null.

    Arrays vs objects: Lua doesn't distinguish an empty array table from an empty
    object table. Use Json.array(t) to force serialization as a JSON array, even when empty.
        local arr = Json.array({})            -- → "[]"
        local arr = Json.array({ a, b, c })   -- → "[...]"

    Null value: use Json.null (sentinel) to produce `null`.
]]

local LrTasks = import 'LrTasks'

local Json = {}

-- Null sentinel (distinct from nil so it doesn't disappear from tables).
Json.null = setmetatable({}, { __tostring = function() return 'null' end })

local ARRAY_MT = {}  -- marker metatable "this table is a JSON array"

function Json.array(t)
    return setmetatable(t or {}, ARRAY_MT)
end

local function isArray(t)
    if getmetatable(t) == ARRAY_MT then return true end
    -- Heuristic: non-empty sequence with keys 1..n.
    local n = 0
    for k in pairs(t) do
        if type(k) ~= 'number' then return false end
        n = n + 1
    end
    return n > 0 and n == #t
end

-- ------------------------------------------------------------------ --
-- Encoding
-- ------------------------------------------------------------------ --
local ESCAPES = {
    ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
    ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
}

local function encodeString(s)
    local out = s:gsub('[%z\1-\31\\"]', function(c)
        local e = ESCAPES[c]
        if e then return e end
        return string.format('\\u%04x', string.byte(c))
    end)
    return '"' .. out .. '"'
end

local function encodeNumber(n)
    if n ~= n or n == math.huge or n == -math.huge then
        return 'null'  -- NaN/Inf are not valid JSON
    end
    if math.floor(n) == n and math.abs(n) < 1e15 then
        return string.format('%d', n)
    end
    return string.format('%.10g', n)
end

local encodeValue  -- forward

local function encodeTable(t, parts)
    if t == Json.null then
        parts[#parts + 1] = 'null'
        return
    end
    if isArray(t) then
        parts[#parts + 1] = '['
        for i = 1, #t do
            if i > 1 then parts[#parts + 1] = ',' end
            encodeValue(t[i], parts)
        end
        parts[#parts + 1] = ']'
    else
        parts[#parts + 1] = '{'
        local first = true
        for k, v in pairs(t) do
            if not first then parts[#parts + 1] = ',' end
            first = false
            parts[#parts + 1] = encodeString(tostring(k))
            parts[#parts + 1] = ':'
            encodeValue(v, parts)
        end
        parts[#parts + 1] = '}'
    end
end

encodeValue = function(v, parts)
    local tv = type(v)
    if v == Json.null or v == nil then
        parts[#parts + 1] = 'null'
    elseif tv == 'boolean' then
        parts[#parts + 1] = v and 'true' or 'false'
    elseif tv == 'number' then
        parts[#parts + 1] = encodeNumber(v)
    elseif tv == 'string' then
        parts[#parts + 1] = encodeString(v)
    elseif tv == 'table' then
        encodeTable(v, parts)
    else
        error('Json.encode: unsupported type: ' .. tv)
    end
end

function Json.encode(value)
    local parts = {}
    encodeValue(value, parts)
    return table.concat(parts)
end

-- ------------------------------------------------------------------ --
-- Decoding (recursive descent)
-- ------------------------------------------------------------------ --
local decodeValue  -- forward

local function skipWhitespace(s, i)
    local _, j = s:find('^[ \t\r\n]*', i)
    return (j or i - 1) + 1
end

local UNESCAPES = {
    ['"'] = '"', ['\\'] = '\\', ['/'] = '/', ['b'] = '\b',
    ['f'] = '\f', ['n'] = '\n', ['r'] = '\r', ['t'] = '\t',
}

local function decodeString(s, i)
    -- s[i] == '"'
    i = i + 1
    local buf = {}
    while i <= #s do
        local c = s:sub(i, i)
        if c == '"' then
            return table.concat(buf), i + 1
        elseif c == '\\' then
            local nxt = s:sub(i + 1, i + 1)
            if nxt == 'u' then
                local hex = s:sub(i + 2, i + 5)
                local code = tonumber(hex, 16)
                if not code then error('Json: invalid \\u at ' .. i) end
                i = i + 6
                -- Surrogate pair (Fable 5 review L-08): \uD800-DBFF followed by
                -- \uDC00-DFFF → astral code point, encoded as 4-byte UTF-8.
                if code >= 0xD800 and code <= 0xDBFF and s:sub(i, i + 1) == '\\u' then
                    local lo = tonumber(s:sub(i + 2, i + 5), 16)
                    if lo and lo >= 0xDC00 and lo <= 0xDFFF then
                        code = 0x10000 + (code - 0xD800) * 0x400 + (lo - 0xDC00)
                        i = i + 6
                    end
                end
                -- Minimal UTF-8 encoding
                if code < 0x80 then
                    buf[#buf + 1] = string.char(code)
                elseif code < 0x800 then
                    buf[#buf + 1] = string.char(
                        0xC0 + math.floor(code / 0x40),
                        0x80 + (code % 0x40))
                elseif code < 0x10000 then
                    buf[#buf + 1] = string.char(
                        0xE0 + math.floor(code / 0x1000),
                        0x80 + (math.floor(code / 0x40) % 0x40),
                        0x80 + (code % 0x40))
                else
                    buf[#buf + 1] = string.char(
                        0xF0 + math.floor(code / 0x40000),
                        0x80 + (math.floor(code / 0x1000) % 0x40),
                        0x80 + (math.floor(code / 0x40) % 0x40),
                        0x80 + (code % 0x40))
                end
            else
                buf[#buf + 1] = UNESCAPES[nxt] or nxt
                i = i + 2
            end
        else
            buf[#buf + 1] = c
            i = i + 1
        end
    end
    error('Json: unterminated string')
end

local function decodeNumber(s, i)
    local _, j = s:find('^%-?%d+%.?%d*[eE]?[%+%-]?%d*', i)
    local numStr = s:sub(i, j)
    return tonumber(numStr), j + 1
end

local function decodeArray(s, i)
    local arr = Json.array({})
    i = skipWhitespace(s, i + 1)
    if s:sub(i, i) == ']' then return arr, i + 1 end
    while true do
        local val
        val, i = decodeValue(s, i)
        arr[#arr + 1] = val
        i = skipWhitespace(s, i)
        local c = s:sub(i, i)
        if c == ']' then return arr, i + 1 end
        if c ~= ',' then error('Json: expected , or ] at ' .. i) end
        i = skipWhitespace(s, i + 1)
    end
end

local function decodeObject(s, i)
    local obj = {}
    i = skipWhitespace(s, i + 1)
    if s:sub(i, i) == '}' then return obj, i + 1 end
    while true do
        if s:sub(i, i) ~= '"' then error('Json: expected key at ' .. i) end
        local key
        key, i = decodeString(s, i)
        i = skipWhitespace(s, i)
        if s:sub(i, i) ~= ':' then error('Json: expected : at ' .. i) end
        local val
        val, i = decodeValue(s, skipWhitespace(s, i + 1))
        obj[key] = val
        i = skipWhitespace(s, i)
        local c = s:sub(i, i)
        if c == '}' then return obj, i + 1 end
        if c ~= ',' then error('Json: expected , or } at ' .. i) end
        i = skipWhitespace(s, i + 1)
    end
end

decodeValue = function(s, i)
    i = skipWhitespace(s, i)
    local c = s:sub(i, i)
    if c == '"' then return decodeString(s, i)
    elseif c == '{' then return decodeObject(s, i)
    elseif c == '[' then return decodeArray(s, i)
    elseif c == '-' or c:match('%d') then return decodeNumber(s, i)
    elseif s:sub(i, i + 3) == 'true' then return true, i + 4
    elseif s:sub(i, i + 4) == 'false' then return false, i + 5
    elseif s:sub(i, i + 3) == 'null' then return Json.null, i + 4
    end
    error('Json: unexpected character at ' .. i .. ': ' .. tostring(c))
end

-- Returns value, err. err is non-nil on failure.
function Json.decode(str)
    if type(str) ~= 'string' or str == '' then
        return nil, 'Json.decode: empty input'
    end
    local ok, value = LrTasks.pcall(function()
        local v = decodeValue(str, 1)
        return v
    end)
    if not ok then return nil, value end
    return value
end

return Json
