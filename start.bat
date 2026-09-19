@echo off
setlocal
cd /d "%~dp0"

set PYTHONDONTWRITEBYTECODE=1

if exist "%LOCALAPPDATA%\Programs\ExifTool" set "PATH=%LOCALAPPDATA%\Programs\ExifTool;%PATH%"
if exist "%ProgramFiles%\ExifTool" set "PATH=%ProgramFiles%\ExifTool;%PATH%"
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages" (
    for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do (
        if exist "%%D\ffmpeg-*-full_build\bin" set "PATH=%%D\ffmpeg-*-full_build\bin;%PATH%"
    )
)
if exist "C:\ffmpeg\bin" set "PATH=C:\ffmpeg\bin;%PATH%"

echo ========================================================
echo   StitchStab 360 - Video Stitcher ^& Stabilizer
echo   Starting Python FastAPI / Uvicorn Server...
echo   URL: http://127.0.0.1:8000
echo ========================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [INFO] Python virtual environment not found. Creating 'venv'...
    py -3.12 -B -m venv venv 2>nul || py -3.11 -B -m venv venv 2>nul || python -B -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment. Ensure Python is installed.
        pause
        exit /b 1
    )
    echo [INFO] Installing dependencies from requirements.txt...
    venv\Scripts\python.exe -m pip install --upgrade pip
    venv\Scripts\python.exe -m pip install -r requirements.txt
)

start http://127.0.0.1:8000
venv\Scripts\python.exe -B -m uvicorn server:app --host 127.0.0.1 --port 8000

pause
