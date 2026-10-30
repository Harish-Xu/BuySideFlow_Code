"""
独立评估脚本：读取 preds.json + benchmark 参考答案，连数据库评估，生成报告。

用法:
    python run_eval.py
    python run_eval.py --preds trajectories/.../preds.json
    python run_eval.py --data data/dataset
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
import re

# Fix GBK console encoding on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from buysideflow.benchmark_paths import (
        load_benchmark_env,
        load_run_instances_config,
        resolve_benchmark_from_run_dir,
        resolve_default_benchmark_path,
        resolve_run_config_path,
    )
except ImportError:
    try:
        from text2sql_benchmark.benchmark_paths import (
            load_benchmark_env,
            load_run_instances_config,
            resolve_benchmark_from_run_dir,
            resolve_default_benchmark_path,
            resolve_run_config_path,
        )
    except ImportError:
        from benchmark_paths import (
            load_benchmark_env,
            load_run_instances_config,
            resolve_benchmark_from_run_dir,
            resolve_default_benchmark_path,
            resolve_run_config_path,
        )

load_benchmark_env()

from sweagent.text2sql.markdown import parse_text2sql_tasks
from sweagent.text2sql.evaluator import CompareResult, compare_items, format_result_for_report
from sweagent.text2sql.manual_adjudications import (
    apply_adjudication_to_record,
    load_run_adjudications,
)
from sweagent.text2sql.provenance import (
    aggregate_sha256,
    anonymize_report_text,
    build_provenance_rows,
    display_path,
    reference_files_from_tasks,
    reference_sidecar_files_from_tasks,
    render_provenance_markdown,
    require_db_snapshot_id,
    sha256_file,
)
from sweagent.run.hooks.text2sql_evaluate import (
    append_pit_diagnostic_breakdowns,
    append_visual_results, comparison_metadata,
    build_tool_use_record,
    write_tool_use_csv,
)


def load_pipeline_types(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    mapping: dict[str, str] = {}
    category = ""
    for line in text.splitlines():
        if line.startswith("## sql_only"):
            category = "sql_only"
        elif line.startswith("## sql_orchestration_or_semantic"):
            category = "sql_python"
        elif line.startswith("## sql_python_compute"):
            category = "sql_python"
        elif line.startswith("## "):
            category = ""
        if category:
            for qid in re.findall(r"\b(?:bond|fund|macro|stock)_\d{3}\b", line):
                if qid in mapping and mapping[qid] != category:
                    raise ValueError(f"Duplicate pipeline category for {qid}")
                mapping[qid] = category
    if len(mapping) != 408:
        raise ValueError(f"Expected 408 pipeline labels, found {len(mapping)}")
    return mapping


def _sort_qid_key(qid: str) -> tuple[int, str]:
    matches = re.findall(r"\d+", str(qid))
    return (int(matches[-1]) if matches else 10**9, str(qid))


def _qid_matches_selection(qid: str, selected: set[str]) -> bool:
    if qid in selected or qid.lstrip("q") in selected:
        return True
    matches = re.findall(r"\d+", str(qid))
    if not matches:
        return False
    numeric = matches[-1]
    return numeric in selected or numeric.lstrip("0") in selected


def _domain_from_qid(qid: str) -> str:
    return qid.split("_", 1)[0] if "_" in qid else "unknown"


def _metric_type(evaluation_kind: str, strict_output_schema: bool) -> str:
    if evaluation_kind == "csv" and strict_output_schema:
        return "primary_tabular"
    if evaluation_kind == "csv":
        return "primary_tabular_loose"
    return "secondary_semantic"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "model"


def _asset_paths_from_run_config(run_dir: Path) -> list[Path]:
    instances = load_run_instances_config(run_dir)
    if instances is None:
        return []
    paths: list[Path] = []
    for key in (
        "schema_path",
        "catalog_schema_path",
        "business_metadata_path",
        "selection_guidance_path",
        "fund_rules_path",
        "bond_rules_path",
    ):
        raw_path = instances.get(key)
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        paths.append(resolve_run_config_path(raw_path))
    return paths


def _guess_model_name(preds_path: Path, preds_raw: object) -> str:
    run_name = preds_path.parent.name
    if "__" in run_name and "___" in run_name:
        return _safe_filename(run_name.split("__", 1)[1].split("___", 1)[0])
    if isinstance(preds_raw, dict) and preds_raw:
        first = next(iter(preds_raw.values()))
        if isinstance(first, dict) and first.get("model_name_or_path"):
            return _safe_filename(str(first["model_name_or_path"]))
    return _safe_filename(run_name)


def _load_trajectory_for_qid(trajectory_dir: Path, qid: str) -> list[dict]:
    candidates = [
        trajectory_dir / qid / f"{qid}.traj",
        trajectory_dir / f"{qid}.traj",
    ]
    task_dir = trajectory_dir / qid
    if task_dir.exists():
        candidates.extend(sorted(task_dir.glob("*.traj")))
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return []
        trajectory = data.get("trajectory") if isinstance(data, dict) else None
        if isinstance(trajectory, list):
            return trajectory
        messages = data if isinstance(data, list) else data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            return []
        # Local Codex traces store assistant tool calls as messages, not SWE-agent actions.
        return [
            {"action": message.get("tool") or ""}
            for message in messages
            if isinstance(message, dict)
            and message.get("role") in {"assistant", "assistant_action"}
        ]
    return []


def _saved_candidate_artifacts(saved_results_dir: Path, qid: str, result_vars: list[str]) -> list[dict]:
    task_dir = saved_results_dir / qid
    primary = task_dir / f"{qid}.result.csv"
    artifacts: list[dict] = []
    seen: set[Path] = set()
    for name in result_vars:
        stem = name.removesuffix("_result")
        candidates = [task_dir / f"{qid}.{name}.csv", task_dir / f"{qid}.{stem}.csv"]
        if name in {"result", "summary", "industry_result"}:
            candidates.append(primary)
        for path in candidates:
            if path.is_file() and path not in seen:
                artifacts.append({"kind": "csv", "path": str(path)})
                seen.add(path)
                break
    if primary.is_file() and primary not in seen:
        artifacts.insert(0, {"kind": "csv", "path": str(primary)})
    for path in sorted(task_dir.glob(f"{qid}.*.png")):
        artifacts.append({"kind": "image", "path": str(path)})
    for path in sorted(task_dir.glob(f"{qid}.*.txt")):
        artifacts.append({"kind": "text", "path": str(path)})
    return artifacts


def _saved_artifacts_for_task(saved_results_dir: Path, qid: str, gen: dict, ref: dict | None) -> list[dict]:
    reference_artifacts = ref.get("reference_artifact_paths") or [] if ref else []
    primary = saved_results_dir / qid / f"{qid}.result.csv"
    if ref and ref.get("evaluation_kind") == "csv" and len(reference_artifacts) == 1:
        return [{"kind": "csv", "path": str(primary)}] if primary.is_file() else []
    return _saved_candidate_artifacts(saved_results_dir, qid, list(gen.get("result_vars", [])))


def _evaluate_qid_task(task: tuple) -> tuple:
    qid, gen, ref, model_name, trajectory_dir, no_tool_use, adjudication, saved_results_dir = task
    if ref is None:
        return qid, None, None, None

    difficulty = str(ref.get("difficulty", "unrated") or "unrated")
    behavior_record = None
    if not no_tool_use:
        behavior_record = build_tool_use_record(
            qid=qid,
            difficulty=difficulty,
            trajectory=_load_trajectory_for_qid(trajectory_dir, qid),
        )

    candidate = {
        "id": qid,
        "model_name": model_name,
        "question": ref.get("question", ""),
        "mode": gen.get("mode", "sql"),
        "sql_code": gen.get("sql_code") or "",
        "python_code": gen.get("python_code") or "",
        "result_vars": list(gen.get("result_vars", [])),
    }
    if ref.get("reference_status") == "blocked":
        # Do not require saved outputs, execute programs, or apply an old manual
        # decision when the current reference cannot establish correctness.
        return qid, ref, behavior_record, compare_items(ref, candidate)
    if saved_results_dir is not None:
        artifacts = _saved_artifacts_for_task(saved_results_dir, qid, gen, ref)
        if not artifacts:
            result = CompareResult(qid)
            result.evaluation_kind = ref.get("evaluation_kind")
            result.evaluation_complete = False
            result.error_type = "artifact_error"
            result.error_msg = "No saved output artifacts for this task"
            result.max_score = 0.0
            return qid, ref, behavior_record, result
        candidate["reference_artifact_paths"] = artifacts
    result = compare_items(ref, candidate)
    if adjudication is not None:
        result.passed = adjudication["decision"] == "pass"
        result.score = float(adjudication["score"])
        result.max_score = float(adjudication["max_score"])
        if result.passed:
            result.error_type = None
            result.error_msg = None
    return qid, ref, behavior_record, result


def _append_behavior_diagnostics(
    lines: list[str],
    behavior_records: list[dict],
    eval_records: list[dict],
    *,
    tool_use_path: Path | None,
) -> None:
    if not behavior_records:
        return
    eval_by_qid = {record["qid"]: record for record in eval_records}
    total = len(behavior_records)
    early_submit = sum(1 for record in behavior_records if record.get("is_early_submit"))
    navigation_trap = sum(
        1
        for record in behavior_records
        if int(record.get("schema_explore_cnt", 0)) >= 6
        and eval_by_qid.get(str(record.get("qid")), {}).get("evaluation_complete") is not False
        and not bool(eval_by_qid.get(str(record.get("qid")), {}).get("passed", False))
    )
    avg_etl = sum(float(record.get("etl_ratio", 0.0)) for record in behavior_records) / total if total else 0.0
    run_code_buckets = {"0次": 0, "1-3次": 0, ">3次": 0}
    for record in behavior_records:
        cnt = int(record.get("run_code_cnt", 0))
        if cnt == 0:
            run_code_buckets["0次"] += 1
        elif cnt <= 3:
            run_code_buckets["1-3次"] += 1
        else:
            run_code_buckets[">3次"] += 1

    lines.append("## Agent Behavior Diagnostics")
    lines.append("| metric | value | description |")
    lines.append("| --- | ---: | --- |")
    lines.append(f"| ESR (Early Submit Rate，过早提交率) | {early_submit / total * 100:.1f}% | submit前run_code调用<1次的题目占比 |")
    lines.append(f"| NTR (Navigation Trap Rate，探索陷阱率) | {navigation_trap / total * 100:.1f}% | schema探索工具调用>=6次且最终失败的题目占比 |")
    lines.append(f"| Avg ETL (Edit-Test Loop Ratio，编辑测试循环强度) | {avg_etl:.3f} | run_code次数 / trajectory总步数 的平均值 |")
    if tool_use_path is not None:
        lines.append(f"| per-task tool-use table | `{tool_use_path.name}` | 每题工具调用明细 CSV |")
    lines.append("")
    lines.append("### Run Code Distribution（run_code调用次数分布）")
    lines.append("| bucket | count |")
    lines.append("| --- | ---: |")
    for bucket, count in run_code_buckets.items():
        lines.append(f"| {bucket} | {count} |")
    lines.append("")


def _append_record_breakdown(lines: list[str], records: list[dict], *, title: str, field: str) -> None:
    groups: dict[str, dict[str, float | int]] = {}
    for record in records:
        key = str(record.get(field) or "unknown")
        bucket = groups.setdefault(key, {"total": 0, "passed": 0, "score": 0.0, "max_score": 0.0})
        bucket["total"] = int(bucket["total"]) + 1
        bucket["passed"] = int(bucket["passed"]) + int(bool(record.get("passed")))
        bucket["score"] = float(bucket["score"]) + float(record.get("score", 0.0))
        bucket["max_score"] = float(bucket["max_score"]) + float(record.get("max_score", 0.0))
    if not groups:
        return
    lines.append(title)
    lines.append("| group | total | passed | score |")
    lines.append("| --- | ---: | ---: | ---: |")
    for key, bucket in sorted(groups.items()):
        max_score = float(bucket["max_score"])
        score_text = f"{float(bucket['score']):.3f}/{max_score:.0f}" if max_score else "-"
        lines.append(f"| {key} | {int(bucket['total'])} | {int(bucket['passed'])} | {score_text} |")
    lines.append("")


def _parse_failure_scores_from_report(path: Path) -> dict[str, tuple[float, float]]:
    """Parse per-failure scores from an existing markdown report.

    Existing reports list failed/partial tasks as "### qid" sections with a
    "score: x/y" line. Passing tasks are intentionally absent from that list.
    """
    if not path.exists():
        raise FileNotFoundError(f"eval report not found: {path}")
    scores: dict[str, tuple[float, float]] = {}
    current_qid = ""
    qid_re = re.compile(r"^###\s+([A-Za-z]+_\d+)\s*$")
    score_re = re.compile(r"^score:\s*([0-9.]+)\s*/\s*([0-9.]+)\s*$")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        qid_match = qid_re.match(line.strip())
        if qid_match:
            current_qid = qid_match.group(1)
            continue
        score_match = score_re.match(line.strip())
        if current_qid and score_match:
            scores[current_qid] = (float(score_match.group(1)), float(score_match.group(2)))
            current_qid = ""
    return scores


def _metadata_eval_record(
    *,
    qid: str,
    ref: dict,
    difficulty: str,
    score_info: tuple[float, float] | None,
    report_scores_available: bool,
) -> dict:
    if score_info is not None:
        score, max_score = score_info
        passed: bool | str = score >= max_score
    elif report_scores_available:
        score, max_score = 1.0, 1.0
        passed = True
    else:
        score, max_score = "", ""
        passed = ""
    record = {
        "qid": qid,
        "domain": _domain_from_qid(qid),
        "mode": ref.get("mode", "unknown"),
        "evaluation_kind": ref.get("evaluation_kind", "unknown"),
        "metric_type": _metric_type(ref.get("evaluation_kind", ""), bool(ref.get("strict_output_schema", False))),
        "difficulty": difficulty,
        "passed": passed,
        "score": score,
        "max_score": max_score,
    }
    if ref.get("reference_status") == "blocked":
        result = compare_items(ref, {})
        record.update(score=result.score, max_score=result.max_score, **comparison_metadata(result))
    return record


def _find_latest_preds() -> Path:
    candidates = sorted(
        (PROJECT_ROOT / "trajectories").glob("**/preds.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return PROJECT_ROOT / "trajectories" / "preds.json"
    return candidates[0]


def _resolve_benchmark_for_preds(preds_path: Path) -> Path:
    run_config_path = resolve_benchmark_from_run_dir(preds_path.parent)
    if run_config_path is not None:
        return run_config_path
    return resolve_default_benchmark_path()


def main():
    default_preds = _find_latest_preds()

    parser = argparse.ArgumentParser(description="BuySideFlow standalone evaluator")
    parser.add_argument(
        "--data",
        "--markdown",
        dest="benchmark",
        default="",
        help="Benchmark 数据源；默认读取该次运行记录，未记录时使用 data/dataset。",
    )
    parser.add_argument(
        "--preds",
        default=str(default_preds),
        help="生成预测的 preds.json 路径",
    )
    parser.add_argument("--output", default="", help="报告输出路径，默认在 preds.json 同目录")
    parser.add_argument("--qids", default="", help="逗号分隔的问题 id，例如 q2,q3")
    parser.add_argument(
        "--trajectory-dir",
        default="",
        help="包含每题 .traj 的目录，默认使用 preds.json 同目录；用于离线导出 tool-use CSV",
    )
    parser.add_argument(
        "--no-tool-use",
        action="store_true",
        help="不从 .traj 导出 per-task tool-use CSV",
    )
    parser.add_argument(
        "--tool-use-output",
        default="",
        help="可选的固定 per-task tool-use CSV 路径；用于原地重报和版本锁定。",
    )
    parser.add_argument(
        "--tool-use-only",
        action="store_true",
        help="只解析已有 .traj 导出 per-task tool-use CSV，不重新执行 SQL/Python 评测",
    )
    parser.add_argument(
        "--eval-report",
        default="",
        help="已有 eval/benchmark markdown 报告；配合 --tool-use-only 可复用其中的 per-task score",
    )
    parser.add_argument(
        "--model-name",
        default="",
        help="报告和 tool-use 文件名中的模型名，默认从 run 目录名推断",
    )
    parser.add_argument(
        "--offline-judging",
        action="store_true",
        help="禁止评测器调用外部模型；需要模型判题且无确定性结果时标记为不可用",
    )
    parser.add_argument(
        "--saved-results-dir",
        default="",
        help="复评每题已保存的 CSV/PNG/TXT 输出；不重放候选程序。",
    )
    parser.add_argument(
        "--num-workers",
        "--num_workers",
        dest="num_workers",
        type=int,
        default=1,
        help="单模型内并发评估题目数；默认1。报告仍按题号顺序汇总。",
    )
    parser.add_argument(
        "--num-processes",
        type=int,
        default=0,
        help="用独立进程并行评估；适用于 SQL+Python 任务，报告仍按题号顺序汇总。",
    )
    args = parser.parse_args()
    if args.offline_judging:
        os.environ["TEXT2SQL_OFFLINE_JUDGE"] = "1"
    require_db_snapshot_id()

    preds_path = Path(args.preds)
    if not preds_path.exists():
        print(f"[ERROR] preds.json 不存在: {preds_path}")
        sys.exit(1)

    if args.benchmark:
        benchmark_path = Path(args.benchmark)
    else:
        try:
            benchmark_path = _resolve_benchmark_for_preds(preds_path)
        except (FileNotFoundError, ValueError) as exc:
            parser.error(str(exc))
    if not benchmark_path.exists():
        print(f"[ERROR] benchmark 数据源不存在: {benchmark_path}")
        sys.exit(1)

    # 加载预测
    # 格式1（dict）: {qid: {model_name_or_path, instance_id, model_patch(str)}}
    # 格式2（list）: [{instance_id, model_patch(str)}, ...]
    preds_raw = json.loads(preds_path.read_text(encoding="utf-8-sig"))
    if isinstance(preds_raw, dict):
        pred_map = {}
        for qid, v in preds_raw.items():
            patch = v["model_patch"] if isinstance(v, dict) else v
            if isinstance(patch, str) and patch.strip():
                pred_map[qid] = json.loads(patch)
            elif isinstance(patch, dict):
                pred_map[qid] = patch
            else:
                pred_map[qid] = {}
    else:
        pred_map = {}
        for item in preds_raw:
            patch = item["model_patch"]
            if isinstance(patch, str) and patch.strip():
                pred_map[item["instance_id"]] = json.loads(patch)
            elif isinstance(patch, dict):
                pred_map[item["instance_id"]] = patch
            else:
                pred_map[item["instance_id"]] = {}

    qids = sorted(pred_map.keys(), key=_sort_qid_key)
    if args.qids.strip():
        selected: set[str] = set()
        for item in args.qids.split(","):
            item = item.strip()
            if not item:
                continue
            # 同时支持 "q26" 和 "26" 两种写法
            selected.add(item)
            if item.startswith("q"):
                selected.add(item[1:])
            else:
                selected.add(f"q{item}")
        qids = [qid for qid in qids if _qid_matches_selection(qid, selected)]

    # Only selected predictions require reference loading and validation.
    evaluated_ids = set(qids)
    ref_tasks = parse_text2sql_tasks(benchmark_path, task_filter=evaluated_ids.__contains__)
    ref_map = {task["id"]: task for task in ref_tasks}
    pipeline_path = (
        benchmark_path / "results/_classify_summary.md"
        if benchmark_path.is_dir()
        else benchmark_path.parent.parent / "results/_classify_summary.md"
    )
    analysis_pipeline_types = load_pipeline_types(pipeline_path) if pipeline_path.is_file() else {}
    model_name = _safe_filename(args.model_name) if args.model_name else _guess_model_name(preds_path, preds_raw)
    trajectory_dir = Path(args.trajectory_dir) if args.trajectory_dir else preds_path.parent
    saved_results_dir = Path(args.saved_results_dir) if args.saved_results_dir else None
    run_asset_paths = _asset_paths_from_run_config(trajectory_dir)
    manual_adjudications = load_run_adjudications(preds_path)

    stats = {"passed": 0, "mismatch": 0, "gen_error": 0, "ref_error": 0,
             "ai_mismatch": 0, "submission_error": 0,
             "syntax_error": 0, "timeout": 0, "schema_mismatch": 0,
             "format_mismatch": 0, "logic_mismatch": 0}
    failures = []
    eval_records = []
    behavior_records = []
    score_sum = 0.0
    score_total = 0.0

    if args.tool_use_only:
        report_scores = (
            _parse_failure_scores_from_report(Path(args.eval_report))
            if args.eval_report
            else {}
        )
        eval_records = []
        behavior_records = []
        for qid in qids:
            ref = ref_map.get(qid)
            if ref is None:
                print(f"[WARN] {qid}: 没有参考答案，跳过")
                continue
            difficulty = str(ref.get("difficulty", "unrated") or "unrated")
            behavior_records.append(build_tool_use_record(
                qid=qid,
                difficulty=difficulty,
                trajectory=_load_trajectory_for_qid(trajectory_dir, qid),
            ))
            eval_record = _metadata_eval_record(
                qid=qid,
                ref=ref,
                difficulty=difficulty,
                score_info=report_scores.get(qid),
                report_scores_available=bool(args.eval_report),
            )
            if qid in manual_adjudications and ref.get("reference_status") != "blocked":
                apply_adjudication_to_record(eval_record, manual_adjudications[qid])
            eval_records.append(eval_record)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.tool_use_output:
            tool_use_path = Path(args.tool_use_output)
        elif args.output:
            tool_use_path = Path(args.output)
        else:
            tool_use_path = preds_path.parent / f"tool_use_{model_name}_{ts}.csv"
        write_tool_use_csv(tool_use_path, behavior_records, eval_records)
        print(f"tool-use CSV 已写入: {tool_use_path}")
        print(f"记录数: {len(behavior_records)}；未重新执行 SQL/Python")
        return

    print(f"benchmark: {benchmark_path}")
    if run_asset_paths:
        print(f"provenance assets: run config ({trajectory_dir / 'run_batch.config.yaml'})")
    else:
        print("provenance assets: defaults (no asset paths recorded in run config)")
    num_workers = max(1, int(args.num_workers))
    num_processes = max(0, int(args.num_processes))
    print(f"评估 {len(qids)} 条预测，参考答案 {len(ref_map)} 条，workers={num_workers}, processes={num_processes}\n")
    tasks = (
        (qid, pred_map[qid], ref_map.get(qid), model_name, trajectory_dir,
         args.no_tool_use, manual_adjudications.get(qid), saved_results_dir)
        for qid in qids
    )
    executor = None
    if num_processes:
        executor = ProcessPoolExecutor(max_workers=num_processes)
        outcomes = executor.map(_evaluate_qid_task, tasks)
    elif num_workers > 1:
        executor = ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="text2sql-eval")
        outcomes = executor.map(_evaluate_qid_task, tasks)
    else:
        outcomes = map(_evaluate_qid_task, tasks)

    try:
        for qid, ref, behavior_record, result in outcomes:
            if ref is None or result is None:
                print(f"[WARN] {qid}: 没有参考答案，跳过")
                stats["submission_error"] += 1
                score_total += 1.0
                continue

            difficulty = str(ref.get("difficulty", "unrated") or "unrated")
            if behavior_record is not None:
                behavior_records.append(behavior_record)

            if result.passed:
                stats["passed"] += 1
                score_sum += result.score
                score_total += result.max_score
                eval_records.append({
                    "qid": qid,
                    "domain": _domain_from_qid(qid),
                    "mode": ref.get("mode", "unknown"),
                    "evaluation_kind": ref.get("evaluation_kind", "unknown"),
                    "metric_type": _metric_type(ref.get("evaluation_kind", ""), bool(ref.get("strict_output_schema", False))),
                    "difficulty": difficulty,
                    "passed": True,
                    "score": result.score,
                    "max_score": result.max_score,
                    "pit_diagnostic_flags": result.pit_diagnostic_flags,
                    "pit_review_candidates": result.pit_review_candidates,
                    **comparison_metadata(result),
                })
                print(f"评估 {qid} ... PASS score={result.score:.3f}", flush=True)
            else:
                etype = result.error_type or "mismatch"
                stats[etype] = stats.get(etype, 0) + 1
                score_sum += result.score
                score_total += result.max_score
                eval_records.append({
                    "qid": qid,
                    "domain": _domain_from_qid(qid),
                    "mode": ref.get("mode", "unknown"),
                    "evaluation_kind": ref.get("evaluation_kind", "unknown"),
                    "metric_type": _metric_type(ref.get("evaluation_kind", ""), bool(ref.get("strict_output_schema", False))),
                    "difficulty": difficulty,
                    "passed": False,
                    "score": result.score,
                    "max_score": result.max_score,
                    "pit_diagnostic_flags": result.pit_diagnostic_flags,
                    "pit_review_candidates": result.pit_review_candidates,
                    **comparison_metadata(result),
                })
                print(f"评估 {qid} ... {'FAIL' if result.evaluation_complete else 'UNAVAILABLE'} {etype} score={result.score:.3f}: {result.error_msg}", flush=True)
                failures.append({
                    "qid": qid,
                    "question": ref.get("question", ""),
                    "error_type": etype,
                    "error_msg": result.error_msg or "",
                    "score": result.score,
                    "max_score": result.max_score,
                    "score_details": result.score_details,
                    "pit_diagnostic_flags": result.pit_diagnostic_flags,
                    "pit_review_candidates": result.pit_review_candidates,
                    **comparison_metadata(result),
                    "ref_sql": result.ref_sql,
                    "ref_python": result.ref_python,
                    "gen_sql": result.gen_sql,
                    "gen_python": result.gen_python,
                    "ref_result": result.ref_result,
                    "gen_result": result.gen_result,
                })
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    _report_groups = {
        "passed": ["passed"],
        "mismatch": ["mismatch", "logic_mismatch", "format_mismatch",
                     "field_mismatch", "unit_mismatch", "component_mismatch",
                     "order_mismatch", "value_mismatch"],
        "gen_error": ["gen_error", "syntax_error", "timeout", "schema_mismatch", "early_submit"],
        "ref_error": ["ref_error"],
        "ai_mismatch": ["ai_mismatch"],
        "submission_error": ["submission_error"],
        "evaluation_unavailable": ["judge_error", "visual_contract_error", "artifact_error"],
    }

    total = sum(stats.values())
    score_ratio = score_sum / score_total * 100 if score_total else 0.0
    print(f"\n=== 结果 ===")
    print(f"score: {score_sum:.3f}/{score_total:.0f} ({score_ratio:.1f}%), passed: {stats['passed']}/{int(score_total)} evaluable")
    for key in ("mismatch", "gen_error", "ref_error", "ai_mismatch", "submission_error"):
        count = sum(stats.get(k, 0) for k in _report_groups[key])
        if count > 0:
            print(f"  {key}: {count}")

    # 生成报告
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        report_path = Path(args.output)
    else:
        report_path = preds_path.parent / f"benchmark_{model_name}_{ts}.md"
    tool_use_path = None
    if behavior_records and not args.no_tool_use:
        tool_use_path = (
            Path(args.tool_use_output)
            if args.tool_use_output
            else report_path.parent / f"tool_use_{model_name}_{ts}.csv"
        )
        write_tool_use_csv(tool_use_path, behavior_records, eval_records)

    lines = [
        "# BuySideFlow Benchmark Report",
        f"- model: {model_name}",
        f"- preds: {display_path(preds_path)}",
        f"- preds_sha256: {sha256_file(preds_path)}",
        f"- benchmark: {display_path(benchmark_path)}",
        f"- timestamp: {ts}",
        f"- judging_policy: {'offline_no_model_api' if args.offline_judging else 'configured_model_judges'}",
        f"- evaluation_mode: {'saved_artifacts_without_program_replay' if saved_results_dir else 'program_replay'}",
        *(
            [f"- saved_results: {display_path(saved_results_dir)}",
             f"- saved_results_sha256: {aggregate_sha256([artifact['path'] for qid in qids for artifact in _saved_artifacts_for_task(saved_results_dir, qid, pred_map[qid], ref_map.get(qid))], base_path=saved_results_dir)[0]}"]
            if saved_results_dir else []
        ),
        f"- evaluator_sha256: {sha256_file(PROJECT_ROOT / 'sweagent/text2sql/evaluator.py')}",
        f"- pit_rule_sha256: {sha256_file(PROJECT_ROOT / 'sweagent/text2sql/time_audit.py')}",
        f"- eval_driver_sha256: {sha256_file(Path(__file__))}",
        *(
            ["- manual_adjudications: " + ", ".join(f"{qid}={item['decision']}" for qid, item in sorted(manual_adjudications.items()))]
            if manual_adjudications
            else []
        ),
        "",
    ]
    lines.extend(render_provenance_markdown(build_provenance_rows(
        benchmark_path=benchmark_path,
        asset_paths=run_asset_paths or None,
        reference_paths=reference_files_from_tasks(ref_tasks),
        sidecar_paths=reference_sidecar_files_from_tasks(ref_tasks),
    )))
    lines.extend([
        "## Summary",
        "| item | count | ratio |",
        "| --- | ---: | ---: |",
    ])
    for key in ("passed", "mismatch", "gen_error", "ref_error", "ai_mismatch", "submission_error", "evaluation_unavailable"):
        count = sum(stats.get(k, 0) for k in _report_groups[key])
        if count == 0:
            continue
        ratio = f"{count / total * 100:.1f}%" if total else "-"
        lines.append(f"| {key} | {count} | {ratio} |")
    lines.append(f"| score | {score_sum:.3f}/{score_total:.0f} | {score_ratio:.1f}% |")
    lines.append("")
    if manual_adjudications:
        lines.extend([
            "## Manual Adjudications",
            "| qid | decision | reason |",
            "| --- | --- | --- |",
        ])
        for qid, item in sorted(manual_adjudications.items()):
            lines.append(f"| {qid} | {item['decision']} | {item['reason']} |")
        lines.append("")

    if analysis_pipeline_types:
        for record in eval_records:
            record["analysis_pipeline"] = analysis_pipeline_types.get(record["qid"], "unknown")

    append_visual_results(lines, eval_records)
    _append_record_breakdown(lines, eval_records, title="## Primary vs Secondary Metrics", field="metric_type")
    _append_record_breakdown(lines, eval_records, title="## Domain Breakdown", field="domain")
    if analysis_pipeline_types:
        _append_record_breakdown(lines, eval_records, title="## Analysis Pipeline Breakdown", field="analysis_pipeline")
    _append_record_breakdown(lines, eval_records, title="## Mode Breakdown", field="mode")
    _append_record_breakdown(lines, eval_records, title="## Evaluation Kind Breakdown", field="evaluation_kind")
    lines.extend(["Task difficulty annotation pending; no difficulty stratification.", ""])
    append_pit_diagnostic_breakdowns(lines, eval_records)
    _append_behavior_diagnostics(lines, behavior_records, eval_records, tool_use_path=tool_use_path)

    if failures:
        lines.append("## Failures")
        for rec in failures:
            lines.append(f"### {rec['qid']}")
            lines.append(f"question: {rec['question']}")
            lines.append(f"error_type: {rec['error_type']}")
            if rec.get("pit_diagnostic_flags"):
                lines.append(f"pit_diagnostic_flags: {', '.join(rec['pit_diagnostic_flags'])}")
            if rec.get("pit_review_candidates"):
                lines.append(f"pit_review_candidates: {', '.join(rec['pit_review_candidates'])}")
            lines.append(f"error_msg: {rec['error_msg']}")
            if rec.get("evaluation_complete") is False:
                lines.append("evaluation_status: unavailable (excluded from score denominator)")
            lines.append(f"score: {rec.get('score', 0.0):.3f}/{rec.get('max_score', 1.0):.0f}")
            if rec.get("score_details"):
                lines.append("score_details:")
                lines.append("```json")
                lines.append(json.dumps(rec["score_details"], ensure_ascii=False, indent=2))
                lines.append("```")
            for key, title, lang in (
                ("ref_sql", "reference sql", "sql"),
                ("ref_python", "reference python", "python"),
                ("gen_sql", "generated sql", "sql"),
                ("gen_python", "generated python", "python"),
            ):
                if rec.get(key):
                    lines.append(f"{title}:")
                    lines.append(f"```{lang}")
                    lines.append(str(rec[key]))
                    lines.append("```")
            if rec.get("ref_result") is not None:
                lines.append("reference result:")
                lines.append("```")
                lines.append(format_result_for_report(rec["ref_result"]))
                lines.append("```")
            if rec.get("gen_result") is not None:
                lines.append("generated result:")
                lines.append("```")
                lines.append(format_result_for_report(rec["gen_result"]))
                lines.append("```")
            lines.append("")

    report_path.write_text(anonymize_report_text("\n".join(lines)), encoding="utf-8")
    print(f"\n报告已写入: {report_path}")


if __name__ == "__main__":
    main()
