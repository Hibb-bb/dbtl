"""The harness operations. Scripts are thin wrappers around these."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from .config import Campaign, load_campaign
from .errors import HarnessError, StateError
from .kill import KillCriterion, evaluate
from .ledger import Ledger, sha256_text, canonical
from .schemas import validate
from .state import State, load_state
from .validate_hypothesis import check_hypothesis
from .validate_plan import check_plan


def _adapter(c: Campaign):
    import sys
    root = str(c.root)
    repo = str(Path(__file__).resolve().parent.parent)
    for p in (repo, root):
        if p not in sys.path:
            sys.path.insert(0, p)
    from adapters import load_adapter
    return load_adapter(c)


def _ctx(root) -> tuple[Campaign, Ledger, State]:
    c = load_campaign(root)
    return c, Ledger(c.ledger_dir), load_state(c)


def _reject(ledger: Ledger, state: State, stage: str, err: HarnessError, payload) -> None:
    ledger.append("rejection", {"stage": stage, "rule": err.rule, "message": err.message, "payload": payload},
                  round_no=state.round if state.initialized else 0)


# ---------------------------------------------------------------- init
def init(root) -> dict:
    c = load_campaign(root)
    ledger = Ledger(c.ledger_dir)
    if ledger.exists():
        st = load_state(c)
        st.require("init")
    adapter = _adapter(c)
    start = adapter.load_start()
    pool = adapter.pool()
    pool_ids = {p["id"] for p in pool}
    body = {
        "task": c.task, "backend": c.backend, "seed": c.seed, "rounds": c.rounds,
        "batch_size": c.batch_size, "budget_experiments": c.budget_experiments,
        "metrics": list(adapter.metrics), "objective": adapter.objective, "maximize": adapter.maximize,
        "pool_size": len(pool), "pool_hash": sha256_text(canonical(pool)),
        "campaign_yaml_hash": sha256_text((c.root / "campaign.yaml").read_text()),
    }
    ledger.append("campaign_init", body, round_no=0, actor="harness")
    raw_path, raw_hash = ledger.store_raw(0, "start.json", start)
    ledger.append("result", {"round": 0, "handle": "start", "raw_path": raw_path, "raw_hash": raw_hash,
                             "n_returned": len(start), "n_failed": sum(1 for m in start if m.get("failed"))},
                  round_no=0, actor="harness")
    for i, m in enumerate(start):
        if m["candidate"] not in pool_ids:
            raise HarnessError("backend", f"starting batch names {m['candidate']} which is not in the pool")
        ledger.append("observation", {"obs_id": f"W0-{i:02d}", "round": 0, "candidate": m["candidate"],
                                      "values": m.get("values", {}), "failed": bool(m.get("failed", False))},
                      round_no=0, actor="harness")
    return {"ok": True, "start_batch": len(start), "pool": len(pool), "next": "hypothesize"}


# ---------------------------------------------------------------- hypothesize
def hypothesize(root, records: list[dict] | dict) -> dict:
    c, ledger, st = _ctx(root)
    st.require("hypothesize")
    if isinstance(records, dict):
        records = [records]
    if not records:
        raise HarnessError("input", "no hypothesis records given")
    adapter = _adapter(c)
    pool, metrics = adapter.pool(), list(adapter.metrics)
    working = dict(st.active_hypotheses)
    normalized = []
    try:
        for rec in records:
            if not isinstance(rec, dict):
                raise HarnessError("input", "each hypothesis must be a JSON object")
            out = check_hypothesis(st, pool, metrics, rec, working)
            if out["status"] == "active":
                working[out["id"]] = out
            else:
                working.pop(out["id"], None)
            # the working set must also see this record as "prior" for duplicates in the same batch
            st.hypotheses[out["id"]] = out
            normalized.append(out)
    except HarnessError as e:
        if not isinstance(e, StateError):
            _reject(ledger, st, "hypothesize", e, records)
        raise
    for out in normalized:
        ledger.append("hypothesis", out, round_no=st.round, actor="agent")
    return {"ok": True, "registered": [o["id"] for o in normalized], "active": sorted(working), "next": "plan"}


# ---------------------------------------------------------------- plan
def plan(root, plan_obj: dict) -> dict:
    c, ledger, st = _ctx(root)
    st.require("plan")
    pool = _adapter(c).pool()
    try:
        check_plan(st, pool, plan_obj)
    except HarnessError as e:
        _reject(ledger, st, "plan", e, plan_obj)
        raise
    ledger.append("batch_plan", plan_obj, round_no=st.round, actor="agent")
    roles = {r: sum(1 for p in plan_obj["picks"] if p["role"] == r) for r in ("exploit", "discriminate", "explore")}
    return {"ok": True, "round": st.round, "picks": len(plan_obj["picks"]), "roles": roles, "next": "submit"}


# ---------------------------------------------------------------- submit
def submit(root) -> dict:
    c, ledger, st = _ctx(root)
    st.require("submit")
    plan_obj = st.valid_plan(st.round)
    adapter = _adapter(c)
    pool = adapter.pool()
    candidates = [p["candidate"] for p in plan_obj["picks"]]
    # Re-validate: the plan on the ledger must still pass (budget etc.).
    check_plan(st, pool, plan_obj)
    shadow = _run_shadow(c, st, pool, adapter)
    handle = adapter.submit(st.round, candidates)
    ledger.append("submission", {"round": st.round, "handle": handle, "candidates": candidates, "shadow": shadow},
                  round_no=st.round, actor="harness")
    return {"ok": True, "round": st.round, "handle": handle, "shadow": {k: len(v) for k, v in shadow.items()}, "next": "collect"}


def _run_shadow(c: Campaign, st: State, pool: list[dict], adapter) -> dict[str, list[str]]:
    import sys
    repo = str(Path(__file__).resolve().parent.parent)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from baselines import BASELINES
    measured = {cid: o.values[adapter.objective] for cid, o in st.measured.items() if adapter.objective in o.values}
    out = {}
    for name in c.shadow_baselines:
        fn = BASELINES[name]
        out[name] = list(fn(pool, measured, c.batch_size, seed=c.seed * 1000 + st.round, maximize=adapter.maximize))
    return out


# ---------------------------------------------------------------- collect
def collect(root) -> dict:
    c, ledger, st = _ctx(root)
    st.require("collect")
    sub = st.submission(st.round)
    adapter = _adapter(c)
    res = adapter.collect(sub["handle"])
    if res is None:
        return {"ok": True, "ready": False, "round": st.round, "handle": sub["handle"], "next": "collect (results not ready; try again later)"}
    raw_path, raw_hash = ledger.store_raw(st.round, "results.json", res)
    ms = res.get("measurements", [])
    submitted = set(sub["candidates"])
    returned = {m["candidate"] for m in ms}
    unknown = sorted(returned - submitted)
    if unknown:
        raise HarnessError("backend", f"backend returned candidates that were not submitted: {unknown}")
    ledger.append("result", {"round": st.round, "handle": sub["handle"], "raw_path": raw_path, "raw_hash": raw_hash,
                             "n_returned": len(ms), "n_failed": sum(1 for m in ms if m.get("failed")),
                             "missing": sorted(submitted - returned)},
                  round_no=st.round, actor="harness")
    for i, m in enumerate(ms):
        failed = bool(m.get("failed", False))
        values = {} if failed else dict(m.get("values", {}))
        if not failed and adapter.objective not in values:
            raise HarnessError("backend", f"measurement for {m['candidate']} lacks objective metric '{adapter.objective}' and is not flagged failed")
        ledger.append("observation", {"obs_id": f"W{st.round}-{i:02d}", "round": st.round, "candidate": m["candidate"],
                                      "values": values, "failed": failed},
                      round_no=st.round, actor="harness")
    return {"ok": True, "ready": True, "round": st.round, "returned": len(ms), "failed": sum(1 for m in ms if m.get("failed")),
            "missing": sorted(submitted - returned), "next": "review"}


# ---------------------------------------------------------------- review
def review(root) -> dict:
    c, ledger, st = _ctx(root)
    st.require("review")
    r = st.round
    res, sub, plan_obj = st.result(r), st.submission(r), st.valid_plan(r)
    if res.get("missing"):
        raise HarnessError("results_incomplete", f"round {r} results are incomplete; missing {res['missing']}. Collect again before reviewing.")
    adapter = _adapter(c)
    pool = adapter.pool()
    feats = {p["id"]: p["features"] for p in pool}
    obj = adapter.objective
    this_round = [o for o in st.observations if o.round == r]
    obs_by_cand = {o.candidate: o for o in this_round}

    per_pick = []
    for p in plan_obj["picks"]:
        o = obs_by_cand.get(p["candidate"])
        if o is None or o.failed:
            per_pick.append({"candidate": p["candidate"], "predicted": p["predicted"], "observed": None, "in_interval": None, "failed": True})
        else:
            v = o.values[obj]
            lo, hi = p["interval"]
            per_pick.append({"candidate": p["candidate"], "predicted": p["predicted"], "observed": v, "in_interval": bool(lo <= v <= hi), "failed": False})

    per_hyp, kills = {}, []
    for hid, h in st.active_hypotheses.items():
        kc = h["kill_criterion"]
        crit = KillCriterion(kc["metric"], kc["comparator"], float(kc["threshold"]), int(kc["count"]), kc["scope"])
        verdict, trigger = evaluate(crit, st.observations, feats, since_round=h["round_opened"], this_round=r)
        per_hyp[hid] = {"verdict": verdict, "trigger": trigger}
        if verdict == "killed":
            kills.append((hid, dict(h, status="killed", round_closed=r, close_verdict="killed")))

    hits, n = 0, 0
    for rr in range(1, r + 1):
        pl, obs = st.valid_plan(rr), {o.candidate: o for o in st.observations if o.round == rr}
        for p in pl["picks"]:
            o = obs.get(p["candidate"])
            if o is None or o.failed:
                continue
            n += 1
            hits += int(p["interval"][0] <= o.values[obj] <= p["interval"][1])
    calibration = {"interval_hit_rate": round(hits / n, 4) if n else None, "n": n}

    shadow = _score_shadow(adapter, obj, sub, obs_by_cand)
    body = {"round": r, "per_pick": per_pick, "per_hypothesis": per_hyp, "calibration_to_date": calibration, "shadow": shadow}
    validate("round_review", body)
    for hid, rec in kills:
        validate("hypothesis", rec)
        ledger.append("hypothesis", rec, round_no=r, actor="review")
    ledger.append("round_review", body, round_no=r, actor="review")
    nxt = "finalize" if r >= c.rounds else "hypothesize"
    return {"ok": True, "round": r, "review": body, "killed": [k for k, _ in kills], "next": nxt}


def _score_shadow(adapter, obj: str, sub: dict, obs_by_cand: dict) -> dict:
    def mean_of(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.fmean(vals), 4) if vals else None
    agent_vals = [o.values[obj] for o in obs_by_cand.values() if not o.failed]
    out = {"agent_batch_mean": mean_of(agent_vals)}
    for name, cands in sub.get("shadow", {}).items():
        truth = adapter.oracle(cands)
        if truth is not None:
            out[f"{name}_batch_mean"] = mean_of(truth.values())
        else:
            overlap = [obs_by_cand[cid].values[obj] for cid in cands if cid in obs_by_cand and not obs_by_cand[cid].failed]
            out[f"{name}_batch_mean"] = mean_of(overlap)
            out[f"{name}_overlap_n"] = len(overlap)
    return out


# ---------------------------------------------------------------- finalize
def finalize(root, deliverable: dict | list) -> dict:
    c, ledger, st = _ctx(root)
    st.require("finalize")
    adapter = _adapter(c)
    pool = adapter.pool()
    pool_ids = {p["id"] for p in pool}
    kind = c.deliverable
    body: dict = {"deliverable": kind, "rounds": c.rounds, "experiments_spent": st.experiments_spent}
    if kind == "ranking":
        if not isinstance(deliverable, list) or not all(isinstance(x, str) for x in deliverable):
            raise HarnessError("deliverable", "ranking must be a JSON list of candidate ids, best first")
        unknown = [x for x in deliverable if x not in pool_ids]
        if unknown:
            raise HarnessError("deliverable", f"ranking names unknown candidate(s) {unknown[:5]}")
        if len(set(deliverable)) != len(deliverable):
            raise HarnessError("deliverable", "ranking contains duplicate candidates")
        if not deliverable:
            raise HarnessError("deliverable", "ranking is empty")
        body["ranking"] = deliverable
        body["score"] = _score_ranking(adapter, deliverable, st)
    elif kind == "model":
        if not isinstance(deliverable, dict) or not all(isinstance(v, (int, float)) for v in deliverable.values()):
            raise HarnessError("deliverable", "model must be a JSON object {candidate: predicted objective} covering the pool")
        missing = sorted(pool_ids - set(deliverable))
        if missing:
            raise HarnessError("deliverable", f"model is missing predictions for {len(missing)} pool candidate(s), e.g. {missing[:5]}")
        body["model"] = deliverable
        body["score"] = _score_model(adapter, deliverable, st)
    else:  # designs
        if not isinstance(deliverable, list) or not deliverable or not all(isinstance(x, dict) and "features" in x for x in deliverable):
            raise HarnessError("deliverable", "designs must be a non-empty JSON list of {\"features\": {...}} objects")
        body["designs"] = deliverable
        body["score"] = None
    body["hypotheses"] = _hypothesis_summary(st)
    ledger.append("final", body, round_no=st.round, actor="harness")
    return {"ok": True, "deliverable": kind, "score": body["score"], "hypotheses": body["hypotheses"], "next": "done"}


def _rank_metrics(truth: dict[str, float], ranking: list[str], maximize: bool, k: int) -> dict:
    import numpy as np
    ids = [r for r in ranking if r in truth]
    if len(ids) < 2:
        return {}
    order = sorted(truth, key=lambda x: truth[x], reverse=maximize)
    topk = set(order[:k])
    prec = sum(1 for x in ids[:k] if x in topk) / min(k, len(ids))
    tr = np.array([truth[x] for x in ids])
    pos = np.arange(len(ids))
    rt = np.argsort(np.argsort(-tr if maximize else tr))
    rho = float(np.corrcoef(pos, rt)[0, 1]) if len(ids) > 2 else None
    return {"n_scored": len(ids), "top_k": k, "precision_at_k": round(prec, 4), "spearman": round(rho, 4) if rho is not None else None}


def _score_ranking(adapter, ranking: list[str], st: State) -> dict | None:
    k = min(st.campaign.batch_size, len(ranking))
    truth = adapter.oracle(ranking)
    if truth is not None:
        return dict(_rank_metrics(truth, ranking, adapter.maximize, k), scored_by="oracle")
    obs = {cid: o.values[adapter.objective] for cid, o in st.measured.items()}
    return dict(_rank_metrics(obs, ranking, adapter.maximize, k), scored_by="measured_only")


def _score_model(adapter, model: dict, st: State) -> dict | None:
    import numpy as np
    truth = adapter.oracle(list(model))
    scored_by = "oracle"
    if truth is None:
        truth = {cid: o.values[adapter.objective] for cid, o in st.measured.items()}
        scored_by = "measured_only"
    ids = [i for i in model if i in truth]
    if len(ids) < 2:
        return {"scored_by": scored_by, "n_scored": len(ids)}
    p = np.array([model[i] for i in ids], float)
    t = np.array([truth[i] for i in ids], float)
    pearson = round(float(np.corrcoef(p, t)[0, 1]), 4) if p.std() > 0 and t.std() > 0 else None
    return {"scored_by": scored_by, "n_scored": len(ids), "rmse": round(float(np.sqrt(((p - t) ** 2).mean())), 4),
            "pearson": pearson}


def _hypothesis_summary(st: State) -> dict:
    counts = {"opened": len(st.hypothesis_history), "killed": 0, "retired": 0, "active_at_end": 0}
    for h in st.hypotheses.values():
        if h["status"] == "killed":
            counts["killed"] += 1
        elif h["status"] == "retired":
            counts["retired"] += 1
        else:
            counts["active_at_end"] += 1
    counts["rejections"] = len(st.entries_of("rejection"))
    return counts
