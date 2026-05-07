"""
独立评估脚本：读取 preds.json + benchmark 参考答案，连数据库评估，生成报告。

用法:
    python run_eval.py
    python run_eval.py --preds trajectories/.../preds.json
    python run_eval.py --data data/dataset
"""
import argparse
import io
import json
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
        benchmark_search_dirs,
        guess_markdown_from_run_name,
        load_benchmark_env,
        resolve_benchmark_from_run_dir,
        resolve_default_benchmark_path,
    )
except ImportError:
    try:
        from text2sql_benchmark.benchmark_paths import (
            benchmark_search_dirs,
            guess_markdown_from_run_name,
            load_benchmark_env,
            resolve_benchmark_from_run_dir,
            resolve_default_benchmark_path,
        )
    except ImportError:
        from benchmark_paths import (
            benchmark_search_dirs,
            guess_markdown_from_run_name,
            load_benchmark_env,
            resolve_benchmark_from_run_dir,
            resolve_default_benchmark_path,
        )

load_benchmark_env()

from sweagent.text2sql.markdown import parse_text2sql_tasks
from sweagent.text2sql.evaluator import compare_items, format_result_for_report
from sweagent.text2sql.provenance import (
    build_provenance_rows,
    reference_files_from_tasks,
    reference_sidecar_files_from_tasks,
    render_provenance_markdown,
    require_db_snapshot_id,
)
from sweagent.run.hooks.text2sql_evaluate import build_tool_use_record, write_tool_use_csv


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
        return trajectory if isinstance(trajectory, list) else []
    return []


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
    lines.append("| group | total | full_score | score |")
    lines.append("| --- | ---: | ---: | ---: |")
    for key, bucket in sorted(groups.items()):
        max_score = float(bucket["max_score"])
        score_text = f"{float(bucket['score']):.3f}/{max_score:.0f}" if max_score else "-"
        lines.append(f"| {key} | {int(bucket['total'])} | {int(bucket['passed'])} | {score_text} |")
    lines.append("")


