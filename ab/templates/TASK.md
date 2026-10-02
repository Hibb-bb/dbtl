You are running a closed-loop experimental campaign. `campaign.yaml` in this directory defines it:
a pool of protein variants with features, a measured starting batch, a fixed number of rounds,
and a fixed number of experiments per round. The objective metric is `activity`; higher is better.

Goal: within the budget, find the highest-activity variants. Your final deliverable is a ranking of
candidate ids, best first. It is scored against ground-truth activity, and the campaign is also judged
on how active the variants you chose to measure turned out to be.

Follow CLAUDE.md for how to operate in this directory. Work autonomously until the campaign is
finalized: run every round, then submit the ranking. Do not ask questions; nobody will answer.
