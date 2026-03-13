@echo off
setlocal

echo.
echo  auto-eda — local dev mode
echo  =========================
echo  App:    http://localhost:8000
echo  Tunnel: starting...
echo.

cd /d "%~dp0"

:: ── Redis ────────────────────────────────────────────────────────────────────
redis-cli ping >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Redis is not running.
    echo         Start it with:  redis-server
    pause
    exit /b 1
)
echo  [ok] Redis

:: ── cloudflared ──────────────────────────────────────────────────────────────
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo  [..] cloudflared not found — installing via Scoop...
    scoop install cloudflare-tunnel
)
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo  [WARN] cloudflared still not found — tunnel will be skipped.
    echo         Install manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    set TUNNEL=0
) else (
    set TUNNEL=1
)

:: ── Worker ───────────────────────────────────────────────────────────────────
echo  [1] Starting worker...
start "auto-eda worker" cmd /k "cd /d "%~dp0" && python backend/worker.py"

:: ── Tunnel ───────────────────────────────────────────────────────────────────
if "%TUNNEL%"=="1" (
    echo  [2] Starting Cloudflare tunnel...
    start "auto-eda tunnel" cmd /k "cloudflared tunnel --no-autoupdate --url http://localhost:8000"
    echo      ^> Tunnel URL will appear in the "auto-eda tunnel" window.
)

:: ── App ──────────────────────────────────────────────────────────────────────
echo  [3] Starting app ^(auto-reload on^)...
echo.
python run_server.py

endlocal
