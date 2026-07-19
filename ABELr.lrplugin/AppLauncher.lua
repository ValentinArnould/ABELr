--[[
    AppLauncher.lua — starts / relaunches the Python App from the plugin.

    Start: launches `launch.ps1` (detached process), current folder = the plugin
    (self-sufficient, embeds `app/`), then waits for /health to respond. On the very
    first launch (no `.venv` under the plugin), `launch.ps1` first chains
    `bootstrap.ps1` (creates the venv + installs dependencies, ~250 MB to 2.5 GB
    depending on GPU/CPU) — so the wait timeout is much longer in that case.
    Relaunch: POST /shutdown to the existing App, waits for it to shut down, then restarts.

    All functions assume they run inside an async task (sleep + HTTP).
]]

local LrTasks     = import 'LrTasks'
local LrPathUtils = import 'LrPathUtils'
local LrFileUtils = import 'LrFileUtils'

local HttpClient = require 'HttpClient'
local Utils      = require 'Utils'

local AppLauncher = {}

-- /health wait timeouts (seconds): normal launch vs. 1st run (bootstrap
-- downloads dependencies — torch CUDA/CPU ~250 MB-2.5 GB, can take several
-- minutes depending on the connection).
local HEALTH_TIMEOUT_NORMAL     = 12
local HEALTH_TIMEOUT_FIRST_RUN  = 900

-- True if the venv hasn't been built under the plugin yet (first launch).
local function isFirstRun()
    local venvPython = LrPathUtils.child(
        LrPathUtils.child(LrPathUtils.child(Utils.appDir(), '.venv'), 'Scripts'),
        'python.exe')
    return not LrFileUtils.exists(venvPython)
end

-- Builds the launch PowerShell command (Windows).
-- Launches launch.ps1 (inside the plugin) in a detached PowerShell window.
local function buildLaunchCommand()
    local script = LrPathUtils.child(Utils.projectRoot(), 'launch.ps1')
    return string.format(
        'cmd /c start "ABELr" powershell -ExecutionPolicy Bypass -File "%s"',
        script)
end

-- Waits for /health to respond (or fail) matching `wantAlive`. Returns true if reached.
local function waitForHealth(wantAlive, maxSeconds)
    local elapsed = 0
    local step = 0.4
    while elapsed < maxSeconds do
        if HttpClient.isAlive() == wantAlive then
            return true
        end
        LrTasks.sleep(step)
        elapsed = elapsed + step
    end
    return HttpClient.isAlive() == wantAlive
end

-- Launches the App process. Returns (ok, message).
function AppLauncher.start()
    if HttpClient.isAlive() then
        return true, 'Application already running.'
    end
    local firstRun = isFirstRun()
    local cmd = buildLaunchCommand()
    Utils.logf('Launching App: %s (1st run = %s)', cmd, tostring(firstRun))
    LrTasks.execute(cmd)   -- detached, returns control immediately
    local timeout = firstRun and HEALTH_TIMEOUT_FIRST_RUN or HEALTH_TIMEOUT_NORMAL
    if waitForHealth(true, timeout) then
        return true, 'Application started and connected.'
    end
    if firstRun then
        return false,
            "Initial installation in progress (venv + dependencies) — see the open " ..
            "PowerShell window. Relaunch \"Start\" once the installation is finished " ..
            "if /health still doesn't respond."
    end
    return false, 'Launch completed but /health is not responding (check the Python console).'
end

-- Requests the App to stop via /shutdown. Returns true if the App shut down.
function AppLauncher.stop()
    if not HttpClient.isAlive() then
        return true
    end
    HttpClient.get('/health', 1)  -- wakes up the socket if needed
    -- /shutdown is a POST; done via postJson (empty body).
    LrTasks.pcall(function() HttpClient.postJson('/shutdown', {}, 3) end)
    return waitForHealth(false, 6)
end

-- Relaunch: stops the existing instance then starts a fresh one.
function AppLauncher.relaunch()
    AppLauncher.stop()
    LrTasks.sleep(0.5)
    return AppLauncher.start()
end

return AppLauncher
