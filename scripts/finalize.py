#!/usr/bin/env python3
"""Submit the final deliverable (ranking | model | designs, per campaign.yaml).

usage: finalize.py DELIVERABLE   where DELIVERABLE is a JSON file, '-' for stdin, or inline JSON.
  ranking: ["C-001", "C-042", ...] best first
  model:   {"C-001": 0.71, ...} for every pool candidate
  designs: [{"features": {...}}, ...]
"""
from _common import load_json_arg, parser, run
from harness import api

if __name__ == "__main__":
    p = parser(__doc__)
    p.add_argument("deliverable", help="JSON file, '-' for stdin, or inline JSON")
    args = p.parse_args()
    run(api.finalize, args, load_json_arg(args.deliverable))
