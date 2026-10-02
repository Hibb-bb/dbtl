#!/usr/bin/env python3
"""Validate and record the batch plan for the current round.

usage: plan.py PLAN   where PLAN is a JSON file, '-' for stdin, or inline JSON.
See schemas/batch_plan.schema.json. A rejected plan is logged and nothing is submitted.
"""
from _common import load_json_arg, parser, run
from harness import api

if __name__ == "__main__":
    p = parser(__doc__)
    p.add_argument("plan", help="JSON file, '-' for stdin, or inline JSON")
    args = p.parse_args()
    run(api.plan, args, load_json_arg(args.plan))
