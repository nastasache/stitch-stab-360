@echo off
setlocal
cd /d "%~dp0"

set "PYTHONDONTWRITEBYTECODE=1"

echo ===================================================
echo [TEST RUNNER] Running StitchStab 360 Test Suite
echo ===================================================

:: Try running with pytest first, otherwise fallback to unittest
python -B -c "import pytest" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [*] Running tests with pytest...
    python -B -m pytest tests/ -v
) else (
    echo [*] pytest not installed, falling back to python unittest discover...
    python -B -m unittest discover -s tests -p "test_*.py" -v
)

if %ERRORLEVEL% NEQ 0 (
    echo [!] Test Suite FAILED with exit code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo [OK] All tests completed successfully.
endlocal
