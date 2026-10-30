"""Fail-closed, segmented continuation. Historical manifests are never rewritten."""
from __future__ import annotations

import hashlib
import ast
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from .assets import PROJECT_ROOT, sha256_file, shared_asset_manifest

SCHEMA = "buysideflow-spider-resume/v1"
# These two coordinator/export files change to implement continuation; neither
# changes the per-task agent. The SWE agent loop is not used by Spider.
COORDINATOR_CHANGES = {
    "spider_agent_lite/run_text2sql.py",
    "spider_agent_lite/adapter/reporting.py",
    "spider_agent_lite/adapter/resume.py",
    "sweagent/agent/agents.py",
}
GLOBAL_DATA = {
    "data/dataset/question/dataset.jsonl",
    "data/dataset/manifest.generated.jsonl",
    "data/dataset/comparison_rules.json",
}
BUDGET_KEYS = (
    "max_steps", "max_memory_length", "max_tokens", "temperature", "top_p",
    "model_retry_attempts", "model_retry_min_wait", "model_retry_max_wait",
    "max_run_code_calls",
)


def file_inventory(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in protected task evidence: {path.name}")
        if path.is_file():
            result[str(path.relative_to(directory))] = sha256_file(path)
    return result


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def resume_history(root: Path) -> list[tuple[Path, dict]]:
    """Ordered, append-only ledgers; no mutable head pointer or file renaming."""
    paths = {}
    for path in root.glob("resume_manifest*.json"):
        if path.name.endswith(".recovery.json"):
            continue
        match = re.fullmatch(r"resume_manifest(?:\.(\d{3,}))?\.json", path.name)
        if not match or path.is_symlink():
            raise ValueError("Invalid continuation ledger filename")
        index = int(match[1]) if match[1] else 1
        expected_name = "resume_manifest.json" if index == 1 else f"resume_manifest.{index:03d}.json"
        if index < 1 or path.name != expected_name or index in paths:
            raise ValueError("Duplicate continuation segment")
        paths[index] = path
    if set(paths) != set(range(1, len(paths) + 1)):
        raise ValueError("Continuation ledger sequence has a gap")
    history = []
    for index in sorted(paths):
        path = paths[index]
        ledger = json.loads(path.read_text())
        recovery = path.with_suffix(".recovery.json")
        if recovery.exists():
            record = json.loads(recovery.read_text())
            if (record.get("schema") != "buysideflow-spider-crash-recovery/v1"
                    or record.get("manifest_sha256") != sha256_file(path)
                    or ledger.get("status") not in {"running", "stopping"}):
                raise ValueError("Invalid crash-recovery record or historical manifest changed")
            for qid, locations in record["preserved_partial_artifacts"].items():
                for location in locations:
                    preserved = root / location["path"]
                    if file_inventory(preserved) != location["files"]:
                        raise ValueError(f"Preserved interrupted evidence changed: {qid}")
            ledger = dict(ledger, status="interrupted", continuation_results=record["continuation_results"])
        history.append((path, ledger))
    return history


def recover_crashed_segment(root: Path, *, restart_incomplete: bool = False,
                            defer_incomplete: bool = False) -> Path:
    """Explicit recovery event, never a rewrite of an abruptly abandoned ledger."""
    history = resume_history(root)
    if not history:
        raise ValueError("No continuation to recover")
    path, ledger = history[-1]
    if ledger["status"] not in {"running", "stopping"}:
        raise ValueError("Latest continuation does not require crash recovery")
    processes = subprocess.run(["ps", "-eo", "pid,ppid,command"], check=True,
                               capture_output=True, text=True).stdout
    for line in processes.splitlines()[1:]:
        fields = line.split(None, 2)
        if len(fields) != 3 or int(fields[0]) in {os.getpid(), os.getppid()}:
            continue
        command = fields[2]
        if ("multiprocessing.spawn" in command or
                (ledger["run_name"] in command and
                 ("run_text2sql.py" in command or ".workspaces/" in command))):
            raise RuntimeError("A possible generation process is still active; refusing recovery")
    _check_evidence(root, ledger)
    results = {qid: sha256_file(root / qid / "result.json") for qid in ledger["scheduled_task_ids"]
               if (root / qid / "result.json").is_file()}
    partial = [qid for qid in ledger["scheduled_task_ids"] if qid not in results
               and ((root / qid).exists() or (root / ".workspaces" / qid).exists())]
    if restart_incomplete and defer_incomplete:
        raise ValueError("Cannot both restart and defer incomplete tasks")
    if partial and not (restart_incomplete or defer_incomplete):
        raise ValueError("Incomplete tasks have no saved trajectory; explicit restart approval is required: " + ", ".join(partial))
    record_path = path.with_suffix(".recovery.json")
    preserved = {}
    # Reserve before moving anything. A failed recovery remains fail-closed and
    # retains its original/archived files; it is never retried silently.
    with record_path.open("x", encoding="utf-8") as stream:
        record = {"schema": "buysideflow-spider-crash-recovery/v1", "manifest_sha256": sha256_file(path),
                  "continuation_results": results,
                  "restarted_incomplete_task_ids": partial if restart_incomplete else [],
                  "deferred_incomplete_task_ids": partial if defer_incomplete else [],
                  "preserved_partial_artifacts": preserved,
                  "reason": "No coordinator/execution process observed; ledger remained active after abrupt exit",
                  "abandoned_step_counts": "unknown; no saved agent trajectory", "restarted_budget": "fresh original per-task budget"}
        for qid in partial:
            locations = []
            for label, source in (("task", root / qid), ("workspace", root / ".workspaces" / qid)):
                if source.exists():
                    target = root / ".recovery" / path.stem / qid / label
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise FileExistsError("Recovery artifact target already exists")
                    files = file_inventory(source)
                    source.rename(target)
                    locations.append({"path": str(target.relative_to(root)), "files": files})
            preserved[qid] = locations
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return record_path


def _check_evidence(root: Path, manifest: dict) -> None:
    """Check historical bytes without requiring old sources to be today's sources."""
    if manifest.get("schema") != SCHEMA:
        raise ValueError("Unknown continuation manifest schema")
    expected, reused, pending = (manifest[key] for key in
                                 ("expected_task_ids", "reused_task_ids", "scheduled_task_ids"))
    if (any(not isinstance(qid, str) or qid in {"", ".", ".."} or Path(qid).name != qid for qid in expected)
            or len(set(expected)) != len(expected) or len(set(reused)) != len(reused)
            or len(set(pending)) != len(pending) or set(reused) & set(pending)
            or set(reused) | set(pending) != set(expected)
            or set(manifest["reuse_compatibility"]) != set(reused)):
        raise ValueError("Invalid continuation coverage/origin mapping")
    segment = manifest.get("manifest_name", "continuation")
    origins = {qid: receipt["origin"] for qid, receipt in manifest["reuse_compatibility"].items()}
    origins.update({qid: segment for qid in pending})
    if manifest["task_origins"] != origins:
        raise ValueError("Invalid continuation generation origins")
    if sha256_file(root / "batch_manifest.json") != manifest["original_manifest_sha256"]:
        raise ValueError("Original batch manifest was changed")
    for qid, receipt in manifest["reuse_compatibility"].items():
        current = file_inventory(root / qid)
        if any(current.get(name) != digest for name, digest in receipt["files"].items()):
            raise ValueError(f"Reused evidence changed: {qid}")
    for qid, digest in manifest.get("continuation_results", {}).items():
        if qid not in manifest["scheduled_task_ids"]:
            raise ValueError("Historical result outside scheduled segment")
        path = root / qid / "result.json"
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"Continuation raw results changed after generation: {qid}")


