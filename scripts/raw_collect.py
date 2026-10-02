#!/usr/bin/env python3
"""Control arm: collect results for the submitted round. Prints ready=false if the backend is not done
yet; call again. When results are in, the round is closed and its measurements are printed."""
from _common import parser, run
from harness import control

if __name__ == "__main__":
    args = parser(__doc__).parse_args()
    run(control.raw_collect, args)
