"""Shadow baselines. They are logged for comparison, never used to pick."""
from __future__ import annotations

from .bayes_opt import bayes_opt_pick
from .random_pick import random_pick

BASELINES = {"random": random_pick, "bayes_opt": bayes_opt_pick}
