#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Rulerything — Post-session-start hook for Claude Code
#
# Automatically starts the rule server in the background when a Claude Code
# session begins, so the first rule query doesn't have a cold-start delay.
#
# Installation:
#   In Claude Code settings (settings.json), add:
#     "hooks": {
#       "PostSessionStart": "bash /path/to/rulerything-skill/hooks/post-session-start.sh"
#     }
#
# Environment variables:
#   RULERYTHING_DIR   Path to rulerything rule system (default: ../rulerything)
#   RULERYTHING_PORT  API port (default: 8001)
# ─────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RULERYTHING_DIR="${RULERYTHING_DIR:-"$(dirname "$SCRIPT_DIR")/rulerything"}"
RULERYTHING_PORT="${RULERYTHING_PORT:-8001}"

# Check if server is already running
if command -v curl &>/dev/null; then
    if curl -sf "http://127.0.0.1:$RULERYTHING_PORT/health" >/dev/null 2>&1; then
        exit 0  # Already running
    fi
fi

# Start the server in background
if [ -f "$RULERYTHING_DIR/main.py" ]; then
    cd "$RULERYTHING_DIR"
    nohup python -m uvicorn main:app \
        --host 127.0.0.1 \
        --port "$RULERYTHING_PORT" \
        --log-level warning \
        >/dev/null 2>&1 &
    disown
fi
