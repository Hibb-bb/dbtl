#!/usr/bin/env python3
"""The one command a human runs. Verifies the chain and reports the campaign state.

  --stop-check   exit 2 (with a message) if the session must not end now; used by the Stop hook.
"""
import sys

from _common import parser
from harness.errors import HarnessError
from harness.status import mid_flight, status_report

if __name__ == "__main__":
    p = parser(__doc__)
    p.add_argument("--stop-check", action="store_true")
    args = p.parse_args()
    try:
        if args.stop_check:
            blocked, why = mid_flight(args.root)
            if blocked:
                print(f"STOP BLOCKED: {why}. Finish the round (status.py shows the next step) before ending.", file=sys.stderr)
                sys.exit(2)
            print(why)
            sys.exit(0)
        text, info = status_report(args.root)
        if args.json:
            import json
            print(json.dumps(info, indent=2, sort_keys=True))
        else:
            print(text)
        sys.exit(0 if info.get("chain_ok", True) else 4)
    except HarnessError as e:
        print(f"ERROR [{e.rule}]: {e.message}", file=sys.stderr)
        sys.exit(1)
