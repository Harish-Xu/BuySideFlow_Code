"""Path bootstrap helpers for compatibility wrappers."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ensure_project_root_on_path() -> None:
    path = str(PROJECT_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)
