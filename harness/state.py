"""Derive the campaign state from the ledger. The ledger is the only source of truth."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Campaign
from .errors import StateError
from .ledger import Ledger

STEPS = ["hypothesize", "plan", "submit", "collect", "review"]


@dataclass
class Observation:
    obs_id: str
    round: int
    candidate: str
    values: dict
    failed: bool


@dataclass
class State:
    campaign: Campaign
    entries: list[dict]
    observations: list[Observation] = field(default_factory=list)
    hypotheses: dict[str, dict] = field(default_factory=dict)   # latest record per id
    hypothesis_history: dict[str, list[dict]] = field(default_factory=dict)
    rounds_reviewed: int = 0
    finalized: bool = False
    initialized: bool = False

    # ---- derived --------------------------------------------------------
    @property
    def round(self) -> int:
        """The round currently in flight (1-based). rounds+1 means all rounds done."""
        return self.rounds_reviewed + 1

    @property
    def all_rounds_done(self) -> bool:
        return self.rounds_reviewed >= self.campaign.rounds

    def entries_of(self, kind: str, round_no: int | None = None) -> list[dict]:
        return [e for e in self.entries if e["kind"] == kind and (round_no is None or e["round"] == round_no)]

    def hypothesis_entries_this_round(self) -> list[dict]:
        return [e for e in self.entries_of("hypothesis", self.round) if e["actor"] == "agent"]

    def valid_plan(self, round_no: int) -> dict | None:
        plans = self.entries_of("batch_plan", round_no)
        return plans[-1]["body"] if plans else None

    def submission(self, round_no: int) -> dict | None:
        subs = self.entries_of("submission", round_no)
        return subs[-1]["body"] if subs else None

    def result(self, round_no: int) -> dict | None:
        res = self.entries_of("result", round_no)
        return res[-1]["body"] if res else None

    def review(self, round_no: int) -> dict | None:
        rev = self.entries_of("round_review", round_no)
        return rev[-1]["body"] if rev else None

    @property
    def active_hypotheses(self) -> dict[str, dict]:
        return {k: v for k, v in self.hypotheses.items() if v["status"] == "active"}

    @property
    def measured(self) -> dict[str, Observation]:
        """Latest successful observation per candidate."""
        out: dict[str, Observation] = {}
        for o in self.observations:
            if not o.failed:
                out[o.candidate] = o
        return out

    @property
    def obs_by_id(self) -> dict[str, Observation]:
        return {o.obs_id: o for o in self.observations}

    @property
    def experiments_spent(self) -> int:
        """Wells submitted so far (failed wells still cost an experiment)."""
        return sum(len(s["body"]["candidates"]) for s in self.entries_of("submission"))

    @property
    def budget_left(self) -> int:
        return self.campaign.budget_experiments - self.experiments_spent

    # ---- step logic -----------------------------------------------------
    def step(self) -> str:
        """Name of the next legal script."""
        if not self.initialized:
            return "init"
        if self.finalized:
            return "done"
        if self.all_rounds_done:
            return "finalize"
        r = self.round
        if self.review(r):
            return "hypothesize"  # unreachable: review bumps rounds_reviewed
        if self.result(r):
            return "review"
        if self.submission(r):
            return "collect"
        if self.valid_plan(r):
            return "submit"
        if self.hypothesis_entries_this_round():
            return "plan"
        return "hypothesize"

    def require(self, step: str) -> None:
        """Refuse unless `step` is legal now. hypothesize/plan may repeat before submit."""
        nxt = self.step()
        if nxt == step:
            return
        if step == "hypothesize" and nxt == "plan":
            return  # more hypotheses before planning is fine
        if step == "plan" and nxt == "submit":
            return  # re-planning before submission is fine
        if not self.initialized:
            raise StateError("Campaign is not initialized.", "init")
        if nxt == "done":
            raise StateError("Campaign is finalized; nothing more to do.", "done")
        detail = {
            "hypothesize": f"round {self.round} already has a validated plan" if nxt in ("submit",) else
                           f"round {self.round if nxt != 'finalize' else self.round - 1} is not reviewed yet",
            "plan": f"no hypothesis registered in round {self.round} yet" if nxt == "hypothesize" else
                    f"round {self.round} is past planning",
            "submit": f"round {self.round} has no validated plan" if nxt in ("hypothesize", "plan") else
                      f"round {self.round} was already submitted",
            "collect": f"nothing submitted for round {self.round}" if nxt in ("hypothesize", "plan", "submit") else
                       f"round {self.round} already collected",
            "review": f"no results collected for round {self.round}",
            "finalize": f"round {self.round} is unreviewed",
            "init": "campaign already initialized",
        }.get(step, "out of order")
        raise StateError(f"Cannot run {step}: {detail}.", nxt)


def load_state(campaign: Campaign) -> State:
    ledger = Ledger(campaign.ledger_dir)
    entries = ledger.entries(verify=True)  # raises ChainBroken loudly
    st = State(campaign=campaign, entries=entries)
    for e in entries:
        k, b = e["kind"], e["body"]
        if k == "campaign_init":
            st.initialized = True
        elif k == "observation":
            st.observations.append(Observation(b["obs_id"], b["round"], b["candidate"], b["values"], b["failed"]))
        elif k == "hypothesis":
            st.hypotheses[b["id"]] = b
            st.hypothesis_history.setdefault(b["id"], []).append(b)
        elif k == "round_review":
            st.rounds_reviewed = max(st.rounds_reviewed, b["round"])
        elif k == "final":
            st.finalized = True
    return st
