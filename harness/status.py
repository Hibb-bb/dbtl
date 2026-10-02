"""status.py content: the one report a human reads."""
from __future__ import annotations

import statistics
from collections import Counter

from .config import load_campaign
from .ledger import Ledger
from .state import load_state


def status_report(root) -> tuple[str, dict]:
    c = load_campaign(root)
    ledger = Ledger(c.ledger_dir)
    ok, msg = ledger.verify()
    lines = [f"Campaign: {c.task}  backend={c.backend}  seed={c.seed}", f"Chain integrity: {'OK' if ok else 'BROKEN'} — {msg}"]
    info = {"chain_ok": ok, "chain_msg": msg}
    if not ok:
        lines.append("!!! The ledger has been tampered with or corrupted. Stop and investigate before continuing.")
        return "\n".join(lines), info
    if not ledger.exists():
        lines.append("Not initialized. Next: scripts/init.py")
        info["step"] = "init"
        return "\n".join(lines), info

    st = load_state(c)
    step = st.step()
    info.update(step=step, round=st.round, rounds=c.rounds, budget_left=st.budget_left)
    shown_round = min(st.round, c.rounds)
    lines.append(f"Round {shown_round} of {c.rounds}  (rounds left: {max(c.rounds - st.rounds_reviewed, 0)})  "
                 f"budget: {st.budget_left} of {c.budget_experiments} experiments left")
    lines.append(f"Next step: {step}")

    lines.append("\nActive hypotheses:")
    act = st.active_hypotheses
    if not act:
        lines.append("  (none)")
    for hid, h in sorted(act.items()):
        age = st.round - h["round_opened"]
        lines.append(f"  {hid}  conf={h['confidence']:.2f}  age={age} round(s)  kill: {h['kill_criterion']['text']}")
        lines.append(f"       {h['statement']}")

    never = [hid for hid, h in st.hypotheses.items() if h["status"] == "active"]
    lines.append("\nHypothesis ledger:")
    counts = Counter(h["status"] for h in st.hypotheses.values())
    lines.append(f"  opened={len(st.hypotheses)}  killed={counts.get('killed', 0)}  retired={counts.get('retired', 0)}  "
                 f"still active (never resolved)={len(never)}  rejections={len(st.entries_of('rejection'))}")
    info["hypotheses"] = dict(counts)
    if st.hypotheses and counts.get("killed", 0) == 0 and st.rounds_reviewed >= 3:
        lines.append("  WARNING: no hypothesis has died in 3+ rounds. Kill criteria may be too loose.")

    lines.append("\nCalibration (stated intervals vs observed):")
    buckets = {"narrow (<0.2)": [0, 0], "medium (0.2-0.4)": [0, 0], "wide (>=0.4)": [0, 0]}
    total = [0, 0]
    reviews = sorted(st.entries_of("round_review"), key=lambda e: e["round"])
    for e in reviews:
        pl = st.valid_plan(e["round"])
        width = {p["candidate"]: p["interval"][1] - p["interval"][0] for p in pl["picks"]}
        for pp in e["body"]["per_pick"]:
            if pp["in_interval"] is None:
                continue
            w = width[pp["candidate"]]
            key = "narrow (<0.2)" if w < 0.2 else "medium (0.2-0.4)" if w < 0.4 else "wide (>=0.4)"
            buckets[key][0] += int(pp["in_interval"]); buckets[key][1] += 1
            total[0] += int(pp["in_interval"]); total[1] += 1
    if total[1]:
        lines.append(f"  overall hit rate {total[0]/total[1]:.2f} (n={total[1]})")
        for k, (h, n) in buckets.items():
            if n:
                lines.append(f"  {k:18s} {h/n:.2f} (n={n})")
    else:
        lines.append("  (no reviewed picks yet)")
    info["calibration"] = {"hits": total[0], "n": total[1]}

    lines.append("\nAgent vs shadow baselines (batch mean of objective, per round):")
    series: dict[str, list] = {}
    for e in reviews:
        for k, v in e["body"]["shadow"].items():
            if k.endswith("_batch_mean"):
                series.setdefault(k.replace("_batch_mean", ""), []).append((e["round"], v))
    if series:
        header = "  round  " + "  ".join(f"{k:>10s}" for k in series)
        lines.append(header)
        for e in reviews:
            r = e["round"]
            row = [f"{dict(v).get(r):10.3f}" if dict(v).get(r) is not None else f"{'n/a':>10s}" for v in series.values()]
            lines.append(f"  {r:5d}  " + "  ".join(row))
        cum = {k: statistics.fmean([x for _, x in v if x is not None]) for k, v in series.items() if any(x is not None for _, x in v)}
        lines.append("  cumulative mean: " + "  ".join(f"{k}={x:.3f}" for k, x in cum.items()))
        info["shadow_cumulative"] = cum
    else:
        lines.append("  (no reviewed rounds yet)")

    roles = Counter()
    for e in st.entries_of("batch_plan"):
        if st.submission(e["round"]) and e["body"] is st.valid_plan(e["round"]):
            roles.update(p["role"] for p in e["body"]["picks"])
    tot = sum(roles.values())
    lines.append("\nPicks by role across submitted batches:")
    lines.append("  " + ("  ".join(f"{k}={v} ({v/tot:.0%})" for k, v in sorted(roles.items())) if tot else "(none submitted)"))
    info["roles"] = dict(roles)
    return "\n".join(lines), info


def mid_flight(root) -> tuple[bool, str]:
    """True when the session must not stop: a round is mid-flight or a submitted round is unreviewed."""
    c = load_campaign(root)
    ledger = Ledger(c.ledger_dir)
    ok, msg = ledger.verify()
    if not ok:
        return True, msg
    if not ledger.exists():
        return False, "not initialized"
    st = load_state(c)
    step = st.step()
    if step in ("plan", "submit", "collect", "review"):
        return True, f"round {st.round} is mid-flight; next legal step is {step}"
    return False, f"clean boundary; next step is {step}"
