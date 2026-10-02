#!/usr/bin/env python3
"""Control arm: submit a batch of candidate ids. No hypotheses, plan or predictions needed.

usage: raw_submit.py C-001 C-042 ...       (exactly batch_size ids)
       raw_submit.py batch.json             (a JSON list of ids; '-' for stdin, or an inline JSON list)
"""
from _common import load_json_arg, parser, run
from harness import control

if __name__ == "__main__":
    p = parser(__doc__)
    p.add_argument("candidates", nargs="+", help="candidate ids, or one JSON file / '-' / inline JSON list")
    args = p.parse_args()
    ids = args.candidates
    if len(ids) == 1 and not ids[0].startswith("C-"):
        ids = load_json_arg(ids[0])
    run(control.raw_submit, args, ids)
