"""Run the BuySideFlow benchmark via ``python -m buysideflow.run_text2sql``."""

from __future__ import annotations

from ._bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from run_text2sql import *  # noqa: F401,F403
from run_text2sql import main


if __name__ == "__main__":
    main()
