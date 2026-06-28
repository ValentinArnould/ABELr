--[[
    Json.lua — encodeur / décodeur JSON compact pour Lua 5.1 (SDK Lr).

    Lua n'a pas de lib JSON native. Module minimal couvrant les besoins du projet :
    objets, tableaux, strings (échappement complet), nombres, bool, null.

    Tableaux vs objets : Lua ne distingue pas une table vide tableau d'une table vide
    objet. Utiliser Json.array(t) pour forcer la sérialisation en tableau JSON, même vide.
        local arr = Json.array({})            -- → "[]"
        local arr = Json.array({ a, b, c })   -- → "[...]"

    Valeur null : utiliser Json.null (sentinelle) pour produire `null`.
]]

local LrTasks = import 'LrTasks'

local Json = {}

-- Sentinelle null (distincte de nil pour ne pas disparaître des tables).
Json.null = setmetatable({}, { __tostring = function() return 'null' end })

local ARRAY_MT = {}  -- métatable marqueur "ce table est un tableau JSON"

function Json.array(t)
    return setmetatable(t or {}, ARRAY_MT)
end

local function isArray(t)
    if getmetatable(t) == ARRAY_MT then return true end
    -- Heuristique : séquence non vide avec clés 1..n.
    local n = 0
    for k in pairs(t) do
        if type(k) ~= 'number' then return false end
        n = n + 1
    end
    return n > 0 and n == #t
end

-- ------------------------------------------------------------------ --
-- Encodage
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
        return 'null'  -- NaN/Inf non valides en JSON
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
        error('Json.encode : type non supporté : ' .. tv)
    end
end

function Json.encode(value)
    local parts = {}
    encodeValue(value, parts)
    return table.concat(parts)
end

-- ------------------------------------------------------------------ --
-- Décodage (descente récursive)
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
                if not code then error('Json: \\u invalide à ' .. i) end
                -- UTF-8 encodage minimal (BMP)
                if code < 0x80 then
                    buf[#buf + 1] = string.char(code)
                elseif code < 0x800 then
                    buf[#buf + 1] = string.char(
                        0xC0 + math.floor(code / 0x40),
                        0x80 + (code % 0x40))
                else
                    buf[#buf + 1] = string.char(
                        0xE0 + math.floor(code / 0x1000),
                        0x80 + (math.floor(code / 0x40) % 0x40),
                        0x80 + (code % 0x40))
                end
                i = i + 6
            else
                buf[#buf + 1] = UNESCAPES[nxt] or nxt
                i = i + 2
            end
        else
            buf[#buf + 1] = c
            i = i + 1
        end
    end
    error('Json: string non terminée')
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
        if c ~= ',' then error('Json: attendu , ou ] à ' .. i) end
        i = skipWhitespace(s, i + 1)
    end
end

local function decodeObject(s, i)
    local obj = {}
    i = skipWhitespace(s, i + 1)
    if s:sub(i, i) == '}' then return obj, i + 1 end
    while true do
        if s:sub(i, i) ~= '"' then error('Json: clé attendue à ' .. i) end
        local key
        key, i = decodeString(s, i)
        i = skipWhitespace(s, i)
        if s:sub(i, i) ~= ':' then error('Json: : attendu à ' .. i) end
        local val
        val, i = decodeValue(s, skipWhitespace(s, i + 1))
        obj[key] = val
        i = skipWhitespace(s, i)
        local c = s:sub(i, i)
        if c == '}' then return obj, i + 1 end
        if c ~= ',' then error('Json: attendu , ou } à ' .. i) end
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
    error('Json: caractère inattendu à ' .. i .. ' : ' .. tostring(c))
end

-- Retourne value, err. err non nil en cas d'échec.
function Json.decode(str)
    if type(str) ~= 'string' or str == '' then
        return nil, 'Json.decode: entrée vide'
    end
    local ok, value = LrTasks.pcall(function()
        local v = decodeValue(str, 1)
        return v
    end)
    if not ok then return nil, value end
    return value
end

return Json
