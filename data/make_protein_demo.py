#!/usr/bin/env python3
"""Generate the synthetic protein_active_learning dataset used by the v0/v1 demos.

400 single/double variants at sites 47, 112, 160 with per-site physicochemical descriptors.
Hidden activity: hydrophobic 47 helps, charge at 112 hurts beyond |1|, size at 160 is
optimal near 0.6, with an interaction between 47 and 160. ~3% recorded failures.
Deterministic (seed 2026). Run: python data/make_protein_demo.py
"""
import json
import math
import random
from pathlib import Path

RES = {"A": (1.8, 0, .3), "L": (3.8, 0, .6), "V": (4.2, 0, .5), "F": (2.8, 0, .8), "I": (4.5, 0, .6), "W": (-0.9, 0, 1.0),
       "D": (-3.5, -1, .4), "E": (-3.5, -1, .5), "K": (-3.9, 1, .7), "R": (-4.5, 1, .9), "S": (-0.8, 0, .3),
       "G": (-0.4, 0, .1), "T": (-0.7, 0, .4), "N": (-3.5, 0, .4)}
HYDRO = {"A", "L", "V", "F", "I", "W"}


def main(out=Path(__file__).with_name("protein_active_learning.json")):
    rng = random.Random(2026)
    cands = []
    seen = set()
    while len(cands) < 400:
        r47, r112, r160 = (rng.choice(list(RES)) for _ in range(3))
        if (r47, r112, r160) in seen:
            continue
        seen.add((r47, r112, r160))
        h47, c47, s47 = RES[r47]; h112, c112, s112 = RES[r112]; h160, c160, s160 = RES[r160]
        feats = {"res47": r47, "res112": r112, "res160": r160,
                 "res47_hydrophobic": r47 in HYDRO, "hydro47": h47 / 4.5, "charge112": c112,
                 "size160": s160, "net_charge": c47 + c112 + c160, "n_mutations": sum(x != "A" for x in (r47, r112, r160))}
        z = (1.4 * feats["hydro47"] - 0.8 * max(abs(c112) - 0.5, 0) - 3.0 * (s160 - 0.6) ** 2
             + 0.6 * feats["hydro47"] * (s160 > 0.5) - 0.3 * abs(feats["net_charge"]) + 0.1)
        act = 1 / (1 + math.exp(-2.2 * z))
        act = min(max(act + rng.gauss(0, 0.03), 0.0), 1.0)
        cid = f"C-{len(cands):03d}"
        measured = {"activity": round(act, 4)}
        if rng.random() < 0.03:
            measured = {"failed": True, "note": "recorded: well dried"}
        cands.append({"id": cid, "features": feats, "measured": measured})
    start = [c["id"] for c in rng.sample([c for c in cands if not c["measured"].get("failed")], 24)]
    d = {"task": "protein_active_learning", "metrics": ["activity"], "maximize": True, "start": start, "candidates": cands}
    out.write_text(json.dumps(d, indent=1, sort_keys=True))
    print(f"wrote {out} ({len(cands)} candidates, {sum(1 for c in cands if c['measured'].get('failed'))} recorded failures)")


if __name__ == "__main__":
    main()
