# DBTL Harness — Build Spec

A harness that forces an AI agent to run closed-loop experimental campaigns
with strict, recorded reasoning.

Target: the task format used by Autonomous Science Bench
(https://yibow.me/autonomous-science-bench).

---

## 1. What this is

The benchmark gives the agent a campaign: a starting batch of measured
candidates, a fixed number of rounds, a batch size, and a budget. Each round
the agent picks a batch, gets measurements back, and picks again. At the end it
submits a ranking, a predictive model, or final designs.

The benchmark has three backends. The agent interface is the same for all three.

- **v0 replay** — results come from recorded experiments, replayed exactly.
- **v1 digital twin** — results come from a simulator, with noise and failures.
- **v2 live** — results come from real instruments, with queues and faults.

This harness sits between the agent and the backend. It does not make the
experiments happen. It makes the agent's reasoning explicit, checkable, and
permanent.

## 2. What problem it solves

Left alone, an agent will pick a batch and write a plausible story about why.
The story is produced after the choice and cannot be wrong. This harness forces
the story first, in a fixed shape, tied to real data, with a stated way to be
proven wrong.

Three rules carry most of the weight:

1. A hypothesis must cite observations that actually exist in the ledger.
2. A hypothesis must carry a kill criterion containing a number.
3. The agent does not decide whether its hypothesis survived. A script applies
   the locked kill criterion to the returned data.

## 3. Key constraint

The campaign budget is experiments, not compute. The harness must never spend a
well on its own process. All overhead is tokens and disk.

---

## 4. Repo layout

```
dbtl/
  campaign.yaml           # the only file a user edits before a run
  schemas/
    hypothesis.schema.json
    batch_plan.schema.json
    round_review.schema.json
  scripts/
    init.py
    status.py
    hypothesize.py
    plan.py
    submit.py
    collect.py
    review.py
    finalize.py
  adapters/
    base.py               # the interface
    fake.py               # for tests
    replay.py             # v0
    twin.py               # v1
  baselines/
    random_pick.py
    bayes_opt.py
  ledger/
    chain.jsonl           # append-only, hash-chained
    raw/                  # raw backend responses, never edited
  hooks/
    settings.json
  tests/
```

---

## 5. campaign.yaml

The only thing a user sets up. After this the agent runs on its own.

```yaml
task: protein_active_learning
backend: v0_replay
seed: 17

rounds: 8
batch_size: 12
budget_experiments: 96

hypotheses:
  max_active: 3            # hard cap on live hypotheses at any time
  min_evidence: 2          # observations each must cite
  require_kill_number: true
  reopen_requires_new_evidence: true

batch_rules:
  explore_floor: 0.20      # share of batch serving no hypothesis
  discriminate_floor: 0.25 # share that must separate rival hypotheses
  no_repeat_measured: true
  require_interval: true   # every pick needs a predicted value + interval

deliverable: ranking        # ranking | model | designs
shadow_baselines: [random, bayes_opt]
```

Validate this file on load. Unknown keys are an error, not a warning.

---

## 6. Data objects

All JSON. All schema-checked on write. All appended to the ledger.

### 6.1 Hypothesis

```json
{
  "id": "H3",
  "round_opened": 2,
  "statement": "activity rises with hydrophobicity at site 47",
  "reason": "site 47 sits in the binding pocket; buried residues favor nonpolar contacts",
  "evidence": ["W0-14", "W0-31", "W1-07"],
  "prediction": "any variant with a hydrophobic residue at 47 scores above 0.6",
  "kill_criterion": {
    "text": "two or more hydrophobic-47 variants score below 0.45",
    "metric": "activity",
    "comparator": "lt",
    "threshold": 0.45,
    "count": 2
  },
  "confidence": 0.55,
  "status": "active",
  "round_closed": null,
  "close_verdict": null
}
```

Rules enforced by `hypothesize.py`:

- Every ID in `evidence` must exist in the ledger and have a measurement.
  Unknown IDs are a hard reject with the offending ID named.
- `evidence` length >= `min_evidence`.
- `kill_criterion` must parse into a machine-checkable form. Free text alone is
  rejected. The `text` field is for humans; the structured fields are what runs.
- `confidence` in (0, 1), exclusive. 0 and 1 are rejected.
- Active hypotheses must not exceed `max_active`.
- A hypothesis with status `killed` cannot return to `active` unless the new
  record cites at least one observation measured after `round_closed`.

### 6.2 Batch plan

```json
{
  "round": 3,
  "picks": [
    {
      "candidate": "C-221",
      "serves": "H3",
      "role": "discriminate",
      "predicted": 0.71,
      "interval": [0.58, 0.84],
      "why_this_one": "H3 predicts 0.71, H1 predicts 0.40; largest gap in the pool"
    },
    {
      "candidate": "C-109",
      "serves": null,
      "role": "explore",
      "predicted": 0.35,
      "interval": [0.10, 0.70],
      "why_this_one": "highest model uncertainty; no active hypothesis covers this region"
    }
  ]
}
```

`role` is one of `exploit`, `discriminate`, `explore`.

Rules enforced by `plan.py`. Each rejection must name the rule and the offending
pick.

- Batch size matches `batch_size` exactly.
- Remaining budget covers the batch.
- Every pick has either a `serves` pointing at an active hypothesis, or
  `serves: null` with `role: explore`. Nothing else passes.
- Explore picks >= `explore_floor` of the batch.
- Discriminate picks >= `discriminate_floor` of the batch.
- **Separation check.** For each pair of active hypotheses, at least one pick
  must have different predictions under each. If every active hypothesis
  predicts the same thing for every pick, the round cannot tell them apart.
  Reject with the names of the pair that is not separated.
- No candidate already measured (if `no_repeat_measured`).
- No duplicate candidates inside the batch.
- Every pick has `predicted` and an `interval` containing it.
- `why_this_one` is non-empty and mentions at least one other candidate or
  hypothesis. A reason that compares nothing is not a reason.

### 6.3 Round review

Written by `review.py`. The agent cannot author this file.

```json
{
  "round": 3,
  "per_pick": [
    {"candidate": "C-221", "predicted": 0.71, "observed": 0.52, "in_interval": false}
  ],
  "per_hypothesis": {
    "H3": {
      "verdict": "killed",
      "trigger": "2 hydrophobic-47 variants below 0.45 (C-221=0.52 excluded, C-233=0.41, C-240=0.38)"
    },
    "H1": {"verdict": "supported", "trigger": null}
  },
  "calibration_to_date": {"interval_hit_rate": 0.61, "n": 36},
  "shadow": {
    "agent_batch_mean": 0.58,
    "random_batch_mean": 0.31,
    "bayes_opt_batch_mean": 0.55
  }
}
```

Verdicts: `supported`, `weakened`, `killed`, `untested`.

`killed` is produced only by the locked kill criterion firing. Nothing else can
produce it. The agent writes its interpretation separately, after this file
exists.

---

## 7. The ledger

`ledger/chain.jsonl`. One JSON object per line. Each line carries the SHA-256
of the previous line.

```json
{"seq": 41, "ts": "...", "kind": "hypothesis", "prev": "a91f...", "hash": "7c20...", "body": {...}}
```

Kinds: `campaign_init`, `observation`, `hypothesis`, `batch_plan`,
`submission`, `result`, `round_review`, `final`.

- Append only. No update, no delete.
- `status.py` verifies the chain on every call and reports a break loudly.
- Raw backend responses go to `ledger/raw/<round>/` untouched. The ledger stores
  their hash.

---

## 8. Round state machine

```
init -> [ hypothesize -> plan -> submit -> collect -> review ] x R -> finalize
```

Each script checks the current state and refuses if called out of order, with a
message saying what the next legal step is.

- `hypothesize.py` refuses if the previous round has no review.
- `plan.py` refuses if no hypothesis is active for this round.
- `submit.py` refuses if the plan for this round did not validate.
- `collect.py` refuses if nothing was submitted.
- `review.py` refuses if results are incomplete.
- `finalize.py` refuses if any round is unreviewed.

## 9. Hooks

`hooks/settings.json` for Claude Code. Without these the agent will skip steps
when it is short on context or under pressure.

- `PreToolUse` on Write/Edit: block any path under `ledger/`. Changes go through
  the scripts.
- `PreToolUse` on Bash: block shell redirection into `ledger/`.
- `Stop`: refuse to end the session if the current round is mid-flight, or any
  submitted round is unreviewed.

The hooks are the difference between a harness and a suggestion. Build them.

---

## 10. Adapter interface

The only thing that changes between backends.

```python
# adapters/base.py
class Adapter:
    def load_start(self) -> list[dict]:
        """Return the starting batch: candidate ids with measurements."""

    def pool(self) -> list[dict]:
        """Return all selectable candidates with their features."""

    def submit(self, round_no: int, candidates: list[str]) -> str:
        """Send the batch. Return a submission handle."""

    def collect(self, handle: str) -> dict | None:
        """Return measurements, or None if not ready yet."""
```

`collect` returning `None` is normal. v2 has queues. Do not assume results come
back in the same process.

Measurements may come back with a `failed` flag. A failed well is not a low
value. Never score it as one. It returns to the pool.

---

## 11. Shadow baselines

Every round, after the agent's plan validates and before results are revealed,
run `random_pick` and `bayes_opt` on the same state and log what they would have
chosen. Score them against the same returned data where it overlaps, and against
the full oracle in v0.

This is the cheapest useful thing in the whole build. It answers whether the
agent's reasoning beats a plain optimizer or just decorates it.

---

## 12. status.py

The one command a human runs.

- Chain integrity: ok or broken at line N
- Round, rounds left, budget left
- Active hypotheses with confidence and age in rounds
- Hypotheses opened but never resolved
- Calibration: stated interval hit rate, bucketed
- Agent vs random vs BO, cumulative
- Share of picks by role across the campaign

---

## 13. Build order

Build in this order. Do not start a phase before the previous one's tests pass.

**Phase 1 — foundations.** Schemas, ledger with hash chain, `fake.py` adapter.
No agent, no loop. Tests prove the chain detects edits.

**Phase 2 — validators.** `hypothesize.py` and `plan.py` with every rule in
sections 6.1 and 6.2. This is the core of the project. Most of the test effort
belongs here.

**Phase 3 — the loop.** `init`, `submit`, `collect`, `review`, `finalize`, and
the state machine. Run a full 8-round campaign against `fake.py`.

**Phase 4 — hooks and status.** Make the rules unskippable. Add `status.py`.

**Phase 5 — real backends.** `replay.py` for v0, then `twin.py` for v1.
Baselines last.

---

## 14. Acceptance tests

Most tests must assert that bad input is **rejected**. A permissive validator
passes a happy-path suite and is worthless.

Write a failing test for each of these. Each must be rejected with a message
naming the rule:

1. Hypothesis citing a candidate ID that does not exist.
2. Hypothesis citing a candidate that exists but was never measured.
3. Hypothesis with fewer than `min_evidence` citations.
4. Kill criterion with no threshold, only prose.
5. Confidence of exactly 1.0.
6. A fourth active hypothesis when `max_active` is 3.
7. Reopening a killed hypothesis citing only pre-death evidence.
8. Batch with a pick that has no `serves` and no `explore` role.
9. Batch below the explore floor.
10. Batch below the discriminate floor.
11. Batch where two active hypotheses predict identically on every pick.
12. Batch containing an already-measured candidate.
13. Batch with a duplicate candidate.
14. Pick whose interval does not contain its own predicted value.
15. `plan.py` called before any hypothesis is registered this round.
16. `submit.py` called on a plan that failed validation.
17. `hypothesize.py` called with the previous round unreviewed.
18. A hand-edited line in the middle of the ledger — chain check must fail.
19. A failed well scored as a low value instead of returned to the pool.

And one positive end-to-end test: a full campaign on `fake.py` that produces a
valid ranking and a complete, unbroken ledger.

---

## 15. Non-goals

- Do not build a UI.
- Do not build an experiment scheduler. The backend owns that.
- Do not make the harness choose the batch. It validates and records; the agent
  chooses. The baselines are logged for comparison, not used to pick.
- Do not add schema fields that nothing checks. An unchecked field is a token
  cost with no benefit.

## 16. Notes for whoever builds this

The failure mode to watch for is strictness that produces hedging instead of
thinking. If the agent starts writing vague hypotheses that are technically
valid but hard to kill, the kill criterion rules are too loose. Track how many
hypotheses die per campaign. If nothing ever dies, the harness is not working.
