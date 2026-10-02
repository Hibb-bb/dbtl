#!/usr/bin/env python3
"""Apply every locked kill criterion to the round's data and write the round review.

The agent cannot author this file. Verdicts: supported | weakened | killed | untested.
"""
from _common import parser, run
from harness import api

if __name__ == "__main__":
    args = parser(__doc__).parse_args()
    run(api.review, args)
