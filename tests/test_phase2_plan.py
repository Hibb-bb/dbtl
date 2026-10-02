"""Acceptance tests 8-15 (spec §14) plus the other §6.2 rules."""
import pytest

from harness import api
from harness.errors import HarnessError, StateError

from conftest import hyp, measured_ids, plan, state


@pytest.fixture
def ready(root):
    api.hypothesize(root, [hyp(root, hid="H1"), hyp(root, hid="H2")])
    return root


def rejected(root, p, rule):
    with pytest.raises(HarnessError) as ex:
        api.plan(root, p)
    assert ex.value.rule == rule, f"expected {rule}, got {ex.value}"
    rj = state(root).entries_of("rejection")
    assert rj and rj[-1]["body"]["rule"] == rule
    assert state(root).valid_plan(state(root).round) is None, "a rejected plan must not be stored as a plan"
    return ex.value.message


def test_happy_path(ready):
    out = api.plan(ready, plan(ready))
    assert out["ok"] and out["roles"] == {"exploit": 2, "discriminate": 1, "explore": 1}
    # batch_size 4, floors 0.25 -> exactly 1 explore and 1 discriminate required


def test_8_no_serves_and_not_explore(ready):
    def mut(picks):
        picks[0]["serves"] = None; picks[0]["role"] = "exploit"; return picks
    msg = rejected(ready, plan(ready, mut), "serves_or_explore")
    assert "pick 0" in msg


def test_8b_explore_with_serves(ready):
    def mut(picks):
        picks[1]["role"] = "explore"; return picks
    rejected(ready, plan(ready, mut), "serves_or_explore")


def test_8c_serves_inactive_hypothesis(ready):
    def mut(picks):
        picks[1]["serves"] = "H9"; picks[1]["predictions_by_hypothesis"]["H9"] = 0.5; return picks
    msg = rejected(ready, plan(ready, mut), "serves_inactive")
    assert "H9" in msg


def test_9_below_explore_floor(ready):
    def mut(picks):
        picks[0]["serves"] = "H1"; picks[0]["role"] = "exploit"; picks[0]["predicted"] = picks[0]["predictions_by_hypothesis"]["H1"]
        picks[0]["interval"] = [0, 1]; return picks
    msg = rejected(ready, plan(ready, mut), "explore_floor")
    assert "requires at least 1" in msg


def test_10_below_discriminate_floor(ready):
    def mut(picks):
        picks[1]["role"] = "exploit"; return picks
    msg = rejected(ready, plan(ready, mut), "discriminate_floor")
    assert "requires at least 1" in msg


def test_11_hypotheses_not_separated(ready):
    def mut(picks):
        for p in picks:
            p["predictions_by_hypothesis"]["H2"] = p["predictions_by_hypothesis"]["H1"]
            if p["serves"] == "H2":
                p["predicted"] = p["predictions_by_hypothesis"]["H2"]
        return picks
    msg = rejected(ready, plan(ready, mut), "separation")
    assert "H1" in msg and "H2" in msg


def test_11b_prediction_missing_for_active_hypothesis(ready):
    def mut(picks):
        del picks[2]["predictions_by_hypothesis"]["H2"]; return picks
    msg = rejected(ready, plan(ready, mut), "predictions_by_hypothesis")
    assert "H2" in msg


def test_11c_predicted_disagrees_with_serving_hypothesis(ready):
    def mut(picks):
        picks[1]["predicted"] = picks[1]["predictions_by_hypothesis"]["H1"] + 0.05
        picks[1]["interval"] = [0, 1]; return picks
    rejected(ready, plan(ready, mut), "predictions_by_hypothesis")


def test_12_already_measured_candidate(ready):
    done = measured_ids(ready, 1)[0]
    def mut(picks):
        picks[2]["candidate"] = done; return picks
    msg = rejected(ready, plan(ready, mut), "already_measured")
    assert done in msg


def test_13_duplicate_candidate(ready):
    def mut(picks):
        picks[3]["candidate"] = picks[2]["candidate"]; return picks
    msg = rejected(ready, plan(ready, mut), "duplicate_candidate")
    assert picks_name(msg)


def picks_name(msg):
    return "pick 3" in msg


def test_14_interval_excludes_prediction(ready):
    def mut(picks):
        picks[2]["interval"] = [picks[2]["predicted"] + 0.01, picks[2]["predicted"] + 0.3]; return picks
    msg = rejected(ready, plan(ready, mut), "interval")
    assert "outside its own interval" in msg


def test_14b_inverted_interval(ready):
    def mut(picks):
        picks[2]["interval"] = [0.9, 0.1]; return picks
    rejected(ready, plan(ready, mut), "interval")


def test_15_plan_before_any_hypothesis_this_round(root):
    with pytest.raises(StateError) as ex:
        api.plan(root, {"round": 1, "picks": []})
    assert "hypothesize" in ex.value.next_step


def test_wrong_batch_size(ready):
    rejected(ready, plan(ready, n=3), "batch_size")


def test_unknown_candidate(ready):
    def mut(picks):
        picks[2]["candidate"] = "C-999"; return picks
    rejected(ready, plan(ready, mut), "unknown_candidate")


def test_why_empty(ready):
    def mut(picks):
        picks[2]["why_this_one"] = "   "; return picks
    rejected(ready, plan(ready, mut), "why_this_one")


def test_why_compares_nothing(ready):
    def mut(picks):
        picks[2]["why_this_one"] = "looks promising and has high uncertainty"; return picks
    msg = rejected(ready, plan(ready, mut), "why_this_one")
    assert "compares nothing" in msg


def test_why_mentioning_only_itself_fails(ready):
    def mut(picks):
        picks[2]["why_this_one"] = f"{picks[2]['candidate']} is the best"; return picks
    rejected(ready, plan(ready, mut), "why_this_one")


def test_why_mentioning_hypothesis_passes(ready):
    def mut(picks):
        picks[2]["why_this_one"] = "H2 predicts a dip here"; return picks
    assert api.plan(ready, plan(ready, mut))["ok"]


def test_wrong_round(ready):
    p = plan(ready); p["round"] = 2
    rejected(ready, p, "plan_round")


def test_budget_exhausted(tmp_path):
    from conftest import write_yaml
    r = write_yaml(tmp_path / "c", budget_experiments=3)
    api.init(r)
    api.hypothesize(r, hyp(r))
    rejected(r, plan(r), "budget")


def test_replan_before_submit_replaces(ready):
    api.plan(ready, plan(ready))
    p2 = plan(ready)
    p2["picks"][2]["why_this_one"] = "H1 says so, replanned"
    api.plan(ready, p2)
    assert state(ready).valid_plan(1)["picks"][2]["why_this_one"] == "H1 says so, replanned"
