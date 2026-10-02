#!/usr/bin/env python3
"""Initialize the campaign: validate campaign.yaml, load the starting batch, open the ledger."""
from _common import parser, run
from harness import api

if __name__ == "__main__":
    args = parser(__doc__).parse_args()
    run(api.init, args)
