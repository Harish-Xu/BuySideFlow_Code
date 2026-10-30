from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime
import importlib.util
import json
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from spider_agent_lite.adapter.assets import SHARED_ASSETS, sha256_file, shared_asset_manifest
from spider_agent_lite.adapter.task_loader import CANONICAL_DATASET, load_tasks, to_spider_task


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _load_environment() -> None:
    try:
        from text2sql_benchmark.benchmark_paths import load_benchmark_env
    except ImportError:
        from benchmark_paths import load_benchmark_env
    load_benchmark_env()


def _run_settings(args: argparse.Namespace) -> dict:
    return {key: getattr(args, key) for key in (
        "model", "output_dir", "run_name", "max_steps", "max_memory_length",
        "max_tokens", "temperature", "top_p", "model_retry_attempts",
        "model_retry_min_wait", "model_retry_max_wait", "max_run_code_calls",
    )}


def _shared_python_sources() -> set[Path]:
    """Follow local Python imports, including imports inside tool functions.

    This is a file-level dependency boundary, not a semantic-diff exemption.
    Dynamic Spider imports are separately covered by its pinned upstream manifest.
    """
    pending = [Path(__file__), *sorted((HERE / "adapter").glob("*.py"))]
    visited, shared = set(), set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        module = ".".join(path.relative_to(PROJECT_ROOT).with_suffix("").parts)
        package = module.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = ".".join(package.split(".")[:len(package.split(".")) - node.level + 1]
                                    + ([base] if base else []))
                names = [base, *[f"{base}.{alias.name}" for alias in node.names]]
            for name in names:
                if name != "sweagent" and not name.startswith("sweagent."):
                    continue
                parts = name.split(".")
                candidates = [PROJECT_ROOT.joinpath(*parts).with_suffix(".py")]
                candidates.extend(PROJECT_ROOT.joinpath(*parts[:i], "__init__.py")
                                  for i in range(1, len(parts) + 1))
                for candidate in candidates:
                    if candidate.is_file() and candidate not in shared:
                        shared.add(candidate)
                        pending.append(candidate)
    return shared


def _source_snapshot() -> dict[str, str]:
    """Fingerprint canonical inputs, active assets and imported implementation."""
    paths = set(SHARED_ASSETS.values())
    paths.update(_shared_python_sources())
    for root in (CANONICAL_DATASET, HERE / "adapter"):
        paths.update(path for path in root.rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts
                     and path.suffix not in {".pyc", ".pyo"}
                     and not path.name.startswith(".env")
                     and "credential" not in path.name.lower())
    paths.update((Path(__file__), PROJECT_ROOT / "benchmark_paths.py",
                  PROJECT_ROOT / "sweagent/config/litellm_openai_compat_models.json",
                  HERE / "config/text2sql.yaml", HERE / "UPSTREAM_MANIFEST.json"))
    upstream = json.loads((HERE / "UPSTREAM_MANIFEST.json").read_text())
    paths.update(HERE / "upstream" / name for name in upstream["files"])
    fingerprints = {str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
                    for path in sorted(paths)}
    for name, digest in upstream["files"].items():
        label = str((HERE / "upstream" / name).relative_to(PROJECT_ROOT))
        if fingerprints[label] != digest:
            raise ValueError(f"Pinned Spider upstream source changed: {name}")
    return fingerprints


class SourceDriftError(RuntimeError):
    """Picklable distinction between infrastructure drift and a task failure."""


def _assert_sources_unchanged(sources: dict[str, str]) -> None:
    changed = [name for name, digest in sources.items()
               if not (PROJECT_ROOT / name).is_file()
               or sha256_file(PROJECT_ROOT / name) != digest]
    if changed:
        raise SourceDriftError("Batch input/source changed: " + ", ".join(changed[:5]))


