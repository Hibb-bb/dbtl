from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import HarnessError

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

# Map the first path element of a schema error onto a named rule so that
# rejections say which rule fired, not just "schema".
_RULE_BY_PATH = {
    "hypothesis": {"kill_criterion": "kill_criterion_structure", "confidence": "confidence_range",
                   "evidence": "evidence_format", "id": "hypothesis_id"},
    "batch_plan": {"picks": "pick_structure", "round": "plan_round"},
    "round_review": {},
}


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    path = SCHEMA_DIR / f"{name}.schema.json"
    with open(path) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(name: str, obj) -> None:
    errors = sorted(_validator(name).iter_errors(obj), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    e = errors[0]
    path = list(e.absolute_path)
    rule = "schema"
    if path:
        rule = _RULE_BY_PATH.get(name, {}).get(str(path[0]), "schema")
    where = "/".join(str(p) for p in path) or "<root>"
    raise HarnessError(rule, f"{name} schema violation at '{where}': {e.message}")
