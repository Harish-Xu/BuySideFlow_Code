"""Replay reference implementations in isolation and compare regenerated artifacts.

The source dataset is read-only.  Each task directory is copied to a temporary
tree before ``refer.py`` is executed.  Regenerated artifacts are compared with
the stored reference artifacts through the existing Text2SQL evaluator.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
PRIMARY_ARTIFACT_NAMES = {"result.csv", "picture.png", "abstract.txt"}

sys.path.insert(0, str(PROJECT_ROOT))

from sweagent.text2sql.evaluator import compare_items  # noqa: E402
from sweagent.text2sql.markdown import parse_text2sql_tasks  # noqa: E402
from sweagent.text2sql.provenance import (  # noqa: E402
    aggregate_sha256,
    reference_files_from_tasks,
    require_db_snapshot_id,
    sha256_file,
)


def _artifact_descriptors(task: dict[str, Any], directory: Path) -> list[dict[str, str]]:
    descriptors: list[dict[str, str]] = []
    for original in task.get("reference_artifact_paths") or []:
        name = str(original.get("name") or Path(str(original.get("path") or "")).name)
        if not name:
            continue
        path = directory / name
        if path.exists() and path.is_file():
            descriptors.append({"kind": str(original.get("kind") or ""), "name": name, "path": str(path)})
    return descriptors


def _artifact_hashes(descriptors: list[dict[str, str]]) -> dict[str, str]:
    return {item["name"]: sha256_file(item["path"]) for item in descriptors}


def _read_csv_rows(path: Path) -> list[list[str]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return list(csv.reader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return []


def _tabular_diff_summary(
    originals: list[dict[str, str]],
    generated: list[dict[str, str]],
) -> dict[str, Any]:
    original_by_name = {item["name"]: item for item in originals if item.get("kind") == "csv"}
    generated_by_name = {item["name"]: item for item in generated if item.get("kind") == "csv"}
    summary: dict[str, Any] = {}
    for name in sorted(set(original_by_name) | set(generated_by_name)):
        original = original_by_name.get(name)
        replayed = generated_by_name.get(name)
        if original is None or replayed is None:
            summary[name] = {"present_in_stored": original is not None, "present_in_replay": replayed is not None}
            continue
        stored_rows = _read_csv_rows(Path(original["path"]))
        replay_rows = _read_csv_rows(Path(replayed["path"]))
        stored_header = stored_rows[0] if stored_rows else []
        replay_header = replay_rows[0] if replay_rows else []
        stored_body = Counter(tuple(row) for row in stored_rows[1:])
        replay_body = Counter(tuple(row) for row in replay_rows[1:])
        shared = sum((stored_body & replay_body).values())
        summary[name] = {
            "stored_rows": max(len(stored_rows) - 1, 0),
            "replay_rows": max(len(replay_rows) - 1, 0),
            "stored_columns": len(stored_header),
            "replay_columns": len(replay_header),
            "header_match": stored_header == replay_header,
            "unordered_exact_shared_rows": shared,
            "stored_only_rows": sum((stored_body - replay_body).values()),
            "replay_only_rows": sum((replay_body - stored_body).values()),
        }
    return summary


def _clean_message(text: str, *, replay_root: Path, limit: int = 2000) -> str:
    cleaned = text
    path_replacements = [
        (PROJECT_ROOT.resolve(), "<project_root>"),
        (replay_root.resolve(), "<replay_root>"),
    ]
    for path, replacement in path_replacements:
        variants = {str(path), str(path.absolute())}
        if str(path).startswith("/private/"):
            variants.add(str(path)[8:])
        else:
            variants.add(f"/private{path}")
        for variant in sorted(variants, key=len, reverse=True):
            cleaned = cleaned.replace(variant, replacement)
    cleaned = cleaned.strip()
    return cleaned[-limit:]


def _subprocess_environment(replay_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    values = dotenv_values(PROJECT_ROOT / ".env")
    env.update({key: value for key, value in values.items() if value is not None})
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(PROJECT_ROOT), str(replay_root), existing) if part)
    env["MPLBACKEND"] = "Agg"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _copy_task(task: dict[str, Any], replay_root: Path) -> Path:
    source = Path(task["result_dir"])
    target = replay_root / "data" / "dataset" / "results" / str(task["id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    declared = {item["name"] for item in task.get("reference_artifact_paths") or []}
    declared.update((task.get("comparison_rules") or {}).get("raw_artifacts", []))
    for name in PRIMARY_ARTIFACT_NAMES | declared:
        if Path(name).name != name:
            raise ValueError(f"Invalid reference artifact filename: {name}")
        path = target / name
        if path.exists():
            path.unlink()
    return target


def _execute_task(
    task: dict[str, Any],
    *,
    replay_root: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> dict[str, Any]:
    qid = str(task["id"])
    started = time.monotonic()
    task_dir = _copy_task(task, replay_root)
    script = task_dir / "refer.py"
    record: dict[str, Any] = {
        "qid": qid,
        "domain": qid.split("_", 1)[0],
        "evaluation_kind": str(task.get("evaluation_kind") or ""),
        "strict_output_schema": bool(task.get("strict_output_schema", False)),
        "execution_status": "not_run",
        "returncode": "",
        "artifact_status": "not_compared",
        "evaluator_passed": False,
        "evaluator_score": 0.0,
        "expected_artifacts": ";".join(item["name"] for item in task.get("reference_artifact_paths") or []),
        "generated_artifacts": "",
        "stored_artifact_sha256": "",
        "replay_artifact_sha256": "",
        "tabular_diff": "",
        "duration_seconds": 0.0,
        "error_type": "",
        "error_message": "",
        "stdout_tail": "",
        "stderr_tail": "",
        "score_details": "",
    }
    if not script.exists():
        record["execution_status"] = "missing_refer_py"
        record["error_type"] = "missing_refer_py"
        record["error_message"] = "refer.py is missing"
        record["duration_seconds"] = round(time.monotonic() - started, 3)
        return record

    try:
        completed = subprocess.run(
            [sys.executable, "refer.py"],
            cwd=task_dir,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        record["returncode"] = completed.returncode
        record["stdout_tail"] = _clean_message(completed.stdout, replay_root=replay_root)
        record["stderr_tail"] = _clean_message(completed.stderr, replay_root=replay_root)
        if completed.returncode != 0:
            record["execution_status"] = "execution_error"
            record["error_type"] = "execution_error"
            record["error_message"] = record["stderr_tail"] or record["stdout_tail"]
            return record
        record["execution_status"] = "success"
    except subprocess.TimeoutExpired as exc:
        record["execution_status"] = "timeout"
        record["error_type"] = "timeout"
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        record["stdout_tail"] = _clean_message(stdout, replay_root=replay_root)
        record["stderr_tail"] = _clean_message(stderr, replay_root=replay_root)
        record["error_message"] = f"refer.py exceeded {timeout_seconds} seconds"
        return record
    except Exception as exc:
        record["execution_status"] = "harness_error"
        record["error_type"] = type(exc).__name__
        record["error_message"] = _clean_message(str(exc), replay_root=replay_root)
        return record
    finally:
        record["duration_seconds"] = round(time.monotonic() - started, 3)

    generated = _artifact_descriptors(task, task_dir)
    record["generated_artifacts"] = ";".join(item["name"] for item in generated)
    expected_names = {item["name"] for item in task.get("reference_artifact_paths") or []}
    generated_names = {item["name"] for item in generated}
    if generated_names != expected_names:
        missing = sorted(expected_names - generated_names)
        extra = sorted(generated_names - expected_names)
        record["artifact_status"] = "missing_artifact"
        record["error_type"] = "missing_artifact"
        record["error_message"] = json.dumps({"missing": missing, "extra": extra}, ensure_ascii=False)
        return record

    originals = task.get("reference_artifact_paths") or []
    original_hashes = _artifact_hashes(originals)
    generated_hashes = _artifact_hashes(generated)
    record["stored_artifact_sha256"] = json.dumps(original_hashes, ensure_ascii=False, sort_keys=True)
    record["replay_artifact_sha256"] = json.dumps(generated_hashes, ensure_ascii=False, sort_keys=True)
    record["tabular_diff"] = json.dumps(
        _tabular_diff_summary(originals, generated),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if original_hashes == generated_hashes:
        record["artifact_status"] = "exact_match"
        record["evaluator_passed"] = True
        record["evaluator_score"] = 1.0
        return record

    original_csv = [item for item in originals if item.get("kind") == "csv"]
    generated_csv = [item for item in generated if item.get("kind") == "csv"]
    has_non_tabular = any(item.get("kind") != "csv" for item in originals)
    reference_for_compare = task
    artifacts_for_compare = generated
    if has_non_tabular:
        if not original_csv or not generated_csv:
            record["artifact_status"] = "non_tabular_changed"
            record["error_type"] = "non_tabular_changed"
            record["error_message"] = "Non-tabular artifact hash changed; manual review required"
            return record
        reference_for_compare = {
            **task,
            "evaluation_kind": "csv",
            "reference_artifact_paths": original_csv,
        }
        artifacts_for_compare = generated_csv

    generated_item = {
        "id": qid,
        "question": task.get("question", ""),
        "mode": "sql",
        "reference_artifact_paths": artifacts_for_compare,
        "evaluation_kind": reference_for_compare.get("evaluation_kind"),
        "strict_output_schema": task.get("strict_output_schema", False),
    }
    try:
        comparison = compare_items(reference_for_compare, generated_item)
    except Exception as exc:
        record["artifact_status"] = "comparison_error"
        record["error_type"] = type(exc).__name__
        record["error_message"] = _clean_message(str(exc), replay_root=replay_root)
        return record
    record["evaluator_passed"] = bool(comparison.passed)
    record["evaluator_score"] = float(comparison.score)
    record["score_details"] = json.dumps(comparison.score_details, ensure_ascii=False, separators=(",", ":"))
    if comparison.passed and has_non_tabular:
        record["artifact_status"] = "non_tabular_changed"
        record["error_type"] = "non_tabular_changed"
        record["error_message"] = "Tabular artifact is equivalent; non-tabular artifact hash changed"
    elif comparison.passed:
        record["artifact_status"] = "evaluator_equivalent"
    else:
        record["artifact_status"] = "mismatch"
        record["error_type"] = comparison.error_type or "mismatch"
        record["error_message"] = _clean_message(comparison.error_msg or "", replay_root=replay_root)
    return record


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = list(records[0]) if records else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def _summary(
    records: list[dict[str, Any]],
    *,
    db_snapshot_id: str,
    reference_sha_before: str,
    reference_sha_after: str,
    reference_file_count: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    artifact_counts = Counter(str(row["artifact_status"]) for row in records)
    execution_counts = Counter(str(row["execution_status"]) for row in records)
    validated = sum(row["artifact_status"] in {"exact_match", "evaluator_equivalent"} for row in records)
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "db_snapshot_id": db_snapshot_id,
        "task_count": len(records),
        "validated_count": validated,
        "execution_status": dict(sorted(execution_counts.items())),
        "artifact_status": dict(sorted(artifact_counts.items())),
        "reference_file_count": reference_file_count,
        "reference_sha256_before": reference_sha_before,
        "reference_sha256_after": reference_sha_after,
        "source_unchanged": reference_sha_before == reference_sha_after,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "method": "isolated refer.py subprocess; regenerated artifacts compared by the existing evaluator",
    }


def _render_markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    domains = ["bond", "fund", "macro", "stock"]
    statuses = [
        "exact_match",
        "evaluator_equivalent",
        "mismatch",
        "missing_artifact",
        "non_tabular_changed",
        "not_compared",
    ]
    lines = [
        "# Reference replay report",
        "",
        f"- Database snapshot: `{summary['db_snapshot_id']}`",
        f"- Tasks recorded: {summary['task_count']}",
        f"- Validated: {summary['validated_count']}",
        f"- Source reference files unchanged: {'yes' if summary['source_unchanged'] else 'no'}",
        f"- Elapsed seconds: {summary['elapsed_seconds']}",
        "",
        "## Artifact outcome by domain",
        "",
        "| domain | " + " | ".join(statuses) + " | total |",
        "| --- | " + " | ".join("---:" for _ in statuses) + " | ---: |",
    ]
    for domain in domains:
        domain_rows = [row for row in records if row["domain"] == domain]
        counts = Counter(row["artifact_status"] for row in domain_rows)
        lines.append(
            f"| {domain} | " + " | ".join(str(counts[status]) for status in statuses) + f" | {len(domain_rows)} |"
        )
    lines.extend(["", "## Unvalidated tasks", ""])
    for status in statuses[2:]:
        qids = [row["qid"] for row in records if row["artifact_status"] == status]
        if qids:
            lines.append(f"- `{status}` ({len(qids)}): " + ", ".join(qids))
    cause_groups: dict[str, list[str]] = {}
    for row in records:
        message = str(row.get("error_message") or "")
        execution_status = str(row.get("execution_status") or "")
        artifact_status = str(row.get("artifact_status") or "")
        if "No module named 'src'" in message:
            cause = "missing_shared_helper"
        elif "has no attribute 'close_all'" in message:
            cause = "connector_api_mismatch"
        elif "maximum statement execution time exceeded" in message:
            cause = "database_statement_timeout"
        elif "SQL result missing columns" in message:
            cause = "script_output_schema_error"
        elif execution_status == "timeout":
            cause = "process_timeout"
        elif execution_status == "execution_error":
            cause = "other_execution_error"
        elif artifact_status == "missing_artifact":
            cause = "missing_generated_artifact"
        elif artifact_status == "mismatch":
            cause = "stored_artifact_mismatch"
        elif artifact_status == "non_tabular_changed":
            cause = "changed_non_tabular_artifact"
        else:
            continue
        cause_groups.setdefault(cause, []).append(str(row["qid"]))
    lines.extend(["", "## Failure grouping", ""])
    for cause, qids in sorted(cause_groups.items()):
        lines.append(f"- `{cause}` ({len(qids)}): " + ", ".join(qids))
    execution_counts = Counter(row["execution_status"] for row in records)
    lines.extend(
        [
            "",
            "## Execution outcome",
            "",
            *[f"- `{status}`: {count}" for status, count in sorted(execution_counts.items())],
            "",
            "## Interpretation",
            "",
            "A task is validated only when the regenerated artifacts are byte-identical or pass the existing structured evaluator. "
            "Execution errors, timeouts, missing artifacts, changed non-tabular artifacts, and result mismatches remain unvalidated. "
            "The replay runs in a temporary dataset tree and does not overwrite stored reference artifacts.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=PROJECT_ROOT / "data" / "dataset")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs" / "reference_replay")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900, help="Per-task timeout in seconds.")
    parser.add_argument("--qids", help="Optional comma-separated task IDs.")
    parser.add_argument(
        "--replay-root",
        type=Path,
        help="Optional empty directory in which regenerated task artifacts are retained for diagnosis.",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    db_snapshot_id = require_db_snapshot_id()
    selected = {item.strip() for item in args.qids.split(",") if item.strip()} if args.qids else None
    tasks = parse_text2sql_tasks(
        args.data, task_filter=selected.__contains__ if selected is not None else None
    )
    if selected is not None:
        missing = selected - {str(task["id"]) for task in tasks}
        if missing:
            raise ValueError(f"Unknown qids: {sorted(missing)}")
    if not tasks:
        raise ValueError("No tasks selected")

    reference_paths = reference_files_from_tasks(tasks)
    reference_sha_before, reference_file_count = aggregate_sha256(reference_paths, base_path=args.data)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    if args.replay_root is not None:
        replay_root = args.replay_root.resolve()
        if replay_root.exists() and any(replay_root.iterdir()):
            raise ValueError(f"Replay root must be empty: {replay_root}")
        replay_root.mkdir(parents=True, exist_ok=True)
        replay_context = nullcontext(str(replay_root))
    else:
        replay_context = tempfile.TemporaryDirectory(prefix="buysideflow-reference-replay-")

    with replay_context as temp_dir:
        replay_root = Path(temp_dir)
        (replay_root / "data" / "dataset" / "results").mkdir(parents=True)
        (replay_root / "sweagent").symlink_to(PROJECT_ROOT / "sweagent", target_is_directory=True)
        env = _subprocess_environment(replay_root)
        records: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(
                    _execute_task,
                    task,
                    replay_root=replay_root,
                    timeout_seconds=args.timeout,
                    env=env,
                ): str(task["id"])
                for task in tasks
            }
            completed_count = 0
            for future in as_completed(futures):
                record = future.result()
                records.append(record)
                completed_count += 1
                print(
                    f"[{completed_count}/{len(tasks)}] {record['qid']} "
                    f"{record['execution_status']} {record['artifact_status']}",
                    flush=True,
                )

    order = {str(task["id"]): index for index, task in enumerate(tasks)}
    records.sort(key=lambda row: order[str(row["qid"])])
    reference_sha_after, reference_file_count_after = aggregate_sha256(reference_paths, base_path=args.data)
    if reference_file_count_after != reference_file_count:
        raise RuntimeError("Reference file count changed during replay")
    summary = _summary(
        records,
        db_snapshot_id=db_snapshot_id,
        reference_sha_before=reference_sha_before,
        reference_sha_after=reference_sha_after,
        reference_file_count=reference_file_count,
        elapsed_seconds=time.monotonic() - started,
    )
    _write_csv(args.output_dir / "reference_replay_results.csv", records)
    (args.output_dir / "reference_replay_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "reference_replay_report.md").write_text(
        _render_markdown(summary, records),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validated_count"] == len(records) and summary["source_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
