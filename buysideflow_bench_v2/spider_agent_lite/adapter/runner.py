from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from sweagent.text2sql.provenance import require_db_snapshot_id
from sweagent.text2sql.runtime import get_run_code_count

from .agent import create_agent
from .assets import shared_asset_manifest
from .environment import BuySideFlowEnv
from .model_gateway import configure_model_retries, resolve_model_name
from .problem import build_problem_statement
from .reporting import export_run_reports, workflow_model_name
from .trajectory import normalize_trajectory


RUNTIME_DISTRIBUTIONS = (
    "dashscope",
    "docker",
    "google-generativeai",
    "groq",
    "gymnasium",
    "openai",
    "requests",
    "tiktoken",
)


def _runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in RUNTIME_DISTRIBUTIONS:
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            versions[distribution] = "missing"
    return versions


def run_task(
    task_id: str,
    *,
    model: str,
    output_dir: str | Path,
    run_name: str | None = None,
    max_steps: int = 300,
    max_memory_length: int = 25,
    max_tokens: int = 65536,
    temperature: float = 0.0,
    top_p: float | None = None,
    model_retry_attempts: int = 20,
    model_retry_min_wait: float = 10.0,
    model_retry_max_wait: float = 120.0,
    max_run_code_calls: int = 100,
    export_reports: bool = True,
) -> Path:
    snapshot_id = require_db_snapshot_id()
    problem_statement = build_problem_statement(task_id)
    resolved_model = resolve_model_name(model)
    resolved_run_name = run_name or datetime.now().astimezone().strftime("%Y%m%d@%H%M%S.%f%z")
    if resolved_run_name in {"", ".", ".."} or Path(resolved_run_name).name != resolved_run_name:
        raise ValueError("run_name must be one path component")
    target = Path(output_dir).resolve() / resolved_run_name / workflow_model_name(resolved_model) / task_id
    if target.exists():
        raise FileExistsError(f"Run output already exists: {target}")
    target.mkdir(parents=True)
    result_path = target / "result.json"

    configure_model_retries(
        attempts=model_retry_attempts,
        min_wait=model_retry_min_wait,
        max_wait=model_retry_max_wait,
    )
    env = BuySideFlowEnv(problem_statement, max_run_code_calls=max_run_code_calls)
    agent = create_agent(
        model=resolved_model,
        max_tokens=max_tokens,
        top_p=top_p,
        temperature=temperature,
        max_memory_length=max_memory_length,
        max_steps=max_steps,
        use_plan=False,
    )
    agent.set_env_and_task(env)

    started_at = datetime.now().astimezone().isoformat()
    run_error = ""
    try:
        finished, result = agent.run()
        trajectory = agent.get_trajectory()
    except Exception as exc:
        finished = False
        result = f"ERROR: {type(exc).__name__}: {exc}"
        run_error = result
        trajectory = agent.get_trajectory()
    finally:
        env.close()
    finished_at = datetime.now().astimezone().isoformat()

    upstream_manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "UPSTREAM_MANIFEST.json").read_text()
    )
    payload: dict[str, Any] = {
        "finished": bool(finished),
        "steps": len(trajectory.get("trajectory") or []),
        "result": result,
        "submission": env.submission,
        "error": run_error,
        **trajectory,
        "adapter": {
            "schema": "buysideflow-spider-agent-lite/v1",
            "run_name": resolved_run_name,
            "requested_model": model,
            "model": resolved_model,
            "upstream_commit": upstream_manifest["commit"],
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "runtime_packages": _runtime_versions(),
            "max_steps": max_steps,
            "max_memory_length": max_memory_length,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "model_retry_attempts": model_retry_attempts,
            "model_retry_min_wait": model_retry_min_wait,
            "model_retry_max_wait": model_retry_max_wait,
            "max_run_code_calls": max_run_code_calls,
            "actual_run_code_calls": get_run_code_count(task_id),
            "db_snapshot_id": snapshot_id,
            "db_snapshot_cutoff": os.getenv("DB_SNAPSHOT_CUTOFF", ""),
            "shared_assets": shared_asset_manifest(),
            "started_at": started_at,
            "finished_at": finished_at,
        },
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    normalized = normalize_trajectory(result_path, task_id)
    (target / "trajectory.normalized.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    if env.submission is not None:
        (target / "submission.json").write_text(
            json.dumps(env.submission, ensure_ascii=False, indent=2) + "\n"
        )
    if export_reports:
        export_run_reports(target.parent)
    return result_path
