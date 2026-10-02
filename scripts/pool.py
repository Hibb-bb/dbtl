#!/usr/bin/env python3
"""Print the candidate pool with features, joined with the latest successful measurement of each.

  --measured     only candidates with a successful measurement
  --unmeasured   only candidates never successfully measured (failed wells count as unmeasured)
  --json         JSON list instead of a TSV table
"""
import json
import sys

from _common import parser
from harness.api import _adapter
from harness.config import load_campaign
from harness.ledger import Ledger
from harness.state import load_state

if __name__ == "__main__":
    p = parser(__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--measured", action="store_true")
    g.add_argument("--unmeasured", action="store_true")
    args = p.parse_args()
    c = load_campaign(args.root)
    adapter = _adapter(c)
    measured = load_state(c).measured if Ledger(c.ledger_dir).exists() else {}
    rows = []
    for cand in adapter.pool():
        o = measured.get(cand["id"])
        if (args.measured and o is None) or (args.unmeasured and o is not None):
            continue
        vals = {m: (o.values.get(m) if o else None) for m in adapter.metrics}
        rows.append({"id": cand["id"], **cand["features"], **vals, "obs_id": o.obs_id if o else None})
    if args.json:
        json.dump(rows, sys.stdout, indent=1)
        print()
    else:
        cols = list(rows[0]) if rows else []
        print("\t".join(cols))
        for r in rows:
            print("\t".join("" if r[k] is None else str(r[k]) for k in cols))