def _check_history_links(root: Path, manifest: dict, history: list[tuple[Path, dict]]) -> None:
    links = [{"path": path.name, "sha256": sha256_file(path)} for path, _ in history]
    if manifest.get("history_manifests", []) != links:
        raise ValueError("Historical continuation ledger changed or chain is incomplete")
    recoveries = [{"path": path.with_suffix(".recovery.json").name,
                   "sha256": sha256_file(path.with_suffix(".recovery.json"))}
                  for path, _ in history if path.with_suffix(".recovery.json").is_file()]
    if manifest.get("recovery_records", []) != recoveries:
        raise ValueError("Crash-recovery record changed or chain is incomplete")


def _gpt_only_addition_matches(source: str, historical_sha: str) -> bool:
    """Narrow compatibility proof: reconstruct the exact historical DeepSeek file."""
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "call_current_gpt55"]
    if len(functions) != 1:
        return False
    node = functions[0]
    if node.decorator_list or node.args.defaults or any(node.args.kw_defaults):
        return False
    lines = source.splitlines(keepends=True)
    if lines[node.lineno-3:node.lineno-1] != ["\n", "\n"]:
        return False
    del lines[node.lineno-3:node.end_lineno]
    restored = "".join(lines)
    new_doc = '    """Route models needing benchmark-specific API parameters."""'
    old_doc = '    """Route DeepSeek requests through the current benchmark model backend."""'
    branch = '        if model == "gpt-5.5":\n            return call_current_gpt55(routed_payload)\n'
    if restored.count(new_doc) != 1 or restored.count(branch) != 1:
        return False
    restored = restored.replace(new_doc, old_doc).replace(branch, "")
    return hashlib.sha256(restored.encode()).hexdigest() == historical_sha


