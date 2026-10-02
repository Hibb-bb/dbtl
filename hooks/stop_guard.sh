#!/bin/sh
# Stop hook: refuse to end the session mid-round. Exit 2 blocks the stop and feeds stderr back to the agent.
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$ROOT" || exit 0
if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; else PY="python3"; fi
"$PY" "$ROOT/scripts/status.py" --root "$ROOT" --stop-check
