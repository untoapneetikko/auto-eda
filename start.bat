@echo off
echo.
echo  auto-eda startup
echo  ================
echo.

echo  [1] Building Docker images (first time takes ~3 minutes)...
docker compose build

echo.
echo  [2] Starting all services...
docker compose up -d

echo.
echo  [3] Status:
docker compose ps

echo.
echo  [4] Waiting for tunnel URL (up to 15 seconds)...
timeout /t 10 /nobreak >nul
docker compose logs tunnel 2>&1 | findstr /C:"trycloudflare.com"

echo.
echo  auto-eda is running!
echo  Local:   http://localhost:8000
echo  Tunnel:  see URL above (also: docker compose logs tunnel)
echo.
echo  Useful commands:
echo    docker compose logs tunnel         (get current HTTPS URL)
echo    docker compose logs -f             (live logs)
echo    docker compose logs -f app         (app logs only)
echo    docker compose down                (stop everything)
echo    docker compose up -d --build       (rebuild after code changes)
echo.
pause
