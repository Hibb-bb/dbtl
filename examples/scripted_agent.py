#!/usr/bin/env python3
"""Scripted stand-in for the agent. Drives the CLI scripts for a full campaign on the fake backend.
Its hypotheses are deliberately naive; it exists to exercise the whole loop through subprocesses.
Run from the repo root with a fresh ledger:  .venv/bin/python examples/scripted_agent.py
"""
import json, subprocess, sys, statistics
from pathlib import Path
ROOT = Path.cwd()
PY = [sys.executable]
def run(script, *args):
    p = subprocess.run(PY + [f"scripts/{script}.py", "--json", *args], capture_output=True, text=True)
    if p.returncode != 0:
        print(f"{script} -> exit {p.returncode}: {p.stderr.strip()}"); sys.exit(1)
    return json.loads(p.stdout)
def ledger():
    return [json.loads(l) for l in (ROOT / "ledger/chain.jsonl").read_text().splitlines()]
def obs():
    return [e["body"] for e in ledger() if e["kind"] == "observation" and not e["body"]["failed"]]
pool = {c["id"]: c["features"] for c in json.loads(subprocess.run(PY + ["-c", "import sys;sys.path.insert(0,'.');from harness.config import load_campaign;from adapters import load_adapter;import json;print(json.dumps(load_adapter(load_campaign('.')).pool()))"], capture_output=True, text=True).stdout)}
print(run("init"))
hyp_n = 0
for rnd in range(1, 9):
    o = obs(); measured = {x["candidate"]: x["values"]["activity"] for x in o}
    hyd = [v for c, v in measured.items() if pool[c]["res47_hydrophobic"]]
    non = [v for c, v in measured.items() if not pool[c]["res47_hydrophobic"]]
    active = {e["body"]["id"]: e["body"] for e in ledger() if e["kind"] == "hypothesis"}
    active = {k: v for k, v in active.items() if v["status"] == "active"}
    hyps = []
    if "H1" not in active:
        hyp_n += 1
        ev = [c for c in measured if pool[c]["res47_hydrophobic"]][:2]
        hyps.append({"id": "H1", "statement": "activity rises with hydrophobicity at site 47", "reason": f"hydrophobic mean {statistics.fmean(hyd):.2f} vs {statistics.fmean(non):.2f} elsewhere",
                     "evidence": ev, "prediction": "hydrophobic-47 variants score above 0.5",
                     "kill_criterion": {"text": "3+ hydrophobic-47 variants below 0.35", "metric": "activity", "comparator": "lt", "threshold": 0.35, "count": 3,
                                        "scope": {"all": [{"feature": "res47_hydrophobic", "op": "eq", "value": True}]}}, "confidence": 0.6, "status": "active"})
    else:
        h = dict(active["H1"]); [h.pop(k, None) for k in ("round_opened", "round_closed", "close_verdict")]; hyps.append(h)
    SLOT2 = [  # rival hypotheses tried in order; each death opens the next with a new id
        ("high absolute charge suppresses activity regardless of site 47", "charge", "gt", 1.5, "gt", 0.6, lambda f: 0.65 - 0.2 * abs(f["charge"])),
        ("large residues at the size axis boost activity", "size", "gt", 0.5, "lt", 0.3, lambda f: 0.35 + 0.5 * f["size"]),
        ("flexibility above 0.5 boosts activity", "flex", "gt", 0.5, "lt", 0.3, lambda f: 0.35 + 0.4 * f["flex"]),
    ]
    all_h = {e["body"]["id"]: e["body"] for e in ledger() if e["kind"] == "hypothesis"}
    rival = [k for k in active if k != "H1"]
    if rival:
        h = dict(active[rival[0]]); [h.pop(k, None) for k in ("round_opened", "round_closed", "close_verdict")]; hyps.append(h)
        rival_id = rival[0]
    else:
        n_dead = sum(1 for k, v in all_h.items() if k != "H1" and v["status"] == "killed")
        stmt, feat, sop, sval, cmp_, thr, _ = SLOT2[min(n_dead, len(SLOT2) - 1)]
        rival_id = "H" + str(max([int(k[1:]) for k in all_h] + [1]) + 1)
        ev = sorted(measured, key=lambda c: -pool[c][feat])[:2]
        hyps.append({"id": rival_id, "statement": stmt, "reason": f"pattern seen in the start batch; opened after {n_dead} rival death(s)",
                     "evidence": ev, "prediction": f"variants with {feat} {sop} {sval} behave differently from the rest",
                     "kill_criterion": {"text": f"2+ variants with {feat} {sop} {sval} score {cmp_} {thr}", "metric": "activity", "comparator": cmp_, "threshold": thr, "count": 2,
                                        "scope": {"all": [{"feature": feat, "op": sop, "value": sval}]}}, "confidence": 0.45, "status": "active"})
    RIVAL_PRED = SLOT2[min(sum(1 for k, v in all_h.items() if k != "H1" and v["status"] == "killed"), len(SLOT2) - 1)][6]
    print(f"round {rnd} hypothesize:", run("hypothesize", json.dumps(hyps))["active"])
    unm = [c for c in pool if c not in measured]
    def pred(c, h):
        f = pool[c]
        if h == "H1": return round(min(max(0.5 + 0.3 * f["hydro47"], 0.05), 0.95), 3)
        return round(min(max(RIVAL_PRED(f), 0.05), 0.95), 3)
    cand = sorted(unm, key=lambda c: -(pred(c, "H1") + pred(c, rival_id)))
    disc = sorted(unm, key=lambda c: -abs(pred(c, "H1") - pred(c, rival_id)))
    picks, used = [], set()
    def add(c, serves, role, why):
        pbh = {"H1": pred(c, "H1"), rival_id: pred(c, rival_id)}
        p = pbh[serves] if serves else round((pbh["H1"] + pbh[rival_id]) / 2, 3)
        picks.append({"candidate": c, "serves": serves, "role": role, "predicted": p, "interval": [round(max(p - 0.25, 0), 3), round(min(p + 0.25, 1), 3)],
                      "why_this_one": why, "predictions_by_hypothesis": pbh}); used.add(c)
    for c in disc:
        if len([p for p in picks if p["role"] == "discriminate"]) >= 3: break
        if c not in used: add(c, "H1", "discriminate", f"H1 says {pred(c,'H1')}, {rival_id} says {pred(c,rival_id)}")
    for c in cand:
        if len(picks) >= 9: break
        if c not in used: add(c, "H1" if pred(c, "H1") >= pred(c, rival_id) else rival_id, "exploit", f"both H1 and {rival_id} rank it near the top, above {cand[-1]}")
    for c in sorted(unm, key=lambda c: pool[c]["flex"]):
        if len(picks) >= 12: break
        if c not in used: add(c, None, "explore", f"low-flex region untested; compare {picks[0]['candidate']}")
    print(f"round {rnd} plan:", run("plan", json.dumps({"round": rnd, "picks": picks}))["roles"])
    print(f"round {rnd} submit:", run("submit")["handle"])
    while not run("collect")["ready"]: pass
    r = run("review"); print(f"round {rnd} review:", {k: v["verdict"] for k, v in r["review"]["per_hypothesis"].items()}, r["review"]["shadow"], "next:", r["next"])
o = obs(); ranking = [x["candidate"] for x in sorted(o, key=lambda x: -x["values"]["activity"])]
print("finalize:", run("finalize", json.dumps(ranking)))
