from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def normalize_trajectory(raw_result_path: str | Path, task_id: str) -> dict[str, Any]:
    """Index an upstream result while keeping its raw evidence as the authority."""
    raw_path = Path(raw_result_path).resolve()
    raw_bytes = raw_path.read_bytes()
    raw = json.loads(raw_bytes)
    trajectory = raw.get("trajectory") or []
    return {
        "adapter_schema": "buysideflow-spider-agent-lite/v1",
        "task_id": task_id,
        "finished": bool(raw.get("finished")),
        "steps": int(raw.get("steps", len(trajectory))),
        "result": raw.get("result", ""),
        "trajectory": trajectory,
        "raw_result_path": str(raw_path),
        "raw_result_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }

