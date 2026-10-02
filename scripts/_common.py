"""Shared CLI plumbing for the harness scripts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.errors import HarnessError, StateError  # noqa: E402


def parser(desc: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--root", default=os.environ.get("DBTL_ROOT", str(REPO)),
                   help="campaign directory holding campaign.yaml and ledger/ (default: repo root or $DBTL_ROOT)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    return p


def load_json_arg(arg: str):
    """`arg` is a path, or '-' for stdin, or an inline JSON literal."""
    if arg == "-":
        return json.load(sys.stdin)
    stripped = arg.lstrip()
    if not stripped.startswith(("{", "[")):
        p = Path(arg)
        if p.exists():
            with open(p) as f:
                return json.load(f)
        print(f"ERROR: file not found: {arg}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(arg)
    except json.JSONDecodeError:
        print(f"ERROR: {arg!r} is neither a file nor valid JSON", file=sys.stderr)
        sys.exit(2)


def run(fn, args, *fn_args):
    try:
        out = fn(args.root, *fn_args)
    except StateError as e:
        print(f"REFUSED [state]: {e.message}", file=sys.stderr)
        sys.exit(3)
    except HarnessError as e:
        print(f"REJECTED [{e.rule}]: {e.message}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        for k, v in out.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            print(f"{k}: {v}")
    return out