def historical_comparison_source(historical_sha: str) -> str:
    source = (PROJECT_ROOT / "sweagent/text2sql/semantic_comparison.py").read_text()
    if hashlib.sha256(source.encode()).hexdigest() == historical_sha:
        return source
    start = source.index('    if rule.get("scalar_order"):', source.index('def _record'))
    end = source.index('    if len(frames) == 1 and frames[0].shape[1] == 2', start)
    old_block = '''    if len(frames) == len(fields) and rule.get("scalar_order"):
        if all(df.shape == (1, 1) and name(df.columns[0]) == "0" for df in frames):
            order = rule["scalar_order"]
            if len(order) != len(fields) or set(order) != {c["name"] for c in fields}:
                raise Difference("reference", "invalid scalar_order contract")
            return [pd.DataFrame([{field: df.iat[0, 0] for field, df in zip(order, frames, strict=True)}])]
'''
    restored = source[:start] + old_block + source[end:]
    if hashlib.sha256(restored.encode()).hexdigest() != historical_sha:
        raise ValueError("Cannot reconstruct exact historical comparison source")
    return restored


def install_historical_comparison(historical_sha: str) -> None:
    """Restore only the differing function in this process, never the shared file."""
    from sweagent.text2sql import semantic_comparison as module
    source = historical_comparison_source(historical_sha)
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_record")
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<hash-verified-historical-comparison>", "exec"), module.__dict__)


def _source_changes(old: dict, current: dict, owned: set[str], model: str, preserve_comparison: bool) -> dict:
    if any(Path(name).is_absolute() or ".." in Path(name).parts for name in old):
        raise ValueError("Invalid historical source path")
    changes = {name: {"original": old.get(name), "current": current.get(name)}
               for name in set(old) | set(current) if old.get(name) != current.get(name)}
    for name in changes:
        if name in COORDINATOR_CHANGES | GLOBAL_DATA:
            continue
        if name == "sweagent/text2sql/semantic_comparison.py" and preserve_comparison:
            historical_comparison_source(old[name])
            changes[name]["reason"] = "Exact historical SHA256 reconstructed; historical _record installed process-locally"
            continue
        if (name == "spider_agent_lite/adapter/model_gateway.py" and model.startswith("deepseek/")
                and old.get(name) and _gpt_only_addition_matches((PROJECT_ROOT / name).read_text(), old[name])):
            changes[name]["reason"] = "GPT-only additions removed reconstruct exact historical SHA256; DeepSeek path unchanged"
            continue
        if name.startswith("data/dataset/results/") and not any(
                name.startswith(f"data/dataset/results/{qid}/") for qid in owned):
            continue
        # Older manifests froze the whole SWE tree. Scope reduction is recorded,
        # not a waiver: dropped files must still match their historical bytes.
        if name.startswith("sweagent/") and name not in current:
            path = PROJECT_ROOT / name
            if path.is_file() and sha256_file(path) == old[name]:
                changes[name]["reason"] = "unchanged_file_outside_current_import_scope"
                continue
        raise ValueError(f"Generation-critical source changed: {name}")
    return changes


