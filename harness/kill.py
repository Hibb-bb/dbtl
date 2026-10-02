"""Kill criteria: parse once at registration, evaluate mechanically at review.

Structured form (the `text` field is for humans and is never evaluated):

    {"text": "...", "metric": "activity", "comparator": "lt", "threshold": 0.45,
     "count": 2, "scope": {"all": [{"feature": "res47_hydrophobic", "op": "eq", "value": true}]}}

or scope = {"candidates": ["C-221", "C-233"]}.

The criterion fires when at least `count` in-scope, non-failed observations
measured since the hypothesis was (re)opened satisfy  value <comparator> threshold.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass

from .errors import HarnessError

_CMP = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}
_SYM = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}
_PRED = {
    "eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "ge": operator.ge,
    "lt": operator.lt, "le": operator.le, "in": lambda a, b: a in b,
}


@dataclass(frozen=True)
class KillCriterion:
    metric: str
    comparator: str
    threshold: float
    count: int
    scope: dict

    def hit(self, values: dict) -> bool:
        v = values.get(self.metric)
        return v is not None and _CMP[self.comparator](v, self.threshold)

    def in_scope(self, candidate: str, features: dict | None) -> bool:
        if "candidates" in self.scope:
            return candidate in self.scope["candidates"]
        if features is None:
            return False
        for p in self.scope["all"]:
            if p["feature"] not in features:
                return False
            try:
                if not _PRED[p["op"]](features[p["feature"]], p["value"]):
                    return False
            except TypeError:
                return False
        return True

    def describe(self) -> str:
        return f"{self.count}+ in-scope observations with {self.metric} {_SYM[self.comparator]} {self.threshold}"


def parse_kill_criterion(kc: dict, metrics: list[str], pool: list[dict]) -> KillCriterion:
    """Validate the structured fields against the backend's metrics and pool. Raises HarnessError."""
    if kc.get("metric") not in metrics:
        raise HarnessError("kill_criterion_structure",
                           f"kill_criterion.metric {kc.get('metric')!r} is not a backend metric; choose from {metrics}")
    if not isinstance(kc.get("threshold"), (int, float)) or isinstance(kc.get("threshold"), bool):
        raise HarnessError("kill_criterion_structure", "kill_criterion.threshold must be a number; prose alone is not a kill criterion")
    crit = KillCriterion(kc["metric"], kc["comparator"], float(kc["threshold"]), int(kc["count"]), kc["scope"])

    pool_ids = {c["id"] for c in pool}
    if "candidates" in crit.scope:
        missing = [c for c in crit.scope["candidates"] if c not in pool_ids]
        if missing:
            raise HarnessError("kill_scope_unknown_candidate", f"kill_criterion.scope names unknown candidate(s) {missing}")
    else:
        feature_names = set()
        for c in pool:
            feature_names.update(c["features"].keys())
        for p in crit.scope["all"]:
            if p["feature"] not in feature_names:
                raise HarnessError("kill_scope_unknown_feature",
                                   f"kill_criterion.scope uses unknown feature {p['feature']!r}; pool features are {sorted(feature_names)}")
    n_scope = sum(1 for c in pool if crit.in_scope(c["id"], c["features"]))
    if n_scope == 0:
        raise HarnessError("kill_scope_empty", "kill_criterion.scope matches no candidate in the pool; the hypothesis could never be killed")
    if crit.count > n_scope:
        raise HarnessError("kill_scope_empty",
                           f"kill_criterion.count={crit.count} exceeds the {n_scope} candidate(s) in scope; the hypothesis could never be killed")
    return crit


def evaluate(crit: KillCriterion, observations, features_by_id: dict[str, dict], since_round: int, this_round: int):
    """Return (verdict, trigger). observations: iterable of Observation."""
    hits, tested_this_round = [], 0
    for o in observations:
        if o.failed or o.round < since_round:
            continue
        if not crit.in_scope(o.candidate, features_by_id.get(o.candidate)):
            continue
        if o.round == this_round:
            tested_this_round += 1
        if crit.hit(o.values):
            hits.append(o)
    if len(hits) >= crit.count:
        listing = ", ".join(f"{o.candidate}={o.values[crit.metric]}" for o in hits)
        return "killed", f"{len(hits)} in-scope observation(s) with {crit.metric} {_SYM[crit.comparator]} {crit.threshold} ({listing}); criterion count={crit.count}"
    if tested_this_round == 0:
        return "untested", None
    if hits:
        listing = ", ".join(f"{o.candidate}={o.values[crit.metric]}" for o in hits)
        return "weakened", f"{len(hits)} of {crit.count} needed hit(s) so far ({listing})"
    return "supported", None
