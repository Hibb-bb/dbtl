# Experimental campaign — operating notes

You choose which experiments to run. **Budget is experiments, not tokens.**

## Commands

Run every step with `uv run python scripts/<step>.py`.

```
raw_init.py                   start the campaign (loads the measured starting batch)
pool.py [--measured|--unmeasured] [--json]
                              the candidate pool with features, joined with measurements so far
raw_submit.py C-001 C-042 ... submit one round: exactly batch_size unmeasured candidate ids
raw_collect.py                fetch that round's results; `ready: false` means call again
raw_status.py                 round, budget left, next step, best measured so far
finalize.py '["C-..", ...]'   after the last round: submit the ranking, best first
```

The loop is `raw_init -> [ raw_submit -> raw_collect ] x rounds -> finalize`.

## Rules

- Never write under `ledger/` by any means. The scripts are the only writers.
- A failed well is not a low value. It is unmeasured and returns to the pool.
- You can read the ledger: `jq -c 'select(.kind=="observation") | .body' ledger/chain.jsonl`.
