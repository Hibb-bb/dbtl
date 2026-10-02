#!/usr/bin/env python3
"""A/B runner: the same campaign, run by Claude Code with and without the harness, over several seeds.

Arms
  harness   Claude Code + CLAUDE.md protocol + hypothesize/plan/review scripts + all three hooks
  control   Claude Code + plain raw_submit/raw_collect scripts + ledger-write guards only (no Stop hook)
  bo        scripted Bayesian optimization (baselines/bayes_opt), no LLM
  random    scripted uniform random picks, no LLM

Every arm gets the same backend, seed, budget, starting batch, pool.py, ledger and scoring.
Each run lives in its own workspace OUT/<arm>/seed<N>/, built from a copy of this repo. Keep OUT
outside the repo: Claude Code reads CLAUDE.md from parent directories, and the repo's CLAUDE.md
would leak the harness protocol into the control arm.

usage
  uv run python ab/run_ab.py --seeds 1-5                       # all four arms
  uv run python ab/run_ab.py --seeds 1,2 --arms harness,control --jobs 4 --model claude-opus-5-5
  uv run python ab/run_ab.py --out DIR --summarize             # re-tabulate an existing OUT
  uv run python ab/run_ab.py --seeds 1 --dry-run               # build workspaces, print commands
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = Path(__file__).resolve().parent / "templates"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

LLM_ARMS = ("harness", "control")
SCRIPTED_ARMS = ("bo", "random")
COMMON_DIRS = ("harness", "adapters", "baselines", "schemas", "data", "hooks")
COMMON_FILES = ("pyproject.toml", "uv.lock")
SCRIPTS = {
    "harness": ["_common", "init", "hypothesize", "plan", "submit", "collect", "review", "finalize", "status", "pool"],
    "control": ["_common", "raw_init", "raw_submit", "raw_collect", "raw_status", "finalize", "pool"],
}
SCRIPTS["bo"] = SCRIPTS["random"] = SCRIPTS["control"]

POOL_NOTE = """
## Seeing the pool

