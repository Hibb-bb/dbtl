"""Backend adapters. The only thing that changes between backends."""
from __future__ import annotations

from .base import Adapter


def load_adapter(campaign) -> Adapter:
    name = campaign.backend
    if name == "fake":
        from .fake import FakeAdapter as A
    elif name == "v0_replay":
        from .replay import ReplayAdapter as A
    elif name == "v1_twin":
        from .twin import TwinAdapter as A
    else:
        raise ValueError(f"no adapter for backend {name!r}")
    return A(campaign)