@contextmanager
def _task_workspace(path: Path):
    """Isolate relative outputs and /tmp aliases in the spawned task process."""
    path.mkdir(parents=True, exist_ok=False)
    temporary = path / ".tmp"
    temporary.mkdir()
    previous_cwd, previous_temp = Path.cwd(), tempfile.tempdir
    previous_env = {name: os.environ.get(name) for name in ("TMPDIR", "TEMP", "TMP")}
    try:
        os.chdir(path)
        tempfile.tempdir = str(temporary)
        os.environ.update({name: str(temporary) for name in previous_env})
        yield
    finally:
        os.chdir(previous_cwd)
        tempfile.tempdir = previous_temp
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _run_batch_task(task_id: str, settings: dict, sources: dict[str, str]) -> str:
    """Top-level picklable worker; only per-task artifacts are written here."""
    _load_environment()
    _assert_sources_unchanged(sources)
    settings = dict(settings)
    historical_comparison = settings.pop("_historical_comparison_sha256", None)
    if historical_comparison:
        from spider_agent_lite.adapter.resume import install_historical_comparison
        install_historical_comparison(historical_comparison)
    from spider_agent_lite.adapter.model_gateway import resolve_model_name

    model_dir = "spider_agent_lite__" + resolve_model_name(settings["model"]).replace("/", "--")
    workspace = Path(settings["output_dir"]) / settings["run_name"] / model_dir / ".workspaces" / task_id
    # Import runtime/evaluator only after chdir: their relative caches must not
    # point at the original SWE-agent working directory in a fresh worker.
    with _task_workspace(workspace):
        from spider_agent_lite.adapter.runner import run_task
        result = run_task(task_id, **settings, export_reports=False)
    if json.loads(result.read_text()).get("error"):
        raise RuntimeError("Agent run failed; original evidence is saved in result.json")
    return str(result)


def _save_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    for name, value in os.environ.items():
        if value and any(token in name.upper() for token in ("KEY", "TOKEN", "PASSWORD", "SECRET", "PWD")):
            message = message.replace(value, "[REDACTED]")
    return f"{type(exc).__name__}: {message[:1000]}"


