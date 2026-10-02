#!/usr/bin/env python3
"""Register (or update/retire) hypotheses for the current round.

usage: hypothesize.py HYPOTHESES   where HYPOTHESES is a JSON file, '-' for stdin,
       or an inline JSON object/list. See schemas/hypothesis.schema.json.
"""
from _common import load_json_arg, parser, run
from harness import api

if __name__ == "__main__":
    p = parser(__doc__)
    p.add_argument("hypotheses", help="JSON file, '-' for stdin, or inline JSON (object or list)")
    args = p.parse_args()
    run(api.hypothesize, args, load_json_arg(args.hypotheses))
