from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import api  # noqa: E402
from harness.config import load_campaign  # noqa: E402
from harness.state import load_state  # noqa: E402

BASE_YAML = {
    "task": "protein_active_learning", "backend": "fake", "seed": 7,
    "rounds": 3, "batch_size": 4, "budget_experiments": 12,
    "hypotheses": {"max_active": 3, "min_evidence": 2, "require_kill_number": True, "reopen_requires_new_evidence": True},
    "batch_rules": {"explore_floor": 0.25, "discriminate_floor": 0.25, "no_repeat_measured": True, "require_interval": True},
    "deliverable": "ranking", "shadow_baselines": ["random", "bayes_opt"],
    "backend_options": {"pool_size": 60, "start_size": 12},
}


def write_yaml(root: Path, **over) -> Path:
    import yaml
    cfg = json.loads(json.dumps(BASE_YAML))
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    root.mkdir(parents=True, exist_ok=True)
    (root / "campaign.yaml").write_text(yaml.safe_dump(cfg))
    return root


@pytest.fixture
def root(tmp_path):
    r = write_yaml(tmp_path / "camp")
    api.init(r)
    return r


def state(root):
    return load_state(load_campaign(root))


def pool(root):
    return api._adapter(load_campaign(root)).pool()


def measured_ids(root, n=2):
    st = state(root)
    return sorted(st.measured)[:n]


def hyp(root, hid="H1", evidence=None, **over) -> dict:
    ev = evidence if evidence is not None else measured_ids(root, 2)
    h = {
        "id": hid,
        "statement": "activity rises with hydrophobicity at site 47",
        "reason": "site 47 sits in the binding pocket; buried residues favor nonpolar contacts",
        "evidence": ev,
        "prediction": "hydrophobic-47 variants score above 0.5 on activity",
        "kill_criterion": {
            "text": "two or more hydrophobic-47 variants score below 0.3",
            "metric": "activity", "comparator": "lt", "threshold": 0.3, "count": 2,
            "scope": {"all": [{"feature": "res47_hydrophobic", "op": "eq", "value": True}]},
        },
        "confidence": 0.55,
        "status": "active",
    }
    for k, v in over.items():
        if k == "kill_criterion":
            h["kill_criterion"].update(v)
        else:
            h[k] = v
    return h


def plan(root, picks_override=None, n=None) -> dict:
    """A valid plan: 1 explore, 1 discriminate, rest exploit; hypotheses disagree on every pick."""
    st = state(root)
    c = st.campaign
    n = n or c.batch_size
    active = sorted(st.active_hypotheses)
    unmeasured = sorted(p["id"] for p in pool(root) if p["id"] not in st.measured)
    picks = []
    ne, nd = c.batch_rules.explore_min(n), c.batch_rules.discriminate_min(n)
    for i, cid in enumerate(unmeasured[:n]):
        other = unmeasured[(i + 1) % len(unmeasured)]
        pbh = {h: round(0.5 + 0.1 * j + 0.01 * i, 3) for j, h in enumerate(active)}
        if i < ne:
            role, serves = "explore", None
            pred = 0.4
        elif i < ne + nd:
            role, serves = "discriminate", active[0]
            pred = pbh[serves]
        else:
            role, serves = "exploit", active[i % len(active)]
            pred = pbh[serves]
        picks.append({
            "candidate": cid, "serves": serves, "role": role, "predicted": pred,
            "interval": [round(pred - 0.2, 3), round(pred + 0.2, 3)],
            "why_this_one": f"{serves or 'no hypothesis'} predicts {pred}; larger gap than {other}",
            "predictions_by_hypothesis": pbh,
        })
    if picks_override:
        picks = picks_override(picks)
    return {"round": st.round, "picks": picks}


def run_round(root, hypotheses=None):
    if hypotheses is None:
        hypotheses = [hyp(root)] if not state(root).active_hypotheses else [dict(state(root).active_hypotheses[h], round_closed=None, close_verdict=None) for h in state(root).active_hypotheses]
        for h in hypotheses:
            h.pop("round_opened", None)
    api.hypothesize(root, hypotheses)
    api.plan(root, plan(root))
    api.submit(root)
    api.collect(root)
    return api.review(root)