def _generation_owners(original: dict, history: list[tuple[Path, dict]], initial_ids: list[str]) -> dict:
    first_ids = history[0][1]["reused_task_ids"] if history else initial_ids
    owners = {qid: ("original_batch", original) for qid in first_ids}
    for path, ledger in history:
        if set(ledger["reused_task_ids"]) != set(owners):
            raise ValueError("Historical reuse coverage differs from prior generation owners")
        if any(receipt["origin"] != owners[qid][0] for qid, receipt in ledger["reuse_compatibility"].items()):
            raise ValueError("Historical reused-task origin changed")
        for qid in ledger.get("continuation_results", {}):
            if qid in owners:
                raise ValueError("Task was generated in more than one segment")
            owners[qid] = (path.name, ledger)
    return owners


def prepare_resume(root: Path, settings: dict, sources: dict, task_ids: list[str],
                   snapshot: str, rules_archive: Path, *, preserve_comparison: bool = False) -> dict:
    """Read-only compatibility proof; call before creating a continuation ledger."""
    from .actions import ACTION_CLASSES
    from .problem import build_problem_statement
    from .prompt import build_system_prompt
    from .runner import _runtime_versions
    from .model_gateway import resolve_model_name

    original_path = root / "batch_manifest.json"
    original = json.loads(original_path.read_text())
    if original.get("schema") != "buysideflow-spider-batch/v1":
        raise ValueError("Unknown original batch schema")
    if original.get("status") not in {"provenance_error", "failed", "interrupted"}:
        raise ValueError("Only a stopped, incomplete batch may be continued")
    history = resume_history(root)
    for index, (path, ledger) in enumerate(history):
        if ledger.get("status") not in {"finished", "failed", "interrupted", "provenance_error"}:
            raise FileExistsError("Previous continuation is active; refusing duplicate workers")
        if ledger.get("manifest_name", path.name) != path.name:
            raise ValueError("Continuation manifest identity differs from its filename")
        _check_history_links(root, ledger, history[:index])
        _check_evidence(root, ledger)
        if (ledger.get("expected_task_ids") != task_ids or ledger.get("settings") != settings
                or ledger.get("model") != original.get("model")
                or ledger.get("db_snapshot_id") != snapshot
                or ledger.get("db_snapshot_cutoff", "") != os.getenv("DB_SNAPSHOT_CUTOFF", "")):
            raise ValueError("Historical continuation identity changed")
    if original.get("expected_task_ids") != task_ids:
        raise ValueError("Canonical task universe/order changed")
    if original.get("settings") != settings:
        raise ValueError("Model, budgets or run identity changed")
    model = resolve_model_name(settings["model"])
    cutoff = os.getenv("DB_SNAPSHOT_CUTOFF", "")
    if (original.get("model"), original.get("db_snapshot_id"), original.get("db_snapshot_cutoff", "")) != (model, snapshot, cutoff):
        raise ValueError("Model/database snapshot changed")
    old_sources = original.get("sources")
    if not isinstance(old_sources, dict) or not old_sources:
        raise ValueError("Missing original source fingerprints")
    rules_name = "data/dataset/comparison_rules.json"
    if sha256_file(rules_archive) != old_sources.get(rules_name):
        raise ValueError("Rules archive does not match the original recorded SHA256")
    old_rules = json.loads(rules_archive.read_text())
    current_rules = json.loads((PROJECT_ROOT / rules_name).read_text())
    reused = [qid for qid in task_ids if (root / qid / "result.json").is_file()]
    pending = [qid for qid in task_ids if qid not in reused]
    if not reused or not pending:
        if history and not pending:
            raise FileExistsError("All tasks already have saved results; nothing to resume")
        raise ValueError("Resume requires both saved results and untouched pending tasks")
    unexpected = {p.parent.name for p in root.glob("*/result.json")} - set(task_ids)
    if unexpected:
        raise ValueError("Unexpected saved task results")
    for qid in pending:
        if (root / qid).exists() or (root / ".workspaces" / qid).exists():
            raise ValueError(f"Pending task has partial evidence/workspace: {qid}")
    owners = _generation_owners(original, history, reused)
    if set(owners) != set(reused):
        raise ValueError("Saved results lack recorded generation-segment ownership")
    segments = {name: ledger for name, ledger in owners.values()}
    changed = {name: _source_changes(ledger["sources"], sources,
                                   {qid for qid, (owner, _) in owners.items() if owner == name}, model, preserve_comparison)
               for name, ledger in segments.items()}
    assets, versions = shared_asset_manifest(), _runtime_versions()
    upstream = json.loads((PROJECT_ROOT / "spider_agent_lite/UPSTREAM_MANIFEST.json").read_text())["commit"]
    action_space = "".join(cls.get_action_description() for cls in ACTION_CLASSES)
    receipt = {}
    for qid in reused:
        origin, generation = owners[qid]
        generation_sources = generation["sources"]
        raw = json.loads((root / qid / "result.json").read_text())
        adapter = raw.get("adapter", {})
        expected = {
            "model": model, "requested_model": settings["model"], "run_name": settings["run_name"],
            "db_snapshot_id": snapshot, "db_snapshot_cutoff": cutoff,
            "shared_assets": assets, "runtime_packages": versions,
            "python_version": sys.version.split()[0], "platform": platform.platform(),
            "upstream_commit": upstream, **{key: settings[key] for key in BUDGET_KEYS},
        }
        if any(key not in adapter or adapter[key] != value for key, value in expected.items()):
            raise ValueError(f"Saved runtime/model/budget identity differs: {qid}")
        if (not isinstance(raw.get("trajectory"), list) or raw.get("error")
                or not adapter.get("finished_at") or not isinstance(raw.get("finished"), bool)
                or (raw.get("submission") is not None and not isinstance(raw["submission"], dict))):
            raise ValueError(f"Saved result is corrupt, partial or has a run error: {qid}")
        problem = build_problem_statement(qid)
        prompt = build_system_prompt(problem, action_space, settings["max_steps"])
        if raw.get("Task") != problem.question or raw.get("system_message") != prompt:
            raise ValueError(f"Full task/system prompt changed: {qid}")
        if origin == "original_batch":
            rule_hash = _digest(old_rules.get(qid))
        elif "generation_rule_hashes" in generation:
            rule_hash = generation["generation_rule_hashes"].get(qid)
        elif generation_sources.get(rules_name) == sha256_file(PROJECT_ROOT / rules_name):
            rule_hash = _digest(current_rules.get(qid))
        elif generation_sources.get(rules_name) == sha256_file(rules_archive):
            rule_hash = _digest(old_rules.get(qid))
        else:
            raise ValueError(f"Historical generation rules are unavailable for: {qid}")
        if rule_hash != _digest(current_rules.get(qid)):
            raise ValueError(f"Comparison rules changed for saved task: {qid}")
        prefix = f"data/dataset/results/{qid}/"
        refs = {name: digest for name, digest in generation_sources.items() if name.startswith(prefix)}
        if not refs or refs != {name: digest for name, digest in sources.items() if name.startswith(prefix)}:
            raise ValueError(f"Saved task reference inventory changed: {qid}")
        receipt[qid] = {
            "origin": origin, "files": file_inventory(root / qid),
            "full_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "reference_sources": refs, "comparison_rules_sha256": _digest(current_rules.get(qid)),
            "runtime_identity_sha256": _digest(expected),
            "submitted": bool(raw.get("submission")),
        }
    manifest_name = "resume_manifest.json" if not history else f"resume_manifest.{len(history)+1:03d}.json"
    recovery_path = history[-1][0].with_suffix(".recovery.json") if history else None
    recovery = json.loads(recovery_path.read_text()) if recovery_path and recovery_path.exists() else {}
    return {
        "schema": SCHEMA, "status": "running", "original_manifest_sha256": sha256_file(original_path),
        "manifest_name": manifest_name,
        "history_manifests": [{"path": path.name, "sha256": sha256_file(path)} for path, _ in history],
        "recovery_records": [{"path": path.with_suffix(".recovery.json").name,
                              "sha256": sha256_file(path.with_suffix(".recovery.json"))}
                             for path, _ in history if path.with_suffix(".recovery.json").is_file()],
        "original_status": original["status"], "run_name": settings["run_name"],
        "model": model, "db_snapshot_id": snapshot, "db_snapshot_cutoff": cutoff,
        "expected_task_ids": task_ids, "reused_task_ids": reused, "scheduled_task_ids": pending,
        "settings": settings, "sources": sources, "source_changes": changed,
        "rules_archive": {"path": str(rules_archive.resolve()), "sha256": sha256_file(rules_archive)},
        "reuse_compatibility": receipt,
        "generation_rule_hashes": {qid: _digest(current_rules.get(qid)) for qid in task_ids},
        "task_origins": {qid: owners[qid][0] if qid in owners else manifest_name for qid in task_ids},
        "worker_errors": {}, "continuation_results": {},
        "restarted_incomplete_task_ids": [qid for qid in recovery.get("restarted_incomplete_task_ids", []) if qid in pending],
        "deferred_task_ids": [qid for qid in recovery.get("deferred_incomplete_task_ids", []) if qid in pending],
        "historical_comparison_sha256": old_sources["sweagent/text2sql/semantic_comparison.py"] if preserve_comparison else None,
    }