def _append_pit_diagnostic_breakdown(lines: list[str], failures: list[dict]) -> None:
    counts: dict[str, int] = {}
    total_flagged = 0
    for record in failures:
        flags = record.get("pit_diagnostic_flags") or []
        if not flags:
            continue
        total_flagged += 1
        for flag in flags:
            counts[str(flag)] = counts.get(str(flag), 0) + 1
    if not counts:
        return
    lines.append("## PIT Diagnostic Flags")
    lines.append("| flag | failed_tasks |")
    lines.append("| --- | ---: |")
    lines.append(f"| any_pit_diagnostic_flag | {total_flagged} |")
    for flag, count in sorted(counts.items()):
        lines.append(f"| {flag} | {count} |")
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
    return {
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


def _find_latest_preds() -> Path:
    candidates = sorted(
        (PROJECT_ROOT / "trajectories").glob("**/preds.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return PROJECT_ROOT / "trajectories" / "preds.json"
    return candidates[0]


def _guess_markdown_for_preds(preds_path: Path) -> Path:
    run_config_path = resolve_benchmark_from_run_dir(preds_path.parent)
    if run_config_path is not None:
        return run_config_path
    run_name = preds_path.parent.name
    for directory in benchmark_search_dirs():
        if directory.exists():
            return guess_markdown_from_run_name(run_name)
    return resolve_default_benchmark_path()


def main():
    require_db_snapshot_id()

    default_preds = _find_latest_preds()
    default_markdown = _guess_markdown_for_preds(default_preds)

    parser = argparse.ArgumentParser(description="BuySideFlow standalone evaluator")
    parser.add_argument(
        "--data",
        dest="markdown",
        default=str(default_markdown),
        help="Alias for --markdown; accepts a new-format benchmark folder.",
    )
    parser.add_argument(
        "--preds",
        default=str(default_preds),
        help="生成预测的 preds.json 路径",
    )
    parser.add_argument(
        "--markdown",
        default=str(default_markdown),
        help="题目 benchmark markdown 文件路径（含参考答案）",
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
    args = parser.parse_args()

    preds_path = Path(args.preds)
    if not preds_path.exists():
        print(f"[ERROR] preds.json 不存在: {preds_path}")
        sys.exit(1)

    markdown_path = Path(args.markdown)
    if not markdown_path.exists():
        print(f"[ERROR] markdown 文件不存在: {markdown_path}")
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

    # Load reference benchmark tasks.
    ref_tasks = parse_text2sql_tasks(markdown_path)
    ref_map = {task["id"]: task for task in ref_tasks}
    model_name = _safe_filename(args.model_name) if args.model_name else _guess_model_name(preds_path, preds_raw)
    trajectory_dir = Path(args.trajectory_dir) if args.trajectory_dir else preds_path.parent

    stats = {"passed": 0, "mismatch": 0, "gen_error": 0, "ref_error": 0,
             "ai_mismatch": 0, "submission_error": 0,
             "syntax_error": 0, "timeout": 0, "schema_mismatch": 0,
             "format_mismatch": 0, "logic_mismatch": 0}
    failures = []
    eval_records = []
    behavior_records = []
    score_sum = 0.0
    score_total = 0.0

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
            difficulty = str(ref.get("difficulty", "unknown") or "unknown")
            behavior_records.append(build_tool_use_record(
                qid=qid,
                difficulty=difficulty,
                trajectory=_load_trajectory_for_qid(trajectory_dir, qid),
            ))
            eval_records.append(_metadata_eval_record(
                qid=qid,
                ref=ref,
                difficulty=difficulty,
                score_info=report_scores.get(qid),
                report_scores_available=bool(args.eval_report),
            ))

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.output:
            tool_use_path = Path(args.output)
        else:
            tool_use_path = preds_path.parent / f"tool_use_{model_name}_{ts}.csv"
        write_tool_use_csv(tool_use_path, behavior_records, eval_records)
        print(f"tool-use CSV 已写入: {tool_use_path}")
        print(f"记录数: {len(behavior_records)}；未重新执行 SQL/Python")
        return

    print(f"benchmark: {markdown_path}")
    print(f"评估 {len(qids)} 条预测，参考答案 {len(ref_map)} 条\n")

    for qid in qids:
        gen = pred_map[qid]
        ref = ref_map.get(qid)
        if ref is None:
            print(f"[WARN] {qid}: 没有参考答案，跳过")
            stats["submission_error"] += 1
            score_total += 1.0
            continue

        difficulty = str(ref.get("difficulty", "unknown") or "unknown")
        if not args.no_tool_use:
            behavior_records.append(build_tool_use_record(
                qid=qid,
                difficulty=difficulty,
                trajectory=_load_trajectory_for_qid(trajectory_dir, qid),
            ))

        print(f"评估 {qid} ...", end=" ", flush=True)
        result = compare_items(ref, {
            "id": qid,
            "question": ref.get("question", ""),
            "mode": gen.get("mode", "sql"),
            "sql_code": gen.get("sql_code") or "",
            "python_code": gen.get("python_code") or "",
            "result_vars": list(gen.get("result_vars", [])),
        })

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
            })
            print(f"PASS score={result.score:.3f}")
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
            })
            print(f"FAIL {etype} score={result.score:.3f}: {result.error_msg}")
            failures.append({
                "qid": qid,
                "question": ref.get("question", ""),
                "error_type": etype,
                "error_msg": result.error_msg or "",
                "score": result.score,
                "max_score": result.max_score,
                "score_details": result.score_details,
                "pit_diagnostic_flags": result.pit_diagnostic_flags,
                "ref_sql": result.ref_sql,
                "ref_python": result.ref_python,
                "gen_sql": result.gen_sql,
                "gen_python": result.gen_python,
                "ref_result": result.ref_result,
                "gen_result": result.gen_result,
            })

    _report_groups = {
        "passed": ["passed"],
        "mismatch": ["mismatch", "logic_mismatch", "format_mismatch"],
        "gen_error": ["gen_error", "syntax_error", "timeout", "schema_mismatch", "early_submit"],
        "ref_error": ["ref_error"],
        "ai_mismatch": ["ai_mismatch"],
        "submission_error": ["submission_error"],
    }

    total = sum(stats.values())
    score_ratio = score_sum / score_total * 100 if score_total else 0.0
    print(f"\n=== 结果 ===")
    print(f"score: {score_sum:.3f}/{score_total:.0f} ({score_ratio:.1f}%), full-score: {stats['passed']}/{total}")
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
        tool_use_path = report_path.parent / f"tool_use_{model_name}_{ts}.csv"
        write_tool_use_csv(tool_use_path, behavior_records, eval_records)

    lines = [
        "# BuySideFlow Benchmark Report",
        f"- model: {model_name}",
        f"- preds: {preds_path}",
        f"- benchmark: {markdown_path}",
        f"- timestamp: {ts}",
        "",
    ]
    lines.extend(render_provenance_markdown(build_provenance_rows(
        benchmark_path=markdown_path,
        reference_paths=reference_files_from_tasks(ref_tasks),
        sidecar_paths=reference_sidecar_files_from_tasks(ref_tasks),
    )))
    lines.extend([
        "## Summary",
        "| item | count | ratio |",
        "| --- | ---: | ---: |",
    ])
    for key in ("passed", "mismatch", "gen_error", "ref_error", "ai_mismatch", "submission_error"):
        count = sum(stats.get(k, 0) for k in _report_groups[key])
        if count == 0:
            continue
        ratio = f"{count / total * 100:.1f}%" if total else "-"
        lines.append(f"| {key} | {count} | {ratio} |")
    lines.append(f"| score | {score_sum:.3f}/{score_total:.0f} | {score_ratio:.1f}% |")
    lines.append("")

    _append_record_breakdown(lines, eval_records, title="## Primary vs Secondary Metrics", field="metric_type")
    _append_record_breakdown(lines, eval_records, title="## Domain Breakdown", field="domain")
    _append_record_breakdown(lines, eval_records, title="## Mode Breakdown", field="mode")
    _append_record_breakdown(lines, eval_records, title="## Evaluation Kind Breakdown", field="evaluation_kind")
    _append_record_breakdown(lines, eval_records, title="## Difficulty Breakdown", field="difficulty")
    _append_pit_diagnostic_breakdown(lines, failures)
    _append_behavior_diagnostics(lines, behavior_records, eval_records, tool_use_path=tool_use_path)

    if failures:
        lines.append("## Failures")
        for rec in failures:
            lines.append(f"### {rec['qid']}")
            lines.append(f"question: {rec['question']}")
            lines.append(f"error_type: {rec['error_type']}")
            if rec.get("pit_diagnostic_flags"):
                lines.append(f"pit_diagnostic_flags: {', '.join(rec['pit_diagnostic_flags'])}")
            lines.append(f"error_msg: {rec['error_msg']}")
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

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入: {report_path}")


if __name__ == "__main__":
    main()
