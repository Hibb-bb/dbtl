"""The no-harness control arm: same backend, budget, ledger and scoring, no reasoning scaffold.

An agent in this arm submits plain lists of candidate ids. There are no hypotheses, plans,
predictions or kill criteria. The ledger still records every submission and observation, the
shadow baselines still run, and finalize scores the deliverable exactly as in the harness arm,
so the two arms differ only in what the agent is made to write down.

A ledger is one arm or the other: raw_submit refuses once a hypothesis or plan exists, and
hypothesize/plan refuse once a control submission exists.
"""
from __future__ import annotations

from .api import _adapter, _ctx, _run_shadow, _score_shadow, collect as _collect
from .errors import HarnessError, StateError
from .schemas import validate
from .state import State


def is_control(st: State) -> bool:
    return any(e["body"].get("arm") == "control" for e in st.entries_of("submission"))


def _require_control_ok(st: State) -> None:
    if not st.initialized:
        raise StateError("Campaign is not initialized.", "init")
    if st.finalized:
        raise StateError("Campaign is finalized; nothing more to do.", "done")
    if st.entries_of("hypothesis") or st.entries_of("batch_plan"):
        raise HarnessError("arm", "this ledger is running the harness arm (hypotheses/plans exist); raw submission is not allowed")


# ---------------------------------------------------------------- submit
def raw_submit(root, candidates: list[str]) -> dict:
    c, ledger, st = _ctx(root)
    _require_control_ok(st)
    nxt = st.step()
    if nxt == "finalize":
        raise StateError(f"All {c.rounds} rounds are done.", "finalize")
    if nxt not in ("hypothesize", "plan"):  # i.e. nothing submitted for this round yet
        raise StateError(f"Round {st.round} was already submitted.", "raw_collect")
    if not isinstance(candidates, list) or not all(isinstance(x, str) for x in candidates):
        raise HarnessError("input", "candidates must be a list of candidate ids")
    adapter = _adapter(c)
    pool = adapter.pool()
    pool_ids = {p["id"] for p in pool}
    if len(candidates) != c.batch_size:
        raise HarnessError("batch_size", f"submit exactly {c.batch_size} candidates; got {len(candidates)}")
    if len(set(candidates)) != len(candidates):
        raise HarnessError("duplicate", "batch contains duplicate candidates")
    unknown = [x for x in candidates if x not in pool_ids]
    if unknown:
        raise HarnessError("unknown_candidate", f"not in the pool: {unknown[:5]}")
    if c.batch_rules.no_repeat_measured:
        rep = [x for x in candidates if x in st.measured]
        if rep:
            raise HarnessError("repeat_measured", f"already measured: {rep[:5]}")
    if st.budget_left < len(candidates):
        raise HarnessError("budget", f"only {st.budget_left} experiments left")
    shadow = _run_shadow(c, st, pool, adapter)
    handle = adapter.submit(st.round, candidates)
    ledger.append("submission", {"round": st.round, "handle": handle, "candidates": list(candidates),
                                 "shadow": shadow, "arm": "control"},
                  round_no=st.round, actor="agent")
    return {"ok": True, "round": st.round, "handle": handle, "next": "raw_collect"}


# ---------------------------------------------------------------- collect (+ automatic review)
def raw_collect(root) -> dict:
    c, ledger, st = _ctx(root)
    _require_control_ok(st)
    nxt = st.step()
    if nxt == "collect":
        out = _collect(root)
        if not out["ready"]:
            return dict(out, next="raw_collect (results not ready; try again later)")
        c, ledger, st = _ctx(root)
    elif nxt != "review":
        raise StateError(f"Nothing submitted for round {st.round}.", "raw_submit" if nxt != "finalize" else "finalize")
    return _control_review(c, ledger, st)


def _control_review(c, ledger, st: State) -> dict:
    r = st.round
    res, sub = st.result(r), st.submission(r)
    if res.get("missing"):
        raise HarnessError("results_incomplete", f"round {r} results are incomplete; missing {res['missing']}")
    adapter = _adapter(c)
    obs_by_cand = {o.candidate: o for o in st.observations if o.round == r}
    body = {"round": r, "per_pick": [], "per_hypothesis": {},
            "calibration_to_date": {"interval_hit_rate": None, "n": 0},
            "shadow": _score_shadow(adapter, adapter.objective, sub, obs_by_cand)}
    validate("round_review", body)
    ledger.append("round_review", body, round_no=r, actor="review")
    measured = [{"candidate": o.candidate, "values": o.values, "failed": o.failed} for o in obs_by_cand.values()]
    return {"ok": True, "ready": True, "round": r, "measurements": measured,
            "next": "finalize" if r >= c.rounds else "raw_submit"}
