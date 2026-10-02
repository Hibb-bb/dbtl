"""v0 replay and v1 twin adapters, plus the baselines."""
import json

import pytest

from harness import api
from harness.config import load_campaign

from conftest import REPO, hyp, plan, run_round, state, write_yaml

DATASET = REPO / "data" / "protein_active_learning.json"


def test_dataset_is_well_formed():
    d = json.loads(DATASET.read_text())
    assert d["metrics"] == ["activity"] and len(d["candidates"]) == 400 and len(d["start"]) == 24
    assert all("res47_hydrophobic" in c["features"] for c in d["candidates"])


def test_replay_is_exact_and_has_oracle(tmp_path):
    r = write_yaml(tmp_path / "c", backend="v0_replay", backend_options={"dataset": str(DATASET)})
    a = api._adapter(load_campaign(r))
    d = json.loads(DATASET.read_text())
    cid = [c for c in d["candidates"] if not c["measured"].get("failed")][0]
    h = a.submit(1, [cid["id"]])
    res = a.collect(h)
    assert res["measurements"][0]["values"]["activity"] == cid["measured"]["activity"]
    assert a.oracle([cid["id"]]) == {cid["id"]: cid["measured"]["activity"]}
    recorded_fail = [c for c in d["candidates"] if c["measured"].get("failed")][0]
    assert a.collect(a.submit(2, [recorded_fail["id"]]))["measurements"][0]["failed"] is True


def test_replay_full_campaign(tmp_path):
    r = write_yaml(tmp_path / "c", backend="v0_replay", backend_options={"dataset": str(DATASET)}, rounds=2, budget_experiments=8)
    api.init(r)
    run_round(r); run_round(r)
    st = state(r)
    ranking = sorted(st.measured, key=lambda c: -st.measured[c].values["activity"])
    out = api.finalize(r, ranking)
    assert out["score"]["scored_by"] == "oracle"
    rev = st.review(2)["shadow"]
    assert rev["random_batch_mean"] is not None and rev["bayes_opt_batch_mean"] is not None


def test_twin_adds_noise_failures_and_queue(tmp_path):
    r = write_yaml(tmp_path / "c", backend="v1_twin",
                   backend_options={"dataset": str(DATASET), "noise_sd": 0.1, "fail_rate": 0.3, "queue_delay": 2})
    a = api._adapter(load_campaign(r))
    d = json.loads(DATASET.read_text())
    ids = [c["id"] for c in d["candidates"] if not c["measured"].get("failed")][:40]
    h = a.submit(1, ids)
    assert a.collect(h) is None and a.collect(h) is None
    res = a.collect(h)
    ms = res["measurements"]
    assert any(m["failed"] for m in ms) and any(not m["failed"] for m in ms)
    truth = {c["id"]: c["measured"]["activity"] for c in d["candidates"] if not c["measured"].get("failed")}
    diffs = [abs(m["values"]["activity"] - truth[m["candidate"]]) for m in ms if not m["failed"]]
    assert max(diffs) > 0.01, "twin must perturb recorded values"
    # deterministic replay of the same campaign
    b = api._adapter(load_campaign(r))
    assert b.collect(h) == res
    # a failed well gets a fresh draw in a later round (not pinned to failing forever)
    failed = [m["candidate"] for m in ms if m["failed"]]
    h2 = a.submit(2, failed); a.collect(h2); a.collect(h2)
    again = a.collect(h2)["measurements"]
    assert not all(m["failed"] for m in again)


def test_twin_full_campaign_survives_queue_and_failures(tmp_path):
    r = write_yaml(tmp_path / "c", backend="v1_twin", rounds=2, budget_experiments=8,
                   backend_options={"dataset": str(DATASET), "fail_rate": 0.25, "queue_delay": 1})
    api.init(r)
    for _ in range(2):
        api.hypothesize(r, [hyp(r)] if not state(r).active_hypotheses else [hyp(r, hid="H2")])
        api.plan(r, plan(r)); api.submit(r)
        assert api.collect(r)["ready"] is False
        assert api.collect(r)["ready"] is True
        api.review(r)
    st = state(r)
    assert st.step() == "finalize"


def test_missing_dataset_is_clear(tmp_path):
    r = write_yaml(tmp_path / "c", backend="v0_replay", backend_options={"dataset": "nope.json"})
    with pytest.raises(FileNotFoundError):
        api.init(r)


def test_baselines_pick_unmeasured_and_distinct(root):
    from baselines import bayes_opt_pick, random_pick
    from conftest import pool
    st = state(root)
    p = pool(root)
    measured = {c: o.values["activity"] for c, o in st.measured.items()}
    for fn in (random_pick, bayes_opt_pick):
        picks = fn(p, measured, 5, seed=1)
        assert len(picks) == 5 and len(set(picks)) == 5 and not set(picks) & set(measured)
    assert bayes_opt_pick(p, measured, 5, seed=1) == bayes_opt_pick(p, measured, 5, seed=1)


def test_bayes_opt_beats_random_on_fake(tmp_path):
    """Sanity: BO's picks should on average score higher than random's under the oracle."""
    from baselines import bayes_opt_pick, random_pick
    r = write_yaml(tmp_path / "c", backend_options={"pool_size": 200, "start_size": 40})
    a = api._adapter(load_campaign(r))
    p = a.pool()
    measured = {m["candidate"]: m["values"]["activity"] for m in a.load_start() if not m["failed"]}
    bo = a.oracle(bayes_opt_pick(p, measured, 10, seed=3))
    rn = [a.oracle(random_pick(p, measured, 10, seed=s)) for s in range(20)]
    import statistics
    assert statistics.fmean(bo.values()) > statistics.fmean(statistics.fmean(x.values()) for x in rn)
