from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path
from typing import Any

from .task_loader import CANONICAL_DATASET


def build_problem_statement(task_id: str, path: str | Path = CANONICAL_DATASET) -> Any:
    """Build the same problem object used by the formal SWE-agent loader."""
    with contextlib.redirect_stdout(io.StringIO()):
        from sweagent.run.batch_instances import Text2SQLInstances

    source = Text2SQLInstances(path=Path(path), filter=f"^{re.escape(task_id)}$")
    instances = source.get_instance_configs()
    if len(instances) != 1:
        raise KeyError(f"Expected one task named {task_id!r}, found {len(instances)}")
    return instances[0].problem_statement

