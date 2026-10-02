# DBTL Harness

A harness that forces an AI agent to run closed-loop experimental campaigns with strict,
recorded reasoning. Built to the spec in [SPEC.md](SPEC.md) for the task format of
[Autonomous Science Bench](https://yibow.me/autonomous-science-bench).

The harness sits between the agent and a backend (recorded replay, digital twin, live lab).
It never spends an experiment of its own. It makes the agent's hypotheses explicit, tied to
real data, and falsifiable by a locked numeric criterion that a script, not the agent, applies.

## Quick start

```bash
uv sync                                  # Python 3.12, numpy, jsonschema, pyyaml, pytest
uv run pytest -q                         # 100+ tests, most assert that bad input is rejected
uv run python scripts/init.py            # reads campaign.yaml, loads the starting batch
uv run python scripts/status.py          # chain check + where the campaign stands
```

Then run the loop (see [CLAUDE.md](CLAUDE.md) for the agent-facing protocol):

```bash
uv run python scripts/hypothesize.py hyps.json
uv run python scripts/plan.py plan.json
uv run python scripts/submit.py
uv run python scripts/collect.py         # repeat until ready: true
uv run python scripts/review.py
# ... x rounds ...
uv run python scripts/finalize.py ranking.json
```

Every script accepts `--root DIR` (or `$DBTL_ROOT`) to run a campaign that lives outside
the repo, and `--json` for machine-readable output. Exit codes: 0 ok, 1 rejected (rule
named on stderr), 3 refused by the state machine (next legal step named), 4 broken chain.

## Layout

```
campaign.yaml      the only file a user edits before a run
schemas/           hypothesis, batch_plan, round_review JSON schemas
scripts/           init, status, hypothesize, plan, submit, collect, review, finalize
harness/           shared library: config, ledger, state, validators, kill criteria, api
adapters/          base (interface), fake (tests), replay (v0), twin (v1), dataset loader
baselines/         random_pick, bayes_opt (shadow only; never used to pick)
ledger/            chain.jsonl (append-only, hash-chained) and raw/<round>/ backend responses
hooks/             Claude Code hooks; copied to .claude/settings.json
data/              synthetic protein_active_learning.json + its generator
tests/             phase-ordered acceptance tests
```

## campaign.yaml

Exactly the spec's keys, validated strictly (unknown keys are errors), plus one optional
`backend_options` mapping for adapter knobs:

| backend     | options |
|-------------|---------|
| `fake`      | `pool_size`, `start_size`, `fail_rate`, `collect_delay`, `noise_sd` |
| `v0_replay` | `dataset` (default `data/<task>.json`) |
| `v1_twin`   | `dataset`, `noise_sd`, `fail_rate`, `queue_delay`, `clip` |

## Data objects

Two fields beyond the spec, both checked by validators:

- **`kill_criterion.scope`** says which candidates the criterion applies to. Either
  `{"candidates": ["C-221", ...]}` or `{"all": [{"feature": "res47_hydrophobic", "op": "eq", "value": true}, ...]}`.
  Ops: `eq ne in gt ge lt le`. A scope matching nothing in the pool is rejected (unkillable).
- **`predictions_by_hypothesis`** on every pick: `{"H1": 0.40, "H3": 0.71}` for every active
  hypothesis. This is what the separation check reads. For a pick that `serves` H3,
  `predicted` must equal `predictions_by_hypothesis.H3`.

### Hypothesis

```json
{
  "id": "H3",
  "statement": "activity rises with hydrophobicity at site 47",
  "reason": "site 47 sits in the binding pocket; buried residues favor nonpolar contacts",
  "evidence": ["C-014", "C-031", "W1-07"],
  "prediction": "any variant with a hydrophobic residue at 47 scores above 0.6",
  "kill_criterion": {
    "text": "two or more hydrophobic-47 variants score below 0.45",
    "metric": "activity", "comparator": "lt", "threshold": 0.45, "count": 2,
    "scope": {"all": [{"feature": "res47_hydrophobic", "op": "eq", "value": true}]}
  },
  "confidence": 0.55,
  "status": "active"
}
```

`round_opened`, `round_closed`, `close_verdict` are set by the harness. The criterion fires
when `count` or more in-scope, non-failed observations measured since the hypothesis was
(re)opened satisfy `value <comparator> threshold`. Verdicts per round: `killed` (fired),
`weakened` (some hits, below count), `supported` (tested in scope this round, no hits),
`untested` (nothing in scope this round).

### Batch plan

```json
{
  "round": 3,
  "picks": [
    {"candidate": "C-221", "serves": "H3", "role": "discriminate", "predicted": 0.71,
     "interval": [0.58, 0.84], "predictions_by_hypothesis": {"H1": 0.40, "H3": 0.71},
     "why_this_one": "H3 predicts 0.71, H1 predicts 0.40; largest gap in the pool"},
    {"candidate": "C-109", "serves": null, "role": "explore", "predicted": 0.35,
     "interval": [0.10, 0.70], "predictions_by_hypothesis": {"H1": 0.35, "H3": 0.35},
     "why_this_one": "highest model uncertainty; nothing near C-221 covers this region"}
  ]
}
```

## The ledger

`ledger/chain.jsonl`: one JSON object per line, `prev` = previous line's SHA-256, `hash` =
SHA-256 of the line itself. Kinds: `campaign_init`, `observation`, `hypothesis`, `batch_plan`,
`submission`, `result`, `round_review`, `final`, and `rejection` (every rejected hypothesis or
plan is logged with its rule, so hedging is countable). Each line also carries `round` and
`actor` (`agent`, `harness`, or `review`); a `hypothesis` line with status `killed` is only
ever written by `review`. Raw backend responses go untouched to `ledger/raw/<round>/` and
the ledger stores their hash. Every script verifies the chain before touching it.

## Hooks

`hooks/settings.json` (mirrored at `.claude/settings.json`) wires three Claude Code hooks:

- `PreToolUse` Write/Edit: blocks any path under `ledger/`.
- `PreToolUse` Bash: blocks redirection, `tee`, `sed -i`, `cp/mv/rm/truncate`, inline
  Python writes, and `git checkout` aimed at `ledger/`.
- `Stop`: refuses to end the session while a round is mid-flight (`status.py --stop-check`).

## Backends

| backend     | source of results                              | oracle for shadow scoring |
|-------------|-------------------------------------------------|---------------------------|
| `fake`      | seeded synthetic function, optional failures/queue | yes |
| `v0_replay` | recorded values in the dataset, replayed exactly | yes |
| `v1_twin`   | dataset values + seeded noise, failures, queue   | yes (noiseless truth) |
| `v2_live`   | not implemented; implement `adapters/base.Adapter` | no |

Dataset format is documented in `adapters/dataset.py`. The shipped
`data/protein_active_learning.json` is synthetic (400 variants, 24-well start batch) and is
regenerated by `python data/make_protein_demo.py`. Swap in the benchmark's real task files by
writing an adapter or converting to this format.

## Spec deviations, all deliberate

- Added `kill_criterion.scope` and `predictions_by_hypothesis` (needed to make the spec's own
  checks runnable). Added `rejection` ledger kind and `actor`/`round` envelope fields.
- Added `backend_options` to `campaign.yaml`.
- Hypothesis status `retired` lets the agent drop a hypothesis voluntarily; it is not `killed`.
- `collect.py` returning `ready: false` is a success exit, not an error.
