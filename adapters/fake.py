"""Deterministic synthetic backend for tests. Seeded; no I/O beyond state_dir.

backend_options (all optional):
  pool_size: 300        number of candidates
  start_size: 24        size of the starting batch
  fail_rate: 0.0        fraction of wells that come back failed
  collect_delay: 0      how many collect() calls return None before results
  noise_sd: 0.0         measurement noise
"""
from __future__ import annotations

import hashlib
import math
import random

from .base import Adapter

RESIDUES = ["A", "L", "V", "F", "I", "D", "K", "S", "G", "W"]
HYDROPHOBIC = {"A", "L", "V", "F", "I", "W"}


def _unit(key: str) -> float:
    """Deterministic float in [0,1) from a string key."""
    h = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


class FakeAdapter(Adapter):
    metrics = ["activity"]
    maximize = True

    def __init__(self, campaign):
        super().__init__(campaign)
        o = campaign.backend_options
        self.pool_size = int(o.get("pool_size", 300))
        self.start_size = int(o.get("start_size", 24))
        self.fail_rate = float(o.get("fail_rate", 0.0))
        self.collect_delay = int(o.get("collect_delay", 0))
        self.noise_sd = float(o.get("noise_sd", 0.0))
        self.seed = campaign.seed
        self._pool = self._make_pool()

    # ---- synthetic world -------------------------------------------------
    def _make_pool(self) -> list[dict]:
        rng = random.Random(self.seed)
        pool = []
        for i in range(self.pool_size):
            res47 = rng.choice(RESIDUES)
            feats = {
                "res47": res47,
                "res47_hydrophobic": res47 in HYDROPHOBIC,
                "hydro47": round(rng.uniform(-1, 1) + (0.6 if res47 in HYDROPHOBIC else -0.4), 3),
                "charge": round(rng.uniform(-2, 2), 3),
                "size": round(rng.uniform(0, 1), 3),
                "flex": round(rng.uniform(0, 1), 3),
            }
            pool.append({"id": f"C-{i:03d}", "features": feats})
        return pool

    def _truth(self, cid: str) -> float:
        f = next(c for c in self._pool if c["id"] == cid)["features"]
        z = (1.6 * f["hydro47"] - 0.9 * abs(f["charge"]) + 1.2 * f["size"]
             - 2.0 * (f["size"] - 0.6) ** 2 + 0.3 * f["flex"] - 0.2)
        return round(1 / (1 + math.exp(-z)), 4)

    def _measure(self, cid: str, round_no: int) -> dict:
        key = f"{self.seed}:{round_no}:{cid}"
        if self.fail_rate > 0 and _unit("fail:" + key) < self.fail_rate:
            return {"candidate": cid, "values": {}, "failed": True, "note": "simulated well failure"}
        v = self._truth(cid)
        if self.noise_sd > 0:
            v += random.Random(key).gauss(0, self.noise_sd)
        return {"candidate": cid, "values": {"activity": round(min(max(v, 0.0), 1.0), 4)}, "failed": False}

    # ---- interface -------------------------------------------------------
    def load_start(self) -> list[dict]:
        rng = random.Random(self.seed + 1)
        ids = [c["id"] for c in rng.sample(self._pool, self.start_size)]
        return [self._measure(cid, 0) for cid in ids]

    def pool(self) -> list[dict]:
        return [dict(c, features=dict(c["features"])) for c in self._pool]

    def submit(self, round_no: int, candidates: list[str]) -> str:
        handle = f"fake-r{round_no}"
        self._save(handle, {"round": round_no, "candidates": list(candidates), "polls": 0})
        return handle

    def collect(self, handle: str) -> dict | None:
        st = self._load(handle)
        if st is None:
            raise KeyError(f"unknown handle {handle!r}")
        if st["polls"] < self.collect_delay:
            st["polls"] += 1
            self._save(handle, st)
            return None
        return {
            "handle": handle,
            "measurements": [self._measure(c, st["round"]) for c in st["candidates"]],
        }

    def oracle(self, candidates: list[str]) -> dict[str, float]:
        return {c: self._truth(c) for c in candidates}