`uv run python scripts/pool.py [--measured|--unmeasured] [--json]` prints every candidate with its
features, joined with its latest successful measurement and observation id.
"""


# ---------------------------------------------------------------- workspaces
def _hooks(arm: str) -> dict:
    def cmd(interp, f):
        return f'f="${{CLAUDE_PROJECT_DIR:-.}}/hooks/{f}"; [ -f "$f" ] || f="hooks/{f}"; [ -f "$f" ] || exit 0; exec {interp} "$f"'
    hooks = {"PreToolUse": [
        {"matcher": "Write|Edit|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": cmd("python3", "guard_write.py")}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd("python3", "guard_bash.py")}]},
    ]}
    if arm == "harness":
        hooks["Stop"] = [{"hooks": [{"type": "command", "command": cmd("sh", "stop_guard.sh")}]}]
    return {"hooks": hooks}


def build_workspace(ws: Path, arm: str, seed: int, overrides: dict) -> None:
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "settings.json")
    for d in COMMON_DIRS:
        shutil.copytree(REPO / d, ws / d, ignore=ignore)
    for f in COMMON_FILES:
        shutil.copy2(REPO / f, ws / f)
    (ws / "scripts").mkdir()
    for s in SCRIPTS[arm]:
        shutil.copy2(REPO / "scripts" / f"{s}.py", ws / "scripts" / f"{s}.py")
    (ws / "ledger").mkdir()
    os.symlink(REPO / ".venv", ws / ".venv")

    camp = yaml.safe_load((REPO / "campaign.yaml").read_text())
    camp["seed"] = seed
    for k, v in overrides.items():
        camp[k] = v
    (ws / "campaign.yaml").write_text(yaml.safe_dump(camp, sort_keys=False))

    if arm == "harness":
        shutil.copy2(REPO / "README.md", ws / "README.md")  # CLAUDE.md points at it for examples
        (ws / "CLAUDE.md").write_text((REPO / "CLAUDE.md").read_text() + POOL_NOTE)
    elif arm == "control":
        shutil.copy2(TEMPLATES / "CLAUDE.control.md", ws / "CLAUDE.md")
    if arm in LLM_ARMS:
        (ws / ".claude").mkdir()
        (ws / ".claude" / "settings.json").write_text(json.dumps(_hooks(arm), indent=2) + "\n")


# ---------------------------------------------------------------- arms
def claude_cmd(args) -> list[str]:
    cmd = [args.claude, "-p", (TEMPLATES / "TASK.md").read_text(), "--output-format", "json",
           "--permission-mode", args.permission_mode,
           "--allowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep"]
    if args.model:
        cmd += ["--model", args.model]
    if args.max_budget_usd:
        cmd += ["--max-budget-usd", str(args.max_budget_usd)]
    for extra in args.claude_arg:
        cmd += extra.split()
    return cmd


def run_llm(ws: Path, args) -> dict:
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("DBTL_ROOT", None)
    t0 = time.time()
    try:
        p = subprocess.run(claude_cmd(args), cwd=ws, env=env, capture_output=True, text=True, timeout=args.timeout)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = "timeout", (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
    (ws / "claude.json").write_text(out or "")
    (ws / "claude.stderr").write_text(err or "")
    meta = {"exit": rc, "wall_s": round(time.time() - t0, 1)}
    try:
        j = json.loads(out)
        meta.update(cost_usd=j.get("total_cost_usd"), turns=j.get("num_turns"), is_error=j.get("is_error"))
    except (json.JSONDecodeError, TypeError):
        pass
    return meta


def run_scripted(ws: Path, arm: str) -> dict:
    from baselines import BASELINES
    from harness import api, control
    from harness.config import load_campaign
    from harness.state import load_state

    t0 = time.time()
    api.init(ws)
    c = load_campaign(ws)
    adapter = api._adapter(c)
    pool = adapter.pool()
    pick = BASELINES["bayes_opt" if arm == "bo" else "random"]
    for _ in range(c.rounds):
        st = load_state(c)
        measured = {cid: o.values[adapter.objective] for cid, o in st.measured.items()}
        # Offset from the shadow seed so the scripted random arm is not a copy of the shadow random picks.
        ids = pick(pool, measured, c.batch_size, seed=c.seed * 1000 + st.round + 7919, maximize=adapter.maximize)
        control.raw_submit(ws, [str(x) for x in ids])
        while not control.raw_collect(ws)["ready"]:
            time.sleep(0.05)
    st = load_state(c)
    ranking = [cid for cid, _ in sorted(((cid, o.values[adapter.objective]) for cid, o in st.measured.items()),
                                        key=lambda t: t[1], reverse=adapter.maximize)]
    api.finalize(ws, ranking)
    return {"exit": 0, "wall_s": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- scoring
def score_workspace(ws: Path) -> dict:
    from harness import api
    from harness.config import load_campaign
    from harness.ledger import Ledger
    from harness.state import load_state

    c = load_campaign(ws)
    out: dict = {"initialized": False, "finalized": False}
    led = Ledger(c.ledger_dir)
    ok, msg = led.verify()
    out["chain_ok"] = ok
    if not ok or not led.exists():
        return out
    st = load_state(c)
    adapter = api._adapter(c)
    obj, sign = adapter.objective, (1 if adapter.maximize else -1)
    pool_ids = [p["id"] for p in adapter.pool()]
    truth = adapter.oracle(pool_ids)
    out.update(initialized=True, finalized=st.finalized, rounds_done=st.rounds_reviewed,
               experiments_spent=st.experiments_spent)
    if truth is None:
        return out
    # Candidates without a recorded truth (twin/replay gaps) always fail; they are not scorable.
    order = sorted(truth, key=lambda x: sign * truth[x], reverse=True)
    picked = [cid for s in st.entries_of("submission") for cid in s["body"]["candidates"] if cid in truth]
    found = set(st.measured) & set(truth)  # includes the shared starting batch
    best_global = truth[order[0]]
    best_found = max((truth[x] for x in found), key=lambda v: sign * v) if found else None
    out.update(
        mean_true_of_picks=round(statistics.fmean(truth[x] for x in picked), 4) if picked else None,
        best_true_found=best_found,
        regret=round(sign * (best_global - best_found), 4) if best_found is not None else None,
    )
    for k in sorted({c.batch_size, max(1, round(0.1 * len(order)))}):
        out[f"top{k}_recall"] = round(len(set(order[:k]) & found) / k, 4)
    final = st.entries_of("final")
    if final:
        sc = final[-1]["body"].get("score") or {}
        out.update(rank_precision_at_k=sc.get("precision_at_k"), rank_spearman=sc.get("spearman"),
                   **{f"hyp_{k}": v for k, v in final[-1]["body"].get("hypotheses", {}).items()})
    out["rejections"] = len(st.entries_of("rejection"))
    rev = [e["body"]["shadow"] for e in st.entries_of("round_review")]
    for key in ("bayes_opt", "random"):
        vals = [r.get(f"{key}_batch_mean") for r in rev if r.get(f"{key}_batch_mean") is not None]
        if vals:
            out[f"shadow_{key}_mean"] = round(statistics.fmean(vals), 4)
    return out


# ---------------------------------------------------------------- driver
def one_run(arm: str, seed: int, args, overrides: dict) -> dict:
    ws = args.out / arm / f"seed{seed}"
    if args.resume and (ws / "result.json").exists():
        return json.loads((ws / "result.json").read_text())
    build_workspace(ws, arm, seed, overrides)
    if args.dry_run:
        print(f"[dry-run] {ws}\n  " + (" ".join(claude_cmd(args)[:2]) + " <TASK.md> ..." if arm in LLM_ARMS else f"scripted {arm}"))
        return {"arm": arm, "seed": seed, "dry_run": True}
    print(f"[start] {arm} seed={seed}", flush=True)
    try:
        meta = run_llm(ws, args) if arm in LLM_ARMS else run_scripted(ws, arm)
    except Exception as e:  # a crash in one run must not take down the batch
        meta = {"exit": "crash", "error": repr(e)}
    res = {"arm": arm, "seed": seed, **meta, **score_workspace(ws)}
    (ws / "result.json").write_text(json.dumps(res, indent=2))
    print(f"[done]  {arm} seed={seed} finalized={res.get('finalized')} regret={res.get('regret')} "
          f"wall={res.get('wall_s')}s", flush=True)
    return res


def parse_seeds(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


METRICS = ["finalized", "rounds_done", "regret", "best_true_found", "mean_true_of_picks", "top12_recall", "top30_recall",
           "rank_precision_at_k", "rank_spearman", "hyp_opened", "hyp_killed", "rejections", "turns", "cost_usd", "wall_s"]


def summarize(out_dir: Path) -> str:
    rows = [json.loads(p.read_text()) for p in sorted(out_dir.glob("*/seed*/result.json"))]
    if not rows:
        return "no results yet"
    metrics = [m for m in METRICS if any(m in r for r in rows)]
    metrics += sorted({k for r in rows for k in r if k.startswith("top") and k.endswith("_recall")} - set(metrics))
    with open(out_dir / "results.csv", "w", newline="") as f:
        cols = ["arm", "seed"] + sorted({k for r in rows for k in r} - {"arm", "seed"})
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    def num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (1.0 if v is True else 0.0 if v is False else None)

    arms = [a for a in (*LLM_ARMS, *SCRIPTED_ARMS) if any(r["arm"] == a for r in rows)]
    lines = [f"# A/B results — {out_dir.name}", "", "mean ± sd over seeds (n per arm in header)", "",
             "| metric | " + " | ".join(f"{a} (n={sum(r['arm'] == a for r in rows)})" for a in arms) + " |",
             "|---|" + "---|" * len(arms)]
    for m in metrics:
        cells = []
        for a in arms:
            vs = [num(r.get(m)) for r in rows if r["arm"] == a]
            vs = [v for v in vs if v is not None]
            cells.append("—" if not vs else f"{statistics.fmean(vs):.3f}" + (f" ± {statistics.stdev(vs):.3f}" if len(vs) > 1 else ""))
        lines.append(f"| {m} | " + " | ".join(cells) + " |")

    by = {(r["arm"], r["seed"]): r for r in rows}
    pairs = [("harness", "control"), ("harness", "bo"), ("control", "bo")]
    lines += ["", "Paired by seed (lower regret is better; higher recall is better):", ""]
    for a, b in pairs:
        seeds = sorted({s for (x, s) in by if x == a} & {s for (x, s) in by if x == b})
        for m, lower_better in (("regret", True), ("top12_recall", False), ("mean_true_of_picks", False)):
            d = [(num(by[(a, s)].get(m)), num(by[(b, s)].get(m))) for s in seeds]
            d = [(x, y) for x, y in d if x is not None and y is not None]
            if not d:
                continue
            diff = [x - y for x, y in d]
            wins = sum((x < y) if lower_better else (x > y) for x, y in d)
            ties = sum(x == y for x, y in d)
            lines.append(f"- {a} vs {b}, {m}: mean diff {statistics.fmean(diff):+.4f}, "
                         f"{a} better on {wins}/{len(d)} seeds ({ties} ties)")
    text = "\n".join(lines) + "\n"
    (out_dir / "summary.md").write_text(text)
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seeds", default="1-5")
    p.add_argument("--arms", default="harness,control,bo,random")
    p.add_argument("--out", type=Path, default=None,
                   help="output dir (default: <repo>/../dbtl-ab-runs/<timestamp>; must be outside the repo)")
    p.add_argument("--jobs", type=int, default=1, help="runs in parallel")
    p.add_argument("--model", default=None)
    p.add_argument("--claude", default="claude", help="claude executable")
    p.add_argument("--permission-mode", default="acceptEdits")
    p.add_argument("--max-budget-usd", type=float, default=None, help="per-run cap passed to claude")
    p.add_argument("--timeout", type=int, default=3 * 3600, help="per-run wall-clock cap, seconds")
    p.add_argument("--claude-arg", action="append", default=[], help="extra args for claude (repeatable)")
    p.add_argument("--set", action="append", default=[], metavar="KEY=YAML",
                   help="override a campaign.yaml key in every workspace, e.g. --set backend=v1_twin")
    p.add_argument("--resume", action="store_true", help="skip runs that already have result.json")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--summarize", action="store_true", help="only tabulate an existing --out")
    args = p.parse_args()

    if args.out is None:
        args.out = REPO.parent / "dbtl-ab-runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    args.out = args.out.resolve()
    if args.out == REPO or REPO in args.out.parents:
        p.error("--out must be outside the repo (its CLAUDE.md would leak into the control arm)")
    if args.summarize:
        print(summarize(args.out))
        return
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in (*LLM_ARMS, *SCRIPTED_ARMS)]
    if bad:
        p.error(f"unknown arm(s) {bad}")
    if any(a in LLM_ARMS for a in arms) and not args.dry_run and shutil.which(args.claude) is None:
        p.error(f"claude executable {args.claude!r} not found")
    if not (REPO / ".venv").exists():
        p.error("no .venv in the repo; run `uv sync` first")
    overrides = {k: yaml.safe_load(v) for k, v in (s.split("=", 1) for s in args.set)}

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "config.json").write_text(json.dumps({
        "seeds": parse_seeds(args.seeds), "arms": arms, "model": args.model, "overrides": overrides,
        "repo_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip(),
        "repo_dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.strip()),
        "started": datetime.now().isoformat(timespec="seconds"),
    }, indent=2))
    print(f"output: {args.out}")
    jobs = [(a, s) for s in parse_seeds(args.seeds) for a in arms]
    # Scripted arms run in-process (fast, and they share module state); LLM arms fan out.
    for a, s in [j for j in jobs if j[0] in SCRIPTED_ARMS]:
        one_run(a, s, args, overrides)
    llm = [j for j in jobs if j[0] in LLM_ARMS]
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        list(ex.map(lambda j: one_run(j[0], j[1], args, overrides), llm))
    if not args.dry_run:
        print(summarize(args.out))


if __name__ == "__main__":
    main()
