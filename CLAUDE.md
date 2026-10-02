# DBTL harness — agent protocol

You are running an experimental campaign through this harness. The harness does not
choose experiments; you do. It validates and records your reasoning, and it decides
mechanically whether your hypotheses survived. **Budget is experiments, not tokens.**

## The loop

```
init -> [ hypothesize -> plan -> submit -> collect -> review ] x rounds -> finalize
```

Run every step with `uv run python scripts/<step>.py`. Each script refuses to run out
of order and tells you the next legal step. `scripts/status.py` shows where you are.

1. `hypothesize.py H.json` — register 1..max_active hypotheses (JSON object or list).
   Each must cite >= `min_evidence` observations that exist in the ledger (candidate ids
   like `C-221` or observation ids like `W1-07`), carry a **machine-checkable kill
   criterion** (metric, comparator, threshold, count, scope), and a confidence strictly
   between 0 and 1. To keep an existing hypothesis alive this round, resubmit it (same id);
   to drop one, resubmit with `"status": "retired"`. You can never set `killed`.
2. `plan.py P.json` — exactly `batch_size` picks. Every pick: `serves` an active
   hypothesis (role `exploit` | `discriminate`) or `serves: null` with role `explore`;
   `predicted` inside `interval`; `predictions_by_hypothesis` for every active hypothesis;
   `why_this_one` that names another candidate or a hypothesis. Floors on explore and
   discriminate shares apply. Every pair of active hypotheses must disagree on some pick.
3. `submit.py` — sends the batch; logs what random and Bayesian optimization would have picked.
4. `collect.py` — fetches results. `ready: false` is normal on queued backends; call again.
5. `review.py` — applies each locked kill criterion. Verdicts: supported / weakened /
   killed / untested. Only this script kills a hypothesis. Read `ledger/raw/<round>/` and
   the review before writing next round's hypotheses; your interpretation goes into the
   `reason` fields of the next `hypothesize.py` call.
6. After the last review: `finalize.py D.json` with the deliverable named in campaign.yaml.

## Hard rules (enforced by hooks)

- Never write under `ledger/` by any means. Scripts are the only writers.
- Do not stop mid-round. The Stop hook refuses until the round is reviewed.
- A failed well is not a low value. It is unscored and returns to the pool.

## Reading the ledger

```
uv run python scripts/status.py            # human summary, chain check
jq -c 'select(.kind=="round_review")' ledger/chain.jsonl
jq -c 'select(.kind=="observation") | .body' ledger/chain.jsonl
```

Schemas live in `schemas/`. Example inputs are in `README.md`.
