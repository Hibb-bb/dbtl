#!/usr/bin/env python3
"""Collect results for the submitted round. Prints ready=false if the backend is not done yet."""
from _common import parser, run
from harness import api

if __name__ == "__main__":
    args = parser(__doc__).parse_args()
    run(api.collect, args)
