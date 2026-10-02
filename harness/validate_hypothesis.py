"""Rules from spec §6.1. Every rejection names the rule and the offending item."""
from __future__ import annotations

import copy

from .errors import HarnessError
from .kill import parse_kill_criterion
from .schemas import validate
from .state import State


def _resolve_evidence(state: State, ref: str):
    """Evidence may cite an observation id (W2-07) or a candidate id (C-221)."""
    by_id = state.obs_by_id
    if ref in by_id:
        return by_id[ref]
    pool_ids = {c["id"] for c in state.pool} if hasattr(state, "pool") else set()
    cands = [o for o in state.observations if o.candidate == ref]
    if cands:
        return max(cands, key=lambda o: (not o.failed, o.round))
    if ref in pool_ids:
        raise HarnessError("evidence_unmeasured", f"evidence '{ref}' exists in the pool but was never measured")
    raise HarnessError("evidence_unknown", f"evidence '{ref}' does not exist in the ledger")


def check_hypothesis(state: State, pool: list[dict], metrics: list[str], record: dict,
                     active_now: dict[str, dict]) -> dict:
    """Validate one agent-submitted record against state + the working active set.

    Returns the normalized record to store. Raises HarnessError on the first violation.
    """
    rules = state.campaign.hypotheses
    rec = copy.deepcopy(record)
    rec.setdefault("status", "active")
    validate("hypothesis", rec)
    hid = rec["id"]

    if rec["status"] == "killed":
        raise HarnessError("agent_cannot_kill", f"{hid}: only review.py can set status 'killed'. Submit 'active' or 'retired'.")
    for k in ("round_closed", "close_verdict"):
        if rec.get(k) is not None:
            raise HarnessError("agent_cannot_close", f"{hid}: '{k}' is written by the harness, not the agent")

    state.pool = pool  # for evidence resolution
    # 1. evidence exists and is measured
    seen_rounds = []
    for ref in rec["evidence"]:
        obs = _resolve_evidence(state, ref)
        if obs.failed:
            raise HarnessError("evidence_failed_well", f"{hid}: evidence '{ref}' is a failed well; a failed well is not an observation")
        seen_rounds.append(obs.round)
    # 2. enough evidence
    if len(rec["evidence"]) < rules.min_evidence:
        raise HarnessError("min_evidence", f"{hid}: cites {len(rec['evidence'])} observation(s); min_evidence is {rules.min_evidence}")
    # 3. kill criterion is machine-checkable
    if rules.require_kill_number:
        parse_kill_criterion(rec["kill_criterion"], metrics, pool)
    # 4. confidence exclusive bounds: schema enforces (0,1)

    prior = state.hypotheses.get(hid)
    prior_status = prior["status"] if prior else None
    # 6. reopening a killed hypothesis needs post-death evidence
    if prior_status == "killed" and rec["status"] == "active":
        if rules.reopen_requires_new_evidence:
            closed = prior["round_closed"]
            if not any(r > closed for r in seen_rounds):
                raise HarnessError("reopen_requires_new_evidence",
                                   f"{hid} was killed in round {closed}; reopening requires at least one observation measured after round {closed}, but all cited evidence predates it")
        rec["round_opened"] = state.round
    elif prior_status == "active":
        rec["round_opened"] = prior["round_opened"]   # continuing, or retiring
    else:
        rec["round_opened"] = state.round             # new, or reopening a retired one

    if rec["status"] == "retired":
        if prior_status != "active":
            raise HarnessError("retire_inactive", f"{hid}: cannot retire a hypothesis that is not active (status {prior_status})")
        rec["round_closed"] = state.round
        rec["close_verdict"] = "retired"
    else:
        rec["round_closed"] = None
        rec["close_verdict"] = None

    # 5. max_active (after applying this record)
    working = dict(active_now)
    if rec["status"] == "active":
        working[hid] = rec
    else:
        working.pop(hid, None)
    if len(working) > rules.max_active:
        raise HarnessError("max_active", f"{hid}: would make {len(working)} active hypotheses; max_active is {rules.max_active}. Active: {sorted(working)}")
    return rec
