#!/usr/bin/env python3
"""Control arm: initialize the campaign (validate campaign.yaml, load the starting batch, open the ledger)."""
from _common import parser, run
from harness import api


def init(root) -> dict:
    return dict(api.init(root), next="raw_submit")


if __name__ == "__main__":
    args = parser(__doc__).parse_args()
    run(init, args)
