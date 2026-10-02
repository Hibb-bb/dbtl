#!/usr/bin/env python3
"""Submit the validated plan to the backend. Logs the shadow baselines' picks first."""
from _common import parser, run
from harness import api

if __name__ == "__main__":
    args = parser(__doc__).parse_args()
    run(api.submit, args)
