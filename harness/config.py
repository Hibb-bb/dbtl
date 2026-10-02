from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import HarnessError

BACKENDS = {"fake", "v0_replay", "v1_twin"}
DELIVERABLES = {"ranking", "model", "designs"}
BASELINES = {"random", "bayes_opt"}

TOP_KEYS = {
    "task", "backend", "seed", "rounds", "batch_size", "budget_experiments",
    "hypotheses", "batch_rules", "deliverable", "shadow_baselines", "backend_options",
}
HYP_KEYS = {"max_active", "min_evidence", "require_kill_number", "reopen_requires_new_evidence"}
BATCH_KEYS = {"explore_floor", "discriminate_floor", "no_repeat_measured", "require_interval"}


@dataclass(frozen=True)
class HypothesisRules:
    max_active: int
    min_evidence: int
    require_kill_number: bool
    reopen_requires_new_evidence: bool


@dataclass(frozen=True)
class BatchRules:
    explore_floor: float
    discriminate_floor: float
    no_repeat_measured: bool
    require_interval: bool

    def explore_min(self, batch_size: int) -> int:
        return math.ceil(self.explore_floor * batch_size - 1e-9)

    def discriminate_min(self, batch_size: int) -> int:
        return math.ceil(self.discriminate_floor * batch_size - 1e-9)


@dataclass(frozen=True)
class Campaign:
    root: Path
    task: str
    backend: str
    seed: int
    rounds: int
    batch_size: int
    budget_experiments: int
    hypotheses: HypothesisRules
    batch_rules: BatchRules
    deliverable: str
    shadow_baselines: tuple[str, ...]
    backend_options: dict = field(default_factory=dict)

    @property
    def ledger_dir(self) -> Path:
        return self.root / "ledger"

    @property
    def state_dir(self) -> Path:
        return self.root / ".harness"


def _require(d: dict, key: str, typ, where: str):
    if key not in d:
        raise HarnessError("config", f"{where}: missing required key '{key}'")
    v = d[key]
    if typ is float:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    elif typ is int:
        ok = isinstance(v, int) and not isinstance(v, bool)
    else:
        ok = isinstance(v, typ)
    if not ok:
        raise HarnessError("config", f"{where}: '{key}' must be {typ.__name__}, got {v!r}")
    return v


def _unknown(d: dict, allowed: set, where: str):
    extra = sorted(set(d) - allowed)
    if extra:
        raise HarnessError("config", f"{where}: unknown key(s) {extra}. Unknown keys are an error.")


def load_campaign(root: Path | str, path: Path | None = None) -> Campaign:
    root = Path(root).resolve()
    path = path or (root / "campaign.yaml")
    if not path.exists():
        raise HarnessError("config", f"campaign file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise HarnessError("config", "campaign.yaml must be a mapping")
    _unknown(raw, TOP_KEYS, "campaign.yaml")

    task = _require(raw, "task", str, "campaign.yaml")
    backend = _require(raw, "backend", str, "campaign.yaml")
    if backend not in BACKENDS:
        raise HarnessError("config", f"backend must be one of {sorted(BACKENDS)}, got {backend!r}")
    seed = _require(raw, "seed", int, "campaign.yaml")
    rounds = _require(raw, "rounds", int, "campaign.yaml")
    batch_size = _require(raw, "batch_size", int, "campaign.yaml")
    budget = _require(raw, "budget_experiments", int, "campaign.yaml")
    if rounds < 1 or batch_size < 1 or budget < 1:
        raise HarnessError("config", "rounds, batch_size and budget_experiments must be >= 1")

    h = _require(raw, "hypotheses", dict, "campaign.yaml")
    _unknown(h, HYP_KEYS, "campaign.yaml.hypotheses")
    hyp = HypothesisRules(
        max_active=_require(h, "max_active", int, "hypotheses"),
        min_evidence=_require(h, "min_evidence", int, "hypotheses"),
        require_kill_number=_require(h, "require_kill_number", bool, "hypotheses"),
        reopen_requires_new_evidence=_require(h, "reopen_requires_new_evidence", bool, "hypotheses"),
    )
    if hyp.max_active < 1 or hyp.min_evidence < 1:
        raise HarnessError("config", "hypotheses.max_active and min_evidence must be >= 1")

    b = _require(raw, "batch_rules", dict, "campaign.yaml")
    _unknown(b, BATCH_KEYS, "campaign.yaml.batch_rules")
    br = BatchRules(
        explore_floor=float(_require(b, "explore_floor", float, "batch_rules")),
        discriminate_floor=float(_require(b, "discriminate_floor", float, "batch_rules")),
        no_repeat_measured=_require(b, "no_repeat_measured", bool, "batch_rules"),
        require_interval=_require(b, "require_interval", bool, "batch_rules"),
    )
    for name in ("explore_floor", "discriminate_floor"):
        v = getattr(br, name)
        if not (0.0 <= v <= 1.0):
            raise HarnessError("config", f"batch_rules.{name} must be in [0, 1]")
    if br.explore_min(batch_size) + br.discriminate_min(batch_size) > batch_size:
        raise HarnessError("config", "explore_floor + discriminate_floor exceed the batch size")

    deliverable = _require(raw, "deliverable", str, "campaign.yaml")
    if deliverable not in DELIVERABLES:
        raise HarnessError("config", f"deliverable must be one of {sorted(DELIVERABLES)}")
    sb = raw.get("shadow_baselines", [])
    if not isinstance(sb, list) or any(s not in BASELINES for s in sb):
        raise HarnessError("config", f"shadow_baselines must be a list drawn from {sorted(BASELINES)}")
    opts = raw.get("backend_options", {}) or {}
    if not isinstance(opts, dict):
        raise HarnessError("config", "backend_options must be a mapping")

    return Campaign(
        root=root, task=task, backend=backend, seed=seed, rounds=rounds,
        batch_size=batch_size, budget_experiments=budget, hypotheses=hyp,
        batch_rules=br, deliverable=deliverable, shadow_baselines=tuple(sb),
        backend_options=opts,
    )
