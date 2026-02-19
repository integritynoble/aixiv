#!/bin/bash
# AI Scientist backend launcher
cd /home/spiritai/aixiv/pwm_aixiv/backend

# Load environment variables from .env
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

exec python3 -m uvicorn app:app --host 127.0.0.1 --port 8501 --workers 1
