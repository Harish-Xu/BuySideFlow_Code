from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

from .assets import PROJECT_ROOT


CANONICAL_DATASET = PROJECT_ROOT / "data/dataset"


def load_tasks(path: str | Path = CANONICAL_DATASET) -> list[dict[str, Any]]:
    """Load tasks through the benchmark's canonical parser."""
    # Importing the SWE-agent package emits its version banner to stdout. Keep
    # adapter output machine-readable without changing the existing package.
    with contextlib.redirect_stdout(io.StringIO()):
        from sweagent.text2sql.markdown import parse_text2sql_tasks

    tasks = parse_text2sql_tasks(Path(path))
    if not tasks:
        raise ValueError(f"No Text-to-SQL tasks found in {path}")
    return tasks


def to_spider_task(task: dict[str, Any]) -> dict[str, Any]:
    """Create an adapter envelope without exposing reference answers."""
    task_id = str(task.get("id") or "").strip()
    question = str(task.get("question") or "").strip()
    if not task_id or not question:
        raise ValueError("Task requires non-empty id and question")
    return {
        "instance_id": task_id,
        "question": question,
        "type": "BuySideFlow",
        "mode": str(task.get("mode") or "sql"),
        "schema_tables": list(task.get("schema_tables") or []),
        "output_contract": str(task.get("output_contract") or ""),
        "evaluation_contract": str(task.get("evaluation_contract") or ""),
    }


def find_task(task_id: str, path: str | Path = CANONICAL_DATASET) -> dict[str, Any]:
    matches = [task for task in load_tasks(path) if task.get("id") == task_id]
    if len(matches) != 1:
        raise KeyError(f"Expected one task named {task_id!r}, found {len(matches)}")
    return matches[0]
