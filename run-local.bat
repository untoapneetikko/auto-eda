@echo off
setlocal

echo.
echo  auto-eda — local dev mode
echo  =========================
echo  App:    http://localhost:8000
echo  No tunnel, no Docker needed.
echo.

cd /d "%~dp0"

:: Check Redis
redis-cli ping >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Redis is not running.
    echo         Start it with:  redis-server
    echo         Or via Scoop:   scoop install redis ^& redis-server
    pause
    exit /b 1
)
echo  [ok] Redis is running.

:: Start the worker in a new window
echo  [1] Starting worker...
start "auto-eda worker" cmd /k "cd /d "%~dp0" && python backend/worker.py"

:: Small delay so worker window is visible before app starts
timeout /t 1 /nobreak >nul

:: Start the app with auto-reload
echo  [2] Starting app (auto-reload on)...
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir backend --reload-dir agents

endlocal
