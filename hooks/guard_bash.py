#!/usr/bin/env python3
"""PreToolUse hook for Bash: block shell commands that write into ledger/.
Catches redirection (>, >>), tee, in-place sed, cp/mv/rm/touch/truncate, python -c writes, etc.
Exit 2 blocks the call."""
import json
import re
import sys

LEDGER = r"(?:^|[^A-Za-z0-9_])(?:[^\s|;&<>]*?/)?ledger(?:/|\b)"

PATTERNS = [
    rf">{{1,2}}\s*[\"']?{LEDGER}",                       # > ledger/..., >> ledger/...
    rf"\btee\b[^|;&]*{LEDGER}",                          # tee ledger/...
    rf"\bsed\b[^|;&]*\s-[a-zA-Z]*i[a-zA-Z]*\b[^|;&]*{LEDGER}",   # sed -i ... ledger/...
    rf"\b(?:cp|mv|rm|rmdir|touch|truncate|install|dd|rsync|ln|chmod|chown)\b[^|;&]*{LEDGER}",
    rf"\b(?:python3?|perl|ruby|node)\b[^|;&]*{LEDGER}",  # scripting straight at the ledger
    rf"\bopen\([^)]*{LEDGER}[^)]*[\"'][wa]",            # open('ledger/..', 'w')
    rf"\b(?:git\s+checkout|git\s+restore|git\s+clean)\b[^|;&]*{LEDGER}",
]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    cmd = (payload.get("tool_input", {}) or {}).get("command", "") or ""
    # Allow the harness's own scripts and read-only inspection.
    if re.search(r"\bscripts/(init|hypothesize|plan|submit|collect|review|finalize|status)\.py\b", cmd) and not re.search(r">{1,2}\s*[\"']?[^\s]*ledger", cmd):
        return 0
    for pat in PATTERNS:
        if re.search(pat, cmd):
            print("BLOCKED by DBTL harness: this command writes into ledger/. The ledger is append-only and written only by scripts/*.py. "
                  "Read it with cat/jq/status.py instead.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
