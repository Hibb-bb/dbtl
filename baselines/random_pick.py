from __future__ import annotations

import random


def random_pick(pool: list[dict], measured: dict[str, float], k: int, seed: int, maximize: bool = True) -> list[str]:
    """Uniform random choice of k unmeasured candidates."""
    rng = random.Random(seed)
    avail = sorted(c["id"] for c in pool if c["id"] not in measured)
    return rng.sample(avail, min(k, len(avail)))
