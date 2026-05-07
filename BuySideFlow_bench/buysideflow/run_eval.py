"""Run BuySideFlow evaluation via ``python -m buysideflow.run_eval``."""

from __future__ import annotations

from ._bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from run_eval import *  # noqa: F401,F403
from run_eval import main


if __name__ == "__main__":
    main()