def run_batch(args: argparse.Namespace) -> int:
    """One logical run, isolated spawned tasks, one parent-owned export."""
    from sweagent.text2sql.provenance import require_db_snapshot_id
    from spider_agent_lite.adapter.model_gateway import resolve_model_name
    from spider_agent_lite.adapter.reporting import export_run_reports, workflow_model_name

    snapshot = require_db_snapshot_id()
    tasks = load_tasks()
    task_ids = [str(task["id"]) for task in tasks]
    if (not task_ids or len(task_ids) != len(set(task_ids))
            or any(qid in {"", ".", ".."} or Path(qid).name != qid for qid in task_ids)):
        raise ValueError("Canonical task ids must be nonempty, unique path components")
    installation = check_installation()
    if not installation["runtime_dependencies_ready"]:
        raise RuntimeError("Missing Spider runtime dependencies: " + ", ".join(
            installation["missing_runtime_dependencies"]) +
            ". Install spider_agent_lite/requirements-runtime.txt first.")

    settings = _run_settings(args)
    settings["output_dir"] = str(Path(args.output_dir).resolve())
    settings["run_name"] = args.run_name or datetime.now().astimezone().strftime("%Y%m%d@%H%M%S.%f%z")
    run_name = settings["run_name"]
    if run_name in {"", ".", ".."} or Path(run_name).name != run_name:
        raise ValueError("run_name must be one path component")
    resolved_model = resolve_model_name(args.model)
    sources = _source_snapshot()
    model_dir = Path(args.output_dir).resolve() / run_name / workflow_model_name(resolved_model)
    resume_from = getattr(args, "resume_from", None)
    if resume_from:
        from spider_agent_lite.adapter.resume import prepare_resume, recover_crashed_segment
        if Path(resume_from).resolve() != model_dir:
            raise ValueError("--resume-from must match the configured output/run/model directory")
        if not getattr(args, "resume_rules_archive", None):
            raise ValueError("--resume-rules-archive is required for hash-bound compatibility proof")
        recovery = None
        if getattr(args, "recover_crashed", False):
            recovery = recover_crashed_segment(model_dir,
                restart_incomplete=getattr(args, "restart_incomplete", False),
                defer_incomplete=getattr(args, "defer_incomplete", False))
        manifest = prepare_resume(model_dir, settings, sources, task_ids, snapshot,
                                  Path(args.resume_rules_archive),
                                  preserve_comparison=getattr(args, "preserve_historical_comparison", False))
        if recovery:
            record = json.loads(recovery.read_text())
            manifest["restarted_incomplete_task_ids"] = record["restarted_incomplete_task_ids"]
            manifest["deferred_task_ids"] = record["deferred_incomplete_task_ids"]
        manifest_path = model_dir / manifest["manifest_name"]
        # Exclusive reservation also prevents two compatible resume coordinators.
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
        scheduled = [qid for qid in manifest["scheduled_task_ids"] if qid not in manifest.get("deferred_task_ids", [])]
    else:
        # Never merge with or overwrite an old run by default.
        model_dir.mkdir(parents=True, exist_ok=False)
        manifest_path = model_dir / "batch_manifest.json"
        scheduled = task_ids
        manifest = {
            "schema": "buysideflow-spider-batch/v1", "status": "running",
            "run_name": run_name, "model": resolved_model, "db_snapshot_id": snapshot,
            "db_snapshot_cutoff": os.getenv("DB_SNAPSHOT_CUTOFF", ""),
            "expected_task_ids": task_ids, "settings": settings, "sources": sources,
            "worker_errors": {},
        }
    manifest["num_workers"] = args.num_workers
    manifest["started_at"] = datetime.now().astimezone().isoformat()
    manifest["completed_task_ids"] = []
    manifest["active_task_ids"] = []
    manifest["not_started_task_ids"] = list(scheduled)
    _save_manifest(manifest_path, manifest)
    print(f"Spider batch: {len(scheduled)} scheduled, {len(task_ids)-len(scheduled)} reused, "
          f"{args.num_workers} workers, run={run_name}", flush=True)
    interrupted = False
    try:
        with ProcessPoolExecutor(max_workers=args.num_workers,
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {}
            remaining = iter(scheduled)
            stopping = False

            def save_progress():
                manifest["active_task_ids"] = list(futures.values())
                dispatched = set(manifest["completed_task_ids"]) | set(futures.values())
                manifest["not_started_task_ids"] = [qid for qid in scheduled if qid not in dispatched]
                _save_manifest(manifest_path, manifest)

            def stop_for_drift(exc):
                nonlocal stopping
                stopping = True
                manifest["status"] = "stopping"
                manifest["provenance_error"] = _safe_error(exc)
                for future in list(futures):
                    if future.cancel():
                        del futures[future]
                save_progress()
                print("Source drift: stopped dispatch; waiting only for active tasks.", flush=True)

            try:
                while True:
                    if not stopping:
                        try:
                            _assert_sources_unchanged(sources)
                        except SourceDriftError as exc:
                            stop_for_drift(exc)
                    # No benchmark-wide queued backlog: at most N active futures.
                    dispatched = False
                    while not stopping and len(futures) < args.num_workers:
                        task_id = next(remaining, None)
                        if task_id is None:
                            break
                        relevant = {name: digest for name, digest in sources.items()
                                    if not name.startswith("data/dataset/results/")
                                    or name.startswith(f"data/dataset/results/{task_id}/")}
                        worker_settings = dict(settings)
                        if manifest.get("historical_comparison_sha256"):
                            worker_settings["_historical_comparison_sha256"] = manifest["historical_comparison_sha256"]
                        futures[pool.submit(_run_batch_task, task_id, worker_settings, relevant)] = task_id
                        dispatched = True
                    if dispatched:
                        save_progress()
                    if not futures:
                        break
                    done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
                    for future in done:
                        task_id = futures.pop(future)
                        manifest["completed_task_ids"].append(task_id)
                        try:
                            future.result()
                            print(f"[{task_id}] recorded", flush=True)
                        except SourceDriftError as exc:
                            manifest["worker_errors"][task_id] = _safe_error(exc)
                            stop_for_drift(exc)
                        except Exception as exc:
                            manifest["worker_errors"][task_id] = _safe_error(exc)
                            print(f"[{task_id}] worker error; retained for coverage", flush=True)
                    if done:
                        save_progress()
            except BaseException as exc:
                # Do not let the context manager drain all queued benchmark jobs
                # after Ctrl-C or a broken scheduler. Running calls may finish.
                for future in list(futures):
                    if future.cancel():
                        del futures[future]
                manifest["status"] = "stopping"
                manifest["batch_error"] = _safe_error(exc)
                save_progress()
                pool.shutdown(wait=True, cancel_futures=True)
                raise
    except KeyboardInterrupt:
        interrupted = True
        manifest["batch_error"] = "KeyboardInterrupt"
    except Exception as exc:
        manifest["batch_error"] = _safe_error(exc)

    manifest["finished_at"] = datetime.now().astimezone().isoformat()
    manifest["active_task_ids"] = []
    manifest["status"] = ("interrupted" if interrupted else
                          "failed" if manifest["worker_errors"] or manifest.get("batch_error") else "finished")
    if resume_from:
        manifest["continuation_results"] = {
            qid: sha256_file(model_dir / qid / "result.json") for qid in scheduled
            if (model_dir / qid / "result.json").is_file()
        }
    try:
        if manifest.get("provenance_error"):
            raise SourceDriftError(manifest["provenance_error"])
        _assert_sources_unchanged(sources)
        if _source_snapshot() != sources:
            raise RuntimeError("Batch source inventory changed")
        if require_db_snapshot_id() != snapshot or os.getenv("DB_SNAPSHOT_CUTOFF", "") != manifest["db_snapshot_cutoff"]:
            raise RuntimeError("Batch database snapshot identity changed")
        if resume_from:
            from spider_agent_lite.adapter.resume import validate_resume
            validate_resume(model_dir, manifest, allow_running=True)
    except Exception as exc:
        manifest["status"] = "provenance_error"
        manifest["provenance_error"] = _safe_error(exc)
        _save_manifest(manifest_path, manifest)
        print("Batch provenance changed; refusing to export a comparable report.", file=sys.stderr)
        return 1
    _save_manifest(manifest_path, manifest)
    report = export_run_reports(model_dir, expected_task_ids=task_ids)
    print(report)
    return int(interrupted or bool(manifest["worker_errors"]) or bool(manifest.get("batch_error")))


def optional_float(value: str) -> float | None:
    if value.strip().lower() in {"none", "null"}:
        return None
    return float(value)


def check_installation() -> dict[str, object]:
    tasks = load_tasks()
    upstream_manifest = json.loads((HERE / "UPSTREAM_MANIFEST.json").read_text())
    runtime_modules = {
        "dashscope": "dashscope",
        "docker": "docker",
        "google-generativeai": "google.generativeai",
        "groq": "groq",
        "gymnasium": "gymnasium",
        "litellm": "litellm",
        "openai": "openai",
        "requests": "requests",
        "tiktoken": "tiktoken",
    }
    def module_available(module: str) -> bool:
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError):
            return False

    missing_runtime_dependencies = [
        package for package, module in runtime_modules.items() if not module_available(module)
    ]
    return {
        "status": "runtime_bridge_ready",
        "task_count": len(tasks),
        "first_task": to_spider_task(tasks[0])["instance_id"],
        "shared_assets": shared_asset_manifest(),
        "upstream": {
            "repository": upstream_manifest["repository"],
            "commit": upstream_manifest["commit"],
            "file_count": len(upstream_manifest["files"]),
        },
        "model_called": False,
        "database_called": False,
        "runtime_dependencies_ready": not missing_runtime_dependencies,
        "missing_runtime_dependencies": missing_runtime_dependencies,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Spider-Agent-Lite BuySideFlow integration")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate provenance, canonical tasks, and shared assets without external calls",
    )
    parser.add_argument("--task", help="canonical task id, for example bond_001")
    parser.add_argument("--all", action="store_true", help="run all canonical tasks in one logical batch")
    parser.add_argument("--resume-from", metavar="MODEL_DIR",
                        help="with --all, preserve compatible saved tasks and run untouched missing tasks")
    parser.add_argument("--resume-rules-archive", metavar="JSON",
                        help="historical comparison rules matching the original manifest SHA256")
    parser.add_argument("--recover-crashed", action="store_true",
                        help="record explicit recovery of an abandoned active ledger without rewriting it")
    parser.add_argument("--restart-incomplete", action="store_true",
                        help="with --recover-crashed, archive partial evidence and restart incomplete tasks with original fresh budgets")
    parser.add_argument("--defer-incomplete", action="store_true",
                        help="with --recover-crashed, archive and defer partial tasks; continue only untouched missing tasks")
    parser.add_argument("--preserve-historical-comparison", action="store_true",
                        help="with --resume-from, use the hash-verified historical scalar-comparison function in task processes")
    parser.add_argument("--num_workers", "--num-workers", type=positive_int, default=1,
                        help="spawned task processes for --all; default 1")
    parser.add_argument(
        "--report-only", metavar="MODEL_DIR",
        help="export reports, tool-use and evaluator inputs from existing results; no external calls",
    )
    parser.add_argument(
        "--model",
        default="deepseek",
        help="deepseek resolves to the current deepseek/deepseek-v4-flash preset",
    )
    parser.add_argument("--output-dir", default="trajectories/spider_agent_lite")
    parser.add_argument("--run-name", help="experiment/run identifier; defaults to a unique timestamp")
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--max-memory-length", type=int, default=25)
    parser.add_argument("--max-tokens", type=int, default=65536)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--top-p",
        type=optional_float,
        default=None,
        help="nucleus sampling parameter; use none to omit it (default)",
    )
    parser.add_argument("--model-retry-attempts", type=int, default=20)
    parser.add_argument("--model-retry-min-wait", type=float, default=10.0)
    parser.add_argument("--model-retry-max-wait", type=float, default=120.0)
    parser.add_argument("--max-run-code-calls", type=int, default=100)
    args = parser.parse_args()
    if args.recover_crashed and not args.resume_from:
        parser.error("--recover-crashed requires --resume-from")
    if args.preserve_historical_comparison and not args.resume_from:
        parser.error("--preserve-historical-comparison requires --resume-from")
    if (args.restart_incomplete or args.defer_incomplete) and not args.recover_crashed:
        parser.error("incomplete-task recovery choices require --recover-crashed")
    if args.restart_incomplete and args.defer_incomplete:
        parser.error("choose either restart or defer incomplete tasks")
    if (args.resume_from or args.resume_rules_archive) and not args.all:
        parser.error("resume options require --all")
    if args.resume_rules_archive and not args.resume_from:
        parser.error("--resume-rules-archive requires --resume-from")
    if args.all and (args.task or args.check or args.report_only):
        parser.error("--all cannot be combined with --task, --check or --report-only")
    if args.check:
        print(json.dumps(check_installation(), ensure_ascii=False, indent=2))
        return 0
    if args.report_only:
        from spider_agent_lite.adapter.reporting import export_run_reports

        print(export_run_reports(args.report_only))
        return 0
    if not args.all and args.num_workers != 1:
        parser.error("--num_workers requires --all")
    if not args.task and not args.all:
        parser.error("--task or --all is required unless --check or --report-only is used")

    _load_environment()
    if args.all:
        return run_batch(args)

    from spider_agent_lite.adapter.runner import run_task

    result_path = run_task(
        args.task,
        model=args.model,
        output_dir=args.output_dir,
        run_name=args.run_name,
        max_steps=args.max_steps,
        max_memory_length=args.max_memory_length,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        model_retry_attempts=args.model_retry_attempts,
        model_retry_min_wait=args.model_retry_min_wait,
        model_retry_max_wait=args.model_retry_max_wait,
        max_run_code_calls=args.max_run_code_calls,
    )
    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
