"""The adapter interface.

Measurement dict shape returned by `collect`:

    {
      "handle": "...",
      "measurements": [
        {"candidate": "C-221", "values": {"activity": 0.52}, "failed": false},
        {"candidate": "C-109", "values": {},               "failed": true, "note": "well dried"}
      ]
    }

A failed well is NOT a low value. The harness never scores it; the candidate
returns to the pool.

Adapters persist pending submissions under `state_dir` because each harness
script runs in its own process, and v2 results may arrive much later.
"""
from __future__ import annotations

import json
from pathlib import Path


class Adapter:
    #: metrics every measurement carries; the first one is the objective
    metrics: list[str] = ["activity"]
    #: True when a larger objective value is better
    maximize: bool = True

    def __init__(self, campaign):
        self.campaign = campaign
        self.state_dir: Path = campaign.state_dir / campaign.backend
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ---- the interface ---------------------------------------------------
    def load_start(self) -> list[dict]:
        """Return the starting batch: [{"candidate", "values": {metric: v}, "failed": False}, ...]."""
        raise NotImplementedError

    def pool(self) -> list[dict]:
        """Return all selectable candidates: [{"id", "features": {...}}, ...]."""
        raise NotImplementedError

    def submit(self, round_no: int, candidates: list[str]) -> str:
        """Send the batch. Return a submission handle."""
        raise NotImplementedError

    def collect(self, handle: str) -> dict | None:
        """Return measurements, or None if not ready yet."""
        raise NotImplementedError

    def oracle(self, candidates: list[str]) -> dict[str, float] | None:
        """Ground-truth objective values, when the backend knows them (v0). Else None."""
        return None

    @property
    def objective(self) -> str:
        return self.metrics[0]

    # ---- persistence helpers --------------------------------------------
    def _path(self, handle: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in handle)
        return self.state_dir / f"{safe}.json"

    def _save(self, handle: str, obj: dict) -> None:
        with open(self._path(handle), "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)

    def _load(self, handle: str) -> dict | None:
        p = self._path(handle)
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)
