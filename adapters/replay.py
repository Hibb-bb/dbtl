"""v0 replay oracle: results come from recorded experiments, replayed exactly."""
from __future__ import annotations

from .base import Adapter
from .dataset import load_dataset


class ReplayAdapter(Adapter):
    def __init__(self, campaign):
        super().__init__(campaign)
        self.data = load_dataset(campaign)
        self.metrics = list(self.data["metrics"])
        self.maximize = bool(self.data["maximize"])
        self.by_id = {c["id"]: c for c in self.data["candidates"]}
        unrecorded = [c["id"] for c in self.data["candidates"] if "measured" not in c]
        if unrecorded:
            raise ValueError(f"v0 replay needs a recorded measurement for every candidate; {len(unrecorded)} lack one, e.g. {unrecorded[:3]}")

    def _measure(self, cid: str) -> dict:
        m = self.by_id[cid]["measured"]
        if m.get("failed"):
            return {"candidate": cid, "values": {}, "failed": True, "note": m.get("note", "recorded failure")}
        return {"candidate": cid, "values": {k: m[k] for k in self.metrics}, "failed": False}

    def load_start(self) -> list[dict]:
        return [self._measure(cid) for cid in self.data["start"]]

    def pool(self) -> list[dict]:
        return [{"id": c["id"], "features": dict(c["features"])} for c in self.data["candidates"]]

    def submit(self, round_no: int, candidates: list[str]) -> str:
        unknown = [c for c in candidates if c not in self.by_id]
        if unknown:
            raise KeyError(f"unknown candidates {unknown}")
        handle = f"replay-r{round_no}"
        self._save(handle, {"round": round_no, "candidates": list(candidates)})
        return handle

    def collect(self, handle: str) -> dict | None:
        st = self._load(handle)
        if st is None:
            raise KeyError(f"unknown handle {handle!r}")
        return {"handle": handle, "measurements": [self._measure(c) for c in st["candidates"]]}

    def oracle(self, candidates: list[str]) -> dict[str, float]:
        obj = self.objective
        return {c: self.by_id[c]["measured"][obj] for c in candidates
                if c in self.by_id and not self.by_id[c]["measured"].get("failed")}
