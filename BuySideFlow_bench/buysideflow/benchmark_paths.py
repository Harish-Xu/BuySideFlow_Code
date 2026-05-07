"""Compatibility wrapper for BuySideFlow path helpers."""

from __future__ import annotations

from ._bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from benchmark_paths import *  # noqa: F401,F403
