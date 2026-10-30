from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

from sweagent.run.hooks.text2sql_evaluate import build_tool_use_record, write_tool_use_csv

from .assets import PROJECT_ROOT, sha256_file
from .task_loader import CANONICAL_DATASET, load_tasks
from .trajectory import normalize_trajectory


def workflow_model_name(model: str) -> str:
    return "spider_agent_lite__" + model.replace("/", "--")


def _batch_metadata(root: Path) -> dict:
    from .resume import resume_history, validate_resume
    history = resume_history(root)
    if history:
        _, manifest = history[-1]
        validate_resume(root, manifest)
        return manifest
    path = root / "batch_manifest.json"
    if not path.is_file():
        return {}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "buysideflow-spider-batch/v1":
        raise ValueError("Unknown Spider batch manifest schema")
    if manifest.get("status") in {"running", "stopping"}:
        raise ValueError("Batch is still running; only its coordinator may export after workers stop")
    if manifest.get("status") == "provenance_error":
        raise ValueError("Batch provenance failed; refusing to export a comparable report")
    for name, digest in manifest.get("sources", {}).items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid batch source path")
        source = PROJECT_ROOT / relative
        if not source.is_file() or sha256_file(source) != digest:
            raise ValueError(f"Batch source changed: {name}")
    return manifest


