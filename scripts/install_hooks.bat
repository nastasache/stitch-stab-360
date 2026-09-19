@echo off
setlocal
cd /d "%~dp0\.."

echo [*] Installing Git pre-commit hook...

if not exist ".git" (
    echo [!] .git directory not found.
    exit /b 1
)

if not exist ".git\hooks" mkdir ".git\hooks"

(
echo #!/bin/sh
echo export PYTHONDONTWRITEBYTECODE=1
echo echo "==================================================="
echo echo "[pre-commit] Running StitchStab 360 test suite..."
echo echo "==================================================="
echo python -B -m unittest discover -s tests -p "test_*.py"
echo RESULT=$?
echo if [ $RESULT -ne 0 ]; then
echo     echo ""
echo     echo "❌ [pre-commit] Tests failed (exit code $RESULT)! Commit aborted."
echo     echo "Run './run_tests.bat' to inspect details."
echo     exit 1
echo fi
echo echo "✅ [pre-commit] All tests passed successfully."
echo exit 0
) > .git\hooks\pre-commit

echo [OK] Pre-commit hook installed successfully at .git/hooks/pre-commit
endlocal
