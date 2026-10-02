#!/usr/bin/env python3
"""PreToolUse hook for Write/Edit/MultiEdit/NotebookEdit: block any path under ledger/.
Reads the hook payload from stdin. Exit 2 blocks the tool call and shows stderr to the agent."""
import json
import os
import sys
from pathlib import Path


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    ti = payload.get("tool_input", {}) or {}
    paths = [ti.get(k) for k in ("file_path", "notebook_path", "path") if ti.get(k)]
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()
    ledger = (root / "ledger").resolve()
    for p in paths:
        rp = Path(p)
        rp = (root / rp if not rp.is_absolute() else rp).resolve()
        if rp == ledger or ledger in rp.parents:
            print(f"BLOCKED by DBTL harness: {p} is under ledger/. The ledger is append-only and written only by scripts/*.py.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
