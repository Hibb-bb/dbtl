"""v1 digital twin: a simulator over the recorded dataset with noise, failures and a queue.

backend_options:
  dataset: path            (default data/<task>.json)
  noise_sd: 0.05           gaussian noise on every metric
  fail_rate: 0.05          probability a well fails
  queue_delay: 1           collect() calls that return None before results arrive
  clip: [0, 1]             clip noisy values to this range (null to disable)

Noise and failures are seeded per (seed, round, candidate), so a campaign replays
identically but a re-submitted failed well gets a fresh draw next round.
"""
from __future__ import annotations

import hashlib
import random

from .base import Adapter
from .dataset import load_dataset


def _unit(key: str) -> float:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


class TwinAdapter(Adapter):
    def __init__(self, campaign):
        super().__init__(campaign)
        self.data = load_dataset(campaign)
        self.metrics = list(self.data["metrics"])
        self.maximize = bool(self.data["maximize"])
        self.by_id = {c["id"]: c for c in self.data["candidates"]}
        o = campaign.backend_options
        self.noise_sd = float(o.get("noise_sd", 0.05))
        self.fail_rate = float(o.get("fail_rate", 0.05))
        self.queue_delay = int(o.get("queue_delay", 1))
        self.clip = o.get("clip", [0.0, 1.0])
        self.seed = campaign.seed

    def _truth(self, cid: str) -> dict | None:
        m = self.by_id[cid].get("measured")
        if not m or m.get("failed"):
            return None
        return {k: m[k] for k in self.metrics}

    def _measure(self, cid: str, round_no: int) -> dict:
        key = f"twin:{self.seed}:{round_no}:{cid}"
        if self.fail_rate > 0 and _unit("fail:" + key) < self.fail_rate:
            return {"candidate": cid, "values": {}, "failed": True, "note": "simulated instrument fault"}
        truth = self._truth(cid)
        if truth is None:
            return {"candidate": cid, "values": {}, "failed": True, "note": "no recorded value to simulate from"}
        rng = random.Random(key)
        out = {}
        for k, v in truth.items():
            x = v + rng.gauss(0, self.noise_sd)
            if self.clip:
                x = min(max(x, self.clip[0]), self.clip[1])
            out[k] = round(x, 4)
        return {"candidate": cid, "values": out, "failed": False}

    def load_start(self) -> list[dict]:
        return [self._measure(cid, 0) for cid in self.data["start"]]

    def pool(self) -> list[dict]:
        return [{"id": c["id"], "features": dict(c["features"])} for c in self.data["candidates"]]

    def submit(self, round_no: int, candidates: list[str]) -> str:
        unknown = [c for c in candidates if c not in self.by_id]
        if unknown:
            raise KeyError(f"unknown candidates {unknown}")
        handle = f"twin-r{round_no}"
        self._save(handle, {"round": round_no, "candidates": list(candidates), "polls": 0})
        return handle

    def collect(self, handle: str) -> dict | None:
        st = self._load(handle)
        if st is None:
            raise KeyError(f"unknown handle {handle!r}")
        if st["polls"] < self.queue_delay:
            st["polls"] += 1
            self._save(handle, st)
            return None
        return {"handle": handle, "measurements": [self._measure(c, st["round"]) for c in st["candidates"]]}

    def oracle(self, candidates: list[str]) -> dict[str, float] | None:
        """The twin knows the noiseless truth; expose it so shadow scoring is exact."""
        obj = self.objective
        out = {c: self._truth(c)[obj] for c in candidates if c in self.by_id and self._truth(c) is not None}
        return out
