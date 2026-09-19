#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f "venv/bin/python" ]; then
    venv/bin/python -B utils/stop_server.py
else
    python3 -B utils/stop_server.py
fi
