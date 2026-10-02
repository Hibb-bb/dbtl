#!/usr/bin/env python3
"""Control arm: where the campaign stands (round, budget, next step, best measured so far)."""
import json

from _common import parser
from harness.config import load_campaign
from harness.ledger import Ledger
from harness.state import load_state

NEXT = {"hypothesize": "raw_submit", "plan": "raw_submit", "collect": "raw_collect", "review": "raw_collect"}

if __name__ == "__main__":
    args = parser(__doc__).parse_args()
    c = load_campaign(args.root)
    led = Ledger(c.ledger_dir)
    ok, msg = led.verify()
    if not led.exists():
        info = {"chain_ok": ok, "next": "init"}
    else:
        st = load_state(c)
        obj = st.entries_of("campaign_init")[0]["body"]["objective"]
        best = sorted(((o.values[obj], cid) for cid, o in st.measured.items() if obj in o.values), reverse=True)[:5]
        info = {"chain_ok": ok, "round": min(st.round, c.rounds), "rounds": c.rounds, "rounds_done": st.rounds_reviewed,
                "budget_left": st.budget_left, "measured": len(st.measured), "next": NEXT.get(st.step(), st.step()),
                "top5_measured": [{"candidate": cid, obj: v} for v, cid in best]}
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        for k, v in info.items():
            print(f"{k}: {json.dumps(v) if isinstance(v, (list, dict)) else v}")
