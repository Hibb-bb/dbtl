"""State machine (acceptance 15-17), failed wells (19), review verdicts, and the end-to-end campaign."""
import pytest

from harness import api
from harness.errors import HarnessError, StateError
from harness.ledger import Ledger

from conftest import hyp, plan, run_round, state, write_yaml


def refused(fn, root, *a, expect_next=None):
    with pytest.raises(StateError) as ex:
        fn(root, *a)
    if expect_next:
        assert ex.value.next_step.startswith(expect_next), ex.value.message
    return ex.value


def test_init_twice_refused(root):
    refused(api.init, root, expect_next="hypothesize")


def test_16_submit_on_failed_plan(root):
    api.hypothesize(root, hyp(root))
    bad = plan(root); bad["picks"][3]["candidate"] = bad["picks"][2]["candidate"]
    with pytest.raises(HarnessError):
        api.plan(root, bad)
    refused(api.submit, root, expect_next="plan")


def test_submit_before_plan(root):
    refused(api.submit, root, expect_next="hypothesize")
    api.hypothesize(root, hyp(root))
    refused(api.submit, root, expect_next="plan")


def test_collect_before_submit(root):
    api.hypothesize(root, hyp(root)); api.plan(root, plan(root))
    refused(api.collect, root, expect_next="submit")


def test_review_before_collect(root):
    api.hypothesize(root, hyp(root)); api.plan(root, plan(root)); api.submit(root)
    refused(api.review, root, expect_next="collect")


def test_17_hypothesize_with_previous_round_unreviewed(root):
    api.hypothesize(root, hyp(root)); api.plan(root, plan(root)); api.submit(root); api.collect(root)
    e = refused(api.hypothesize, root, hyp(root, hid="H2"), expect_next="review")
    assert "not reviewed" in e.message
    # also blocked once a plan is locked (before submit)
    api.review(root)
    api.hypothesize(root, hyp(root, hid="H2")); api.plan(root, plan(root))
    refused(api.hypothesize, root, hyp(root, hid="H3"), expect_next="submit")


def test_finalize_with_unreviewed_round(root):
    run_round(root)
    refused(api.finalize, root, ["C-001"], expect_next="hypothesize")
    api.hypothesize(root, hyp(root, hid="H2")); api.plan(root, plan(root)); api.submit(root)
    refused(api.finalize, root, ["C-001"], expect_next="collect")


def test_submit_twice_refused(root):
    api.hypothesize(root, hyp(root)); api.plan(root, plan(root)); api.submit(root)
    refused(api.submit, root, expect_next="collect")
    refused(api.plan, root, plan(root), expect_next="collect")


def test_collect_not_ready_is_not_an_error(tmp_path):
    r = write_yaml(tmp_path / "c", backend_options={"collect_delay": 1, "pool_size": 60, "start_size": 12})
    api.init(r)
    api.hypothesize(r, hyp(r)); api.plan(r, plan(r)); api.submit(r)
    out = api.collect(r)
    assert out["ready"] is False
    assert state(r).step() == "collect"
    out = api.collect(r)
    assert out["ready"] is True and state(r).step() == "review"


def test_19_failed_well_not_scored_and_returns_to_pool(tmp_path):
    r = write_yaml(tmp_path / "c", backend_options={"fail_rate": 0.6, "pool_size": 60, "start_size": 12}, batch_size=6, budget_experiments=18)
    api.init(r)
    api.hypothesize(r, hyp(r)); api.plan(r, plan(r)); api.submit(r)
    out = api.collect(r)
    assert out["failed"] > 0, "fixture must produce at least one failed well"
    rev = api.review(r)["review"]
    failed = [p for p in rev["per_pick"] if p["failed"]]
    assert failed and all(p["observed"] is None and p["in_interval"] is None for p in failed)
    # never scored as a value: calibration n counts only successes, batch mean excludes failures
    assert rev["calibration_to_date"]["n"] == len(rev["per_pick"]) - len(failed)
    st = state(r)
    for p in failed:
        assert p["candidate"] not in st.measured, "a failed candidate must return to the pool"
    # and cannot be cited as evidence
    api.hypothesize(r, hyp(r, hid="H2"))
    with pytest.raises(HarnessError) as ex:
        api.hypothesize(r, hyp(r, hid="H3", evidence=[failed[0]["candidate"], sorted(st.measured)[0]]))
    assert ex.value.rule == "evidence_failed_well"
    # but it can be picked again next round
    target = failed[0]["candidate"]
    def mut(picks):
        for q in picks:
            if q["candidate"] == target:
                q["candidate"] = picks[2]["candidate"]
        picks[2]["candidate"] = target
        return picks
    assert api.plan(r, plan(r, mut))["ok"]
    assert state(r).valid_plan(2)["picks"][2]["candidate"] == target


