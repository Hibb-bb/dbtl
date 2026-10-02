"""Shared dataset loader for the v0 replay and v1 twin backends.

Dataset file (JSON):
{
  "task": "protein_active_learning",
  "metrics": ["activity"],            # first is the objective
  "maximize": true,
  "start": ["C-014", ...],            # the provided starting batch
  "candidates": [
    {"id": "C-000", "features": {"res47": "L", "hydro47": 0.8, ...},
     "measured": {"activity": 0.71}}  # recorded ground truth; may be absent for v1-only pools
  ]
}
"""
from __future__ import annotations

import json
from pathlib import Path


def load_dataset(campaign) -> dict:
    opt = campaign.backend_options.get("dataset")
    path = Path(opt) if opt else Path("data") / f"{campaign.task}.json"
    if not path.is_absolute():
        for base in (campaign.root, Path(__file__).resolve().parent.parent):
            if (base / path).exists():
                path = base / path
                break
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path} (set backend_options.dataset in campaign.yaml)")
    with open(path) as f:
        d = json.load(f)
    for k in ("metrics", "start", "candidates"):
        if k not in d:
            raise ValueError(f"dataset {path} lacks required key {k!r}")
    ids = [c["id"] for c in d["candidates"]]
    if len(set(ids)) != len(ids):
        raise ValueError("dataset has duplicate candidate ids")
    missing = [s for s in d["start"] if s not in set(ids)]
    if missing:
        raise ValueError(f"dataset start batch names unknown candidates {missing}")
    d.setdefault("maximize", True)
    d["_path"] = str(path)
    return d
