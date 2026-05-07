"""Run BuySideFlow time audit via ``python -m buysideflow.run_time_audit``."""

from __future__ import annotations

from ._bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from run_time_audit import *  # noqa: F401,F403
from run_time_audit import main


if __name__ == "__main__":
    main()