def validate_resume(root: Path, manifest: dict, *, allow_running: bool = False) -> None:
    """Validate frozen segment and all reused bytes before any report export."""
    if manifest.get("schema") != SCHEMA:
        raise ValueError("Unknown continuation manifest schema")
    if not allow_running and manifest.get("status") not in {"finished", "failed", "interrupted"}:
        raise ValueError("Continuation is running or has failed provenance")
    history = resume_history(root)
    name = manifest.get("manifest_name", "resume_manifest.json")
    if history and name != history[-1][0].name:
        raise ValueError("Only the latest continuation may export")
    ancestors = [(path, ledger) for path, ledger in history if path.name != name]
    if any(path.name == name for path, _ in history[:-1]):
        raise ValueError("Only the latest continuation may export")
    _check_history_links(root, manifest, ancestors)
    for index, (_, ledger) in enumerate(ancestors):
        if ledger.get("status") not in {"finished", "failed", "interrupted", "provenance_error"}:
            raise ValueError("Historical continuation is still active")
        _check_history_links(root, ledger, ancestors[:index])
        _check_evidence(root, ledger)
    _check_evidence(root, manifest)
    owners = _generation_owners(json.loads((root / "batch_manifest.json").read_text()), ancestors,
                                manifest["reused_task_ids"])
    if set(owners) != set(manifest["reused_task_ids"]) or any(
            receipt["origin"] != owners[qid][0] for qid, receipt in manifest["reuse_compatibility"].items()):
        raise ValueError("Reused-task generation origin differs from history")
    expected, reused, pending = (manifest[key] for key in
                                 ("expected_task_ids", "reused_task_ids", "scheduled_task_ids"))
    if (any(not isinstance(qid, str) or qid in {"", ".", ".."}
            or Path(qid).name != qid for qid in expected)
            or len(set(expected)) != len(expected) or set(reused) & set(pending)
            or set(reused) | set(pending) != set(expected)
            or set(manifest["reuse_compatibility"]) != set(reused)
            or any(manifest["task_origins"].get(qid) != receipt["origin"]
                   for qid, receipt in manifest["reuse_compatibility"].items())
            or (manifest.get("manifest_name") and any(
                manifest["task_origins"].get(qid) != name for qid in pending))
            or (not manifest.get("manifest_name") and manifest["task_origins"] != {
                qid: "original_batch" if qid in reused else "continuation" for qid in expected})):
        raise ValueError("Invalid continuation coverage/origin mapping")
    for name, digest in manifest["sources"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid continuation source path")
        path = PROJECT_ROOT / relative
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"Continuation source changed: {name}")
    archive = manifest["rules_archive"]
    if sha256_file(Path(archive["path"])) != archive["sha256"]:
        raise ValueError("Historical rules evidence changed")
    if manifest.get("status") != "running":
        current_results = {qid: sha256_file(root / qid / "result.json") for qid in pending
                           if (root / qid / "result.json").is_file()}
        if current_results != manifest["continuation_results"]:
            raise ValueError("Continuation raw results changed after generation")
