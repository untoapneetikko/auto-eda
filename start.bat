@echo off
echo.
echo  auto-eda startup
echo  ================
echo.

REM Check .env has been filled
findstr /C:"paste_your_token_here" .env >/dev/null 2>&1
if %errorlevel%==0 (
    echo  [!] ERROR: You haven't set your Cloudflare tunnel token yet.
    echo      Edit .env and replace paste_your_token_here with your real token.
    echo      Get it from: dash.cloudflare.com - Zero Trust - Networks - Tunnels
    echo.
    pause
    exit /b 1
)

echo  [1] Building Docker images (first time takes ~3 minutes)...
docker compose build

echo.
echo  [2] Starting all services...
docker compose up -d

echo.
echo  [3] Status:
docker compose ps

echo.
echo  auto-eda is running!
echo  Local:   http://localhost:8000
echo  Tunnel:  Check Cloudflare dashboard for your HTTPS URL
echo.
echo  Useful commands:
echo    docker compose logs -f          (live logs)
echo    docker compose logs -f app      (app logs only)
echo    docker compose down             (stop everything)
echo    docker compose up -d --build    (rebuild after code changes)
echo.
pause
