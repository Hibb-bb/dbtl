"""Plain Bayesian optimization: GP (RBF + noise) on featurized candidates, UCB
acquisition, greedy batch with a constant-liar penalty. numpy only."""
from __future__ import annotations

import numpy as np


def featurize(pool: list[dict]) -> tuple[dict[str, np.ndarray], list[str]]:
    keys = sorted({k for c in pool for k in c["features"]})
    cols: list[tuple[str, object]] = []
    for k in keys:
        vals = [c["features"].get(k) for c in pool]
        if all(isinstance(v, (int, float, bool)) or v is None for v in vals):
            cols.append((k, None))
        else:
            for lvl in sorted({str(v) for v in vals}):
                cols.append((k, lvl))
    X = {}
    for c in pool:
        row = []
        for k, lvl in cols:
            v = c["features"].get(k)
            if lvl is None:
                row.append(float(v) if v is not None else 0.0)
            else:
                row.append(1.0 if str(v) == lvl else 0.0)
        X[c["id"]] = np.array(row, dtype=float)
    return X, [f"{k}={lvl}" if lvl is not None else k for k, lvl in cols]


def _rbf(A: np.ndarray, B: np.ndarray, ls: float) -> np.ndarray:
    d2 = ((A[:, None, :] - B[None, :, :]) ** 2).sum(-1)
    return np.exp(-0.5 * d2 / ls**2)


def bayes_opt_pick(pool: list[dict], measured: dict[str, float], k: int, seed: int, maximize: bool = True,
                   kappa: float = 2.0, noise: float = 1e-2) -> list[str]:
    feats, _ = featurize(pool)
    ids = sorted(feats)
    Xall = np.stack([feats[i] for i in ids])
    mu, sd = Xall.mean(0), Xall.std(0) + 1e-9
    Z = (Xall - mu) / sd
    idx = {cid: i for i, cid in enumerate(ids)}
    avail = [c for c in ids if c not in measured]
    if not avail:
        return []
    train = [c for c in ids if c in measured]
    if len(train) < 2:
        rng = np.random.default_rng(seed)
        return list(rng.choice(avail, size=min(k, len(avail)), replace=False))
    y = np.array([measured[c] for c in train], dtype=float)
    if not maximize:
        y = -y
    ym, ys = y.mean(), y.std() + 1e-9
    yn = (y - ym) / ys
    Xt = Z[[idx[c] for c in train]]
    ls = max(np.sqrt(Z.shape[1]) * 0.75, 1e-3)
    chosen: list[str] = []
    Xt_cur, yn_cur = Xt.copy(), yn.copy()
    for _ in range(min(k, len(avail))):
        K = _rbf(Xt_cur, Xt_cur, ls) + noise * np.eye(len(Xt_cur))
        L = np.linalg.cholesky(K)
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, yn_cur))
        cand = [c for c in avail if c not in chosen]
        Xc = Z[[idx[c] for c in cand]]
        Ks = _rbf(Xc, Xt_cur, ls)
        mean = Ks @ alpha
        v = np.linalg.solve(L, Ks.T)
        var = np.clip(1.0 - (v**2).sum(0), 1e-12, None)
        ucb = mean + kappa * np.sqrt(var)
        best = cand[int(np.argmax(ucb))]
        chosen.append(best)
        # constant liar: pretend we observed the current max
        Xt_cur = np.vstack([Xt_cur, Z[idx[best]]])
        yn_cur = np.append(yn_cur, yn_cur.max())
    return chosen
