"""Acceptance tests 1-7 (spec §14) plus the other §6.1 rules. Every bad input must be REJECTED by name."""
import pytest

from harness import api
from harness.errors import HarnessError

from conftest import hyp, measured_ids, pool, state


def rejected(root, records, rule):
    with pytest.raises(HarnessError) as ex:
        api.hypothesize(root, records)
    assert ex.value.rule == rule, f"expected rule {rule}, got {ex.value}"
    rj = state(root).entries_of("rejection")
    assert rj and rj[-1]["body"]["rule"] == rule, "rejection must be logged on the ledger"
    return ex.value.message


def test_1_evidence_unknown_candidate(root):
    msg = rejected(root, hyp(root, evidence=[measured_ids(root)[0], "C-999"]), "evidence_unknown")
    assert "C-999" in msg


def test_2_evidence_exists_but_unmeasured(root):
    st = state(root)
    unm = sorted(p["id"] for p in pool(root) if p["id"] not in st.measured)[0]
    msg = rejected(root, hyp(root, evidence=[measured_ids(root)[0], unm]), "evidence_unmeasured")
    assert unm in msg


def test_3_too_little_evidence(root):
    msg = rejected(root, hyp(root, evidence=measured_ids(root, 1)), "min_evidence")
    assert "min_evidence is 2" in msg


def test_4_kill_criterion_prose_only(root):
    h = hyp(root)
    h["kill_criterion"] = {"text": "it stops looking right"}
    rejected(root, h, "kill_criterion_structure")


def test_4b_kill_criterion_threshold_not_number(root):
    h = hyp(root)
    h["kill_criterion"]["threshold"] = "low"
    rejected(root, h, "kill_criterion_structure")


def test_4c_kill_criterion_unknown_metric(root):
    rejected(root, hyp(root, kill_criterion={"metric": "vibes"}), "kill_criterion_structure")


def test_4d_kill_scope_unknown_feature(root):
    rejected(root, hyp(root, kill_criterion={"scope": {"all": [{"feature": "nope", "op": "eq", "value": 1}]}}), "kill_scope_unknown_feature")


def test_4e_kill_scope_matches_nothing(root):
    rejected(root, hyp(root, kill_criterion={"scope": {"all": [{"feature": "size", "op": "gt", "value": 99}]}}), "kill_scope_empty")


def test_4f_kill_scope_explicit_candidates(root):
    ids = [p["id"] for p in pool(root)][:3]
    out = api.hypothesize(root, hyp(root, kill_criterion={"scope": {"candidates": ids}}))
    assert out["registered"] == ["H1"]
    rejected(root, hyp(root, hid="H2", kill_criterion={"scope": {"candidates": ["C-999"]}}), "kill_scope_unknown_candidate")


@pytest.mark.parametrize("conf", [1.0, 0.0, 1.5, -0.1])
def test_5_confidence_exclusive(root, conf):
    rejected(root, hyp(root, confidence=conf), "confidence_range")


def test_6_fourth_active_hypothesis(root):
    api.hypothesize(root, [hyp(root, hid=f"H{i}") for i in (1, 2, 3)])
    msg = rejected(root, hyp(root, hid="H4"), "max_active")
    assert "max_active is 3" in msg and "H4" in msg


def test_6b_retire_frees_a_slot(root):
    api.hypothesize(root, [hyp(root, hid=f"H{i}") for i in (1, 2, 3)])
    api.hypothesize(root, [hyp(root, hid="H2", status="retired"), hyp(root, hid="H4")])
    assert sorted(state(root).active_hypotheses) == ["H1", "H3", "H4"]


def test_7_reopen_killed_with_only_pre_death_evidence(root):
    """Kill H1 mechanically in round 1, then try to reopen it citing round-0 evidence only."""
    from conftest import plan
    # Criterion that is certain to fire: every candidate scores < 1.0 (count 1).
    api.hypothesize(root, hyp(root, kill_criterion={"threshold": 1.0, "count": 1, "scope": {"all": [{"feature": "size", "op": "ge", "value": 0}]}}))
    api.plan(root, plan(root)); api.submit(root); api.collect(root)
    rev = api.review(root)
    assert rev["review"]["per_hypothesis"]["H1"]["verdict"] == "killed"
    assert state(root).hypotheses["H1"]["status"] == "killed"
    assert state(root).hypotheses["H1"]["round_closed"] == 1
    round0 = [o.candidate for o in state(root).observations if o.round == 0 and not o.failed][:2]
    round1 = [o.candidate for o in state(root).observations if o.round == 1 and not o.failed][:2]
    # Round-0 evidence predates the death; round-1 evidence IS the death (it was measured
    # before the close, not after). Both are pre-death. Both must be rejected.
    msg = rejected(root, hyp(root, evidence=round0), "reopen_requires_new_evidence")
    assert "H1" in msg and "round 1" in msg
    rejected(root, hyp(root, evidence=round1), "reopen_requires_new_evidence")
    # Run round 2 on a different hypothesis so new data exists.
    api.hypothesize(root, hyp(root, hid="H2", evidence=round1))
    api.plan(root, plan(root)); api.submit(root); api.collect(root); api.review(root)
    round2 = [o.candidate for o in state(root).observations if o.round == 2 and not o.failed][:1]
    api.hypothesize(root, hyp(root, evidence=round0[:1] + round2))
    assert state(root).hypotheses["H1"]["status"] == "active"
    assert state(root).hypotheses["H1"]["round_opened"] == 3


def test_agent_cannot_declare_killed(root):
    rejected(root, hyp(root, status="killed"), "agent_cannot_kill")


def test_agent_cannot_set_close_fields(root):
    rejected(root, hyp(root, round_closed=1), "agent_cannot_close")


def test_unknown_field_rejected(root):
    rejected(root, hyp(root, vibes="good"), "schema")


def test_bad_id_rejected(root):
    rejected(root, hyp(root, hid="hyp-1"), "hypothesis_id")


def test_evidence_by_observation_id(root):
    obs = [o.obs_id for o in state(root).observations if not o.failed][:2]
    assert obs[0].startswith("W0-")
    out = api.hypothesize(root, hyp(root, evidence=obs))
    assert out["registered"] == ["H1"]


def test_batch_is_atomic(root):
    """One bad record in a list means nothing is written."""
    rejected(root, [hyp(root, hid="H1"), hyp(root, hid="H2", confidence=1.0)], "confidence_range")
    assert state(root).hypotheses == {}
