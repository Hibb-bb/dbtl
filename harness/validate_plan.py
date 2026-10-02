"""Rules from spec §6.2. Every rejection names the rule and the offending pick."""
from __future__ import annotations

import re

from .errors import HarnessError
from .schemas import validate
from .state import State

SEP_EPS = 1e-6


def check_plan(state: State, pool: list[dict], plan: dict) -> dict:
    validate("batch_plan", plan)
    c = state.campaign
    br = c.batch_rules
    picks = plan["picks"]
    if plan["round"] != state.round:
        raise HarnessError("plan_round", f"plan.round is {plan['round']} but the campaign is in round {state.round}")
    if len(picks) != c.batch_size:
        raise HarnessError("batch_size", f"plan has {len(picks)} picks; batch_size is {c.batch_size}")
    if state.budget_left < len(picks):
        raise HarnessError("budget", f"plan needs {len(picks)} experiments but only {state.budget_left} remain in the budget")

    active = state.active_hypotheses
    if not active:
        raise HarnessError("no_active_hypothesis", "no hypothesis is active; register one with hypothesize.py first")
    pool_ids = {p["id"] for p in pool}
    measured = set(state.measured)
    all_hyp_ids = set(state.hypotheses)

    seen: set[str] = set()
    n_explore = n_discr = 0
    for i, p in enumerate(picks):
        cid = p["candidate"]
        tag = f"pick {i} ({cid})"
        if cid not in pool_ids:
            raise HarnessError("unknown_candidate", f"{tag}: not in the pool")
        if cid in seen:
            raise HarnessError("duplicate_candidate", f"{tag}: appears more than once in the batch")
        seen.add(cid)
        if br.no_repeat_measured and cid in measured:
            raise HarnessError("already_measured", f"{tag}: already measured (observation {state.measured[cid].obs_id}); no_repeat_measured is on")

        serves, role = p["serves"], p["role"]
        if serves is None:
            if role != "explore":
                raise HarnessError("serves_or_explore", f"{tag}: has serves=null but role '{role}'; a pick with no hypothesis must be role 'explore'")
            n_explore += 1
        else:
            if serves not in active:
                raise HarnessError("serves_inactive", f"{tag}: serves '{serves}', which is not an active hypothesis (active: {sorted(active)})")
            if role == "explore":
                raise HarnessError("serves_or_explore", f"{tag}: role 'explore' must have serves=null, not '{serves}'")
            if role == "discriminate":
                n_discr += 1

        lo, hi = p["interval"]
        if br.require_interval:
            if lo > hi:
                raise HarnessError("interval", f"{tag}: interval [{lo}, {hi}] is inverted")
            if not (lo <= p["predicted"] <= hi):
                raise HarnessError("interval", f"{tag}: predicted {p['predicted']} lies outside its own interval [{lo}, {hi}]")

        pbh = p["predictions_by_hypothesis"]
        missing = sorted(set(active) - set(pbh))
        extra = sorted(set(pbh) - set(active))
        if missing:
            raise HarnessError("predictions_by_hypothesis", f"{tag}: no prediction under active hypothesis(es) {missing}")
        if extra:
            raise HarnessError("predictions_by_hypothesis", f"{tag}: predictions for non-active hypothesis(es) {extra}")
        if serves is not None and abs(pbh[serves] - p["predicted"]) > SEP_EPS:
            raise HarnessError("predictions_by_hypothesis",
                               f"{tag}: predicted={p['predicted']} but predictions_by_hypothesis[{serves}]={pbh[serves]}; they must agree")

        why = p["why_this_one"].strip()
        if not why:
            raise HarnessError("why_this_one", f"{tag}: why_this_one is empty")
        if not _mentions_other(why, cid, pool_ids, all_hyp_ids):
            raise HarnessError("why_this_one", f"{tag}: why_this_one must mention at least one other candidate or a hypothesis id; a reason that compares nothing is not a reason")

    need_e, need_d = br.explore_min(c.batch_size), br.discriminate_min(c.batch_size)
    if n_explore < need_e:
        raise HarnessError("explore_floor", f"{n_explore} explore pick(s); explore_floor {br.explore_floor} of {c.batch_size} requires at least {need_e}")
    if n_discr < need_d:
        raise HarnessError("discriminate_floor", f"{n_discr} discriminate pick(s); discriminate_floor {br.discriminate_floor} of {c.batch_size} requires at least {need_d}")

    # Separation check: every pair of active hypotheses must disagree on some pick.
    ids = sorted(active)
    for a_i in range(len(ids)):
        for b_i in range(a_i + 1, len(ids)):
            a, b = ids[a_i], ids[b_i]
            if not any(abs(p["predictions_by_hypothesis"][a] - p["predictions_by_hypothesis"][b]) > SEP_EPS for p in picks):
                raise HarnessError("separation", f"hypotheses {a} and {b} predict the same value for every pick; this round cannot tell them apart")
    return plan


def _mentions_other(text: str, self_id: str, pool_ids: set[str], hyp_ids: set[str]) -> bool:
    for h in hyp_ids:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(h)}(?![A-Za-z0-9])", text):
            return True
    for cid in pool_ids:
        if cid != self_id and re.search(rf"(?<![A-Za-z0-9]){re.escape(cid)}(?![A-Za-z0-9])", text):
            return True
    return False
