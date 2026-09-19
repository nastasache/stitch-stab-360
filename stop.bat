@echo off
setlocal
cd /d "%~dp0"

set PYTHONDONTWRITEBYTECODE=1

echo =======================================================
echo   StitchStab 360 - Stopping Server...
echo =======================================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -B utils\stop_server.py
) else (
    python -B utils\stop_server.py
)

echo.
pause
