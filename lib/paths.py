"""Central data root — local project folder, or Drive-synced cache on Cloud."""

from __future__ import annotations

import os
from pathlib import Path

# lib/ → project root
_CODE_ROOT = Path(__file__).resolve().parents[1]


def code_root() -> Path:
    return _CODE_ROOT


def data_root() -> Path:
    """
    Where Uber/OLA/GPS/Rapido/Allocation live.
    Override with AI_DASHBOARD_DATA_ROOT (set by Drive sync on Cloud).
    """
    override = os.environ.get("AI_DASHBOARD_DATA_ROOT", "").strip()
    if override:
        return Path(override)
    return _CODE_ROOT