def test_review_verdicts_are_mechanical(root):
    # H1 cannot fire (threshold below 0); H2 fires on anything; H3 has an empty-this-round scope
    ids = [p["id"] for p in state(root).observations and __import__("conftest").pool(root)]
    never = hyp(root, hid="H1", kill_criterion={"threshold": -1.0, "count": 1, "scope": {"all": [{"feature": "size", "op": "ge", "value": 0}]}})
    always = hyp(root, hid="H2", kill_criterion={"threshold": 2.0, "count": 1, "scope": {"all": [{"feature": "size", "op": "ge", "value": 0}]}})
    measured_only = hyp(root, hid="H3", kill_criterion={"threshold": 2.0, "count": 1, "scope": {"candidates": sorted(state(root).measured)[:2]}})
    api.hypothesize(root, [never, always, measured_only])
    api.plan(root, plan(root)); api.submit(root); api.collect(root)
    ph = api.review(root)["review"]["per_hypothesis"]
    assert ph["H1"]["verdict"] == "supported" and ph["H1"]["trigger"] is None
    assert ph["H2"]["verdict"] == "killed" and "count=1" in ph["H2"]["trigger"]
    assert ph["H3"]["verdict"] == "untested"
    st = state(root)
    assert st.hypotheses["H2"]["status"] == "killed" and st.hypotheses["H2"]["close_verdict"] == "killed"
    killed_entry = [e for e in st.entries_of("hypothesis") if e["body"]["id"] == "H2"][-1]
    assert killed_entry["actor"] == "review"


def test_weakened_verdict(root):
    # count=3 on a scope that will take hits but not enough in one round of 4 picks (3 hypothesis picks)
    h = hyp(root, kill_criterion={"threshold": 2.0, "count": 50, "scope": {"all": [{"feature": "size", "op": "ge", "value": 0}]}})
    api.hypothesize(root, h)
    api.plan(root, plan(root)); api.submit(root); api.collect(root)
    ph = api.review(root)["review"]["per_hypothesis"]
    assert ph["H1"]["verdict"] == "weakened" and "of 50" in ph["H1"]["trigger"]


def test_shadow_baselines_logged_and_scored(root):
    run_round(root)
    sub = state(root).submission(1)
    assert set(sub["shadow"]) == {"random", "bayes_opt"}
    assert len(sub["shadow"]["random"]) == 4 and len(sub["shadow"]["bayes_opt"]) == 4
    sh = state(root).review(1)["shadow"]
    assert sh["agent_batch_mean"] is not None and sh["random_batch_mean"] is not None and sh["bayes_opt_batch_mean"] is not None


def test_end_to_end_campaign(root):
    """Full campaign on fake.py: valid ranking, complete and unbroken ledger."""
    for _ in range(3):
        run_round(root)
    st = state(root)
    assert st.step() == "finalize" and st.budget_left == 0
    ranking = sorted(st.measured, key=lambda c: -st.measured[c].values["activity"])
    out = api.finalize(root, ranking)
    assert out["ok"] and out["score"]["scored_by"] == "oracle" and 0 <= out["score"]["precision_at_k"] <= 1
    led = Ledger(root / "ledger")
    ok, _ = led.verify(); assert ok
    kinds = [e["kind"] for e in led.entries()]
    for r in (1, 2, 3):
        assert st.valid_plan(r) and st.submission(r) and st.result(r) and st.review(r)
    assert kinds.count("round_review") == 3 and kinds[-1] == "final"
    assert kinds.count("observation") == 12 + 12
    refused(api.hypothesize, root, hyp(root, hid="H9"), expect_next="done")


def test_finalize_rejects_bad_ranking(root):
    for _ in range(3):
        run_round(root)
    with pytest.raises(HarnessError) as ex:
        api.finalize(root, ["C-001", "C-001"])
    assert ex.value.rule == "deliverable"
    with pytest.raises(HarnessError):
        api.finalize(root, ["C-999"])
    with pytest.raises(HarnessError):
        api.finalize(root, {"not": "a list"})


def test_model_deliverable(tmp_path):
    r = write_yaml(tmp_path / "c", deliverable="model", rounds=1, budget_experiments=4)
    api.init(r); run_round(r)
    from conftest import pool
    with pytest.raises(HarnessError):
        api.finalize(r, {"C-000": 0.5})
    out = api.finalize(r, {p["id"]: 0.5 for p in pool(r)})
    assert out["score"]["scored_by"] == "oracle" and "rmse" in out["score"]