def export_run_reports(model_dir: str | Path, *, expected_task_ids: list[str] | None = None) -> Path:
    """Export generation evidence without calling a model, database, or evaluator."""
    root = Path(model_dir).resolve()
    batch = _batch_metadata(root)
    if expected_task_ids is None and batch:
        expected_task_ids = batch["expected_task_ids"]
    if expected_task_ids is not None:
        if (not isinstance(expected_task_ids, (list, tuple)) or not expected_task_ids
                or any(not isinstance(qid, str) or qid in {"", ".", ".."}
                       or Path(qid).name != qid for qid in expected_task_ids)
                or len(set(expected_task_ids)) != len(expected_task_ids)):
            raise ValueError("Expected task ids must be nonempty, unique path components")
        if batch and list(expected_task_ids) != batch["expected_task_ids"]:
            raise ValueError("Expected task ids differ from the recorded batch")
    paths = sorted(root.glob("*/result.json"))
    if not paths and not (batch and expected_task_ids):
        raise ValueError(f"No task result.json files under {root}")
    if expected_task_ids is not None:
        unexpected = {path.parent.name for path in paths} - set(expected_task_ids)
        if unexpected:
            raise ValueError("Unexpected task results: " + ", ".join(sorted(unexpected)))
    results, unreadable = {}, {}
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(raw, dict) or not isinstance(raw.get("adapter"), dict)
                    or any(not isinstance(raw["adapter"].get(key), str) or not raw["adapter"][key]
                           for key in ("model", "run_name", "db_snapshot_id"))
                    or not isinstance(raw["adapter"].get("db_snapshot_cutoff", ""), str)
                    or not isinstance(raw.get("trajectory") or [], list)
                    or (raw.get("submission") is not None and not isinstance(raw["submission"], dict))):
                raise ValueError("Invalid raw result structure")
        except (ValueError, OSError) as exc:
            if expected_task_ids is None:
                raise
            unreadable[path.parent.name] = type(exc).__name__
            continue
        results[path.parent.name] = (path, raw)
    identities = {
        (raw["adapter"]["model"], raw["adapter"]["run_name"],
         raw["adapter"]["db_snapshot_id"], raw["adapter"].get("db_snapshot_cutoff", ""))
        for _, raw in results.values()
    }
    if batch:
        identities.add((batch["model"], batch["run_name"], batch["db_snapshot_id"], batch.get("db_snapshot_cutoff", "")))
    if len(identities) != 1:
        raise ValueError("Cannot combine different models, runs, or database snapshots")
    model, run_name, snapshot, cutoff = identities.pop()
    stem = f"{workflow_model_name(model)}_{run_name}"
    if Path(stem).name != stem:
        raise ValueError("Report name must be one path component")
    tasks = {task["id"]: task for task in load_tasks()}
    task_ids = list(expected_task_ids) if expected_task_ids is not None else sorted(results)
    preds = {}
    behaviors = []
    rows = []
    accepted = 0
    statuses = []
    for qid in task_ids:
        if qid not in results:
            # This is an absent prediction, not a fabricated submission/trajectory.
            preds[qid] = {"instance_id": qid, "model_name_or_path": model, "model_patch": ""}
            behaviors.append({"qid": qid, "difficulty": str(tasks.get(qid, {}).get("difficulty") or "unknown")})
            status = "unreadable" if qid in unreadable else "missing"
            if qid in batch.get("worker_errors", {}):
                status = "worker_error"
            statuses.append(status)
            evidence = f"[{qid}/result.json]({qid}/result.json)" if qid in unreadable else "not recorded"
            digest = sha256_file(root / qid / "result.json") if qid in unreadable else ""
            rows.append(f"| {qid} | {status} | unknown | unknown | unknown | unknown | unknown | {evidence} | {digest} |")
            continue
        path, raw = results[qid]
        trajectory = raw.get("trajectory") or []
        submission = raw.get("submission")
        has_submission = isinstance(submission, dict) and bool(submission)
        accepted += int(has_submission)
        preds[qid] = {
            "instance_id": qid,
            "model_name_or_path": model,
            "model_patch": json.dumps(submission, ensure_ascii=False) if has_submission else "",
        }
        normalized = normalize_trajectory(path, qid)
        normalized_path = path.parent / "trajectory.normalized.json"
        reused = qid in batch.get("reused_task_ids", [])
        if not reused:
            normalized_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # The standalone evaluator reads this trajectory format for tool counts.
        traj_path = path.parent / f"{qid}.traj"
        if not reused or not traj_path.exists():
            traj_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        behavior = build_tool_use_record(
            qid=qid, difficulty=str(tasks.get(qid, {}).get("difficulty") or "unknown"),
            trajectory=trajectory,
        )
        behaviors.append(behavior)
        status = "submitted" if has_submission else "incomplete"
        if raw.get("error") or qid in batch.get("worker_errors", {}):
            status = "error"
        statuses.append(status)
        rows.append(
            f"| {qid} | {status} | {len(trajectory)} | {behavior['schema_explore_cnt']} | "
            f"{behavior['run_code_cnt']} | {raw['adapter'].get('actual_run_code_calls', 'unknown')} | "
            f"{behavior['submit_cnt']} | [{path.name}]({qid}/result.json) | "
            f"{hashlib.sha256(path.read_bytes()).hexdigest()} |"
        )
    preds_path = root / "preds.json"
    preds_path.write_text(json.dumps(preds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tool_path = root / f"tool_use_{stem}_generation.csv"
    write_tool_use_csv(tool_path, behaviors, [])
    eval_report = root / f"benchmark_{stem}.md"
    eval_tools = root / f"tool_use_{stem}.csv"
    command = shlex.join([
        "python", str(PROJECT_ROOT / "run_eval.py"), "--data", str(CANONICAL_DATASET),
        "--preds", str(preds_path), "--trajectory-dir", str(root),
        "--model-name", workflow_model_name(model), "--output", str(eval_report),
        "--tool-use-output", str(eval_tools), "--num-workers", "1",
    ])
    if batch:
        verify_command = shlex.join([
            "python", str(PROJECT_ROOT / "spider_agent_lite/run_text2sql.py"),
            "--report-only", str(root),
        ])
        command = verify_command + " && " + command
    lines = [
        "# Spider Agent Lite Generation Report", "",
        "Evaluation status: NOT EVALUATED. Submission acceptance is not a passing score.",
        "Tool-use score fields are blank until independent evaluation.", "",
        f"- Workflow: spider_agent_lite", f"- Model: {model}", f"- Run: {run_name}",
        f"- Database snapshot: {snapshot}", f"- Snapshot cutoff: {cutoff or '(not recorded)'}",
        f"- Recorded tasks: {len(results)}", f"- Accepted submissions: {accepted}",
        f"- Tool use: [{tool_path.name}]({tool_path.name})",
        "- Predictions: [preds.json](preds.json)", "",
        ("Each row represents one expected task; missing evidence remains explicitly unknown."
         if expected_task_ids is not None else "Each row represents an available raw result, not the full benchmark."),
        "Turns count recorded upstream trajectory steps, not model API attempts or retries.",
        "Tool counts include recorded attempts, including rejected calls. Runtime run_code counts",
        "are listed separately. Raw results retain prompts, settings, timestamps and asset hashes.", "",
        "| Task | Status | Turns | Schema calls | run_code attempts | Runtime run_code calls | submit attempts | Raw evidence | SHA256 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        *rows, "", "## Independent Evaluation", "",
        "Run with the recorded snapshot and the normal benchmark environment. This executes",
        "candidate SQL/Python and evaluates against the current canonical references; archive",
        "the resulting report with its provenance if those references have changed since generation.",
        "The command writes separate scored report and tool-use files.", "", "```bash",
        f"export DB_SNAPSHOT_ID={shlex.quote(snapshot)}",
    ]
    if batch:
        verification_note = "For this batch the command first rechecks the recorded input/source fingerprints; evaluation runs only if that succeeds."
        lines.insert(lines.index("The command writes separate scored report and tool-use files."), verification_note)
    if expected_task_ids is not None:
        coverage = [
            f"- Expected tasks: {len(task_ids)}",
            f"- Completed agent loops: {sum(bool(raw.get('finished')) for _, raw in results.values())}",
            f"- Submitted tasks: {statuses.count('submitted')}",
            f"- Unsubmitted recorded tasks: {statuses.count('incomplete')}",
            f"- Generation/worker errors: {sum(status in {'error', 'worker_error', 'unreadable'} for status in statuses)}",
            f"- Missing/unreadable raw results: {len(task_ids) - len(results)}",
            f"- Configured task workers: {batch.get('num_workers', 'not recorded')}",
            "- No automatic model retries based on scores; no independent evaluation has been run.",
        ]
        if batch:
            ledger = batch.get("manifest_name", "resume_manifest.json") if "reused_task_ids" in batch else "batch_manifest.json"
            coverage.extend([f"- Batch status: {batch['status']}", f"- Batch provenance and worker failures: [{ledger}]({ledger})"])
            if "reused_task_ids" in batch:
                coverage.extend([
                    f"- Reused historical tasks: {len(batch['reused_task_ids'])}",
                    f"- Newly scheduled tasks: {len(batch['scheduled_task_ids'])}",
                    f"- Generation source segments: {len(batch.get('history_manifests', []))+2}; not one historical global dataset version.",
                    "- Historical prompts, runtime/budgets, per-task references and comparison rules verified compatible.",
                    "- Original failed provenance manifest retained unchanged: [batch_manifest.json](batch_manifest.json)",
                    "- Per-task generation origins and byte-preservation proof are recorded in the continuation ledger.",
                    f"- Restarted interrupted tasks with fresh original budgets: {batch.get('restarted_incomplete_task_ids', [])}",
                    f"- Deferred incomplete tasks (not model failures): {batch.get('deferred_task_ids', [])}",
                    "- Crash-recovery receipts and preserved partial artifacts are linked by SHA256 in the ledger.",
                    f"- Process-local historical generation comparator SHA256: {batch.get('historical_comparison_sha256') or 'not overridden'}",
                    "- Independent evaluation uses the current installed comparator; its version is separate from any generation override.",
                ])
        coverage_index = lines.index(f"- Recorded tasks: {len(results)}")
        lines[coverage_index:coverage_index] = coverage
    if cutoff:
        lines.append(f"export DB_SNAPSHOT_CUTOFF={shlex.quote(cutoff)}")
    lines.extend([command, "```", ""])
    report = root / f"benchmark_{stem}_generation.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report
