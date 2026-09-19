#!/usr/bin/env bash
cd "$(dirname "$0")"

export PYTHONDONTWRITEBYTECODE=1

echo "========================================================"
echo "  StitchStab 360 - Video Stitcher & Stabilizer"
echo "  Starting Python FastAPI / Uvicorn Server..."
echo "  URL: http://127.0.0.1:8000"
echo "========================================================"
echo ""

# Ensure venv exists
if [ ! -f "venv/bin/python3" ] && [ ! -f "venv/bin/python" ]; then
    echo "[INFO] Python virtual environment not found. Creating 'venv'..."
    python3.12 -B -m venv venv 2>/dev/null || python3.11 -B -m venv venv 2>/dev/null || python3 -B -m venv venv || python -B -m venv venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment. Ensure python3-venv is installed."
        exit 1
    fi
    echo "[INFO] Installing dependencies from requirements.txt..."
    if [ -f "venv/bin/python3" ]; then
        venv/bin/python3 -m pip install --upgrade pip
        venv/bin/python3 -m pip install -r requirements.txt
    else
        venv/bin/python -m pip install --upgrade pip
        venv/bin/python -m pip install -r requirements.txt
    fi
fi

PYTHON_BIN="venv/bin/python3"
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="venv/bin/python"
fi

# Attempt to open browser if in desktop environment
if which xdg-open > /dev/null 2>&1; then
    xdg-open "http://127.0.0.1:8000" > /dev/null 2>&1 &
elif which open > /dev/null 2>&1; then
    open "http://127.0.0.1:8000" > /dev/null 2>&1 &
fi

"$PYTHON_BIN" -B -m uvicorn server:app --host 127.0.0.1 --port 8000
