from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sweagent.run.hooks.abstract import RunHook
from sweagent.text2sql.evaluator import compare_items, format_result_for_report
from sweagent.text2sql.provenance import (
    anonymize_report_text,
    build_provenance_rows,
    render_provenance_markdown,
    require_db_snapshot_id,
)
from sweagent.text2sql.time_audit import pit_diagnostic_flags, pit_review_candidates
from sweagent.types import AgentRunResult
from sweagent.utils.log import get_logger


def comparison_metadata(result) -> dict[str, Any]:
    return {"evaluation_complete": result.evaluation_complete,
            "passed": result.passed if result.evaluation_complete else None,
            "data_passed": result.data_passed, "image_passed": result.image_passed,
            "data_score": result.data_score, "image_score": result.image_score,
            "image_status": result.image_status, "error_type": result.error_type,
            "evidence_manifest": result.evidence_manifest}


def append_visual_results(lines, records):
    visual = [r for r in records if r.get("evaluation_kind") in {"vision", "mixed"}]
    if not visual:
        return
    lines.extend(["## Data and Image Results", "Data means comparison with the current reference tables; image correctness is assessed separately.",
                  "Unavailable judgments are excluded from the score denominator.",
                  "| Question | Data match | Image match | Image status | Evidence |",
                  "| --- | --- | --- | --- | --- |"])
    label = lambda value: "Pass" if value is True else "Fail" if value is False else "Unavailable"
    for r in visual:
        evidence = r.get("evidence_manifest")
        lines.append(f"| {r['qid']} | {label(r.get('data_passed'))} | {label(r.get('image_passed'))} | {r.get('image_status', '')} | "
                     + (f"[manifest]({evidence})" if evidence else "") + " |")
    lines.append("")


def _domain_from_qid(qid: str) -> str:
    return qid.split("_", 1)[0] if "_" in qid else "unknown"


def _metric_type(evaluation_kind: str, strict_output_schema: bool) -> str:
    if evaluation_kind == "csv" and strict_output_schema:
        return "primary_tabular"
    if evaluation_kind == "csv":
        return "primary_tabular_loose"
    return "secondary_semantic"


_SCHEMA_TOOL_NAMES = ("search_tables", "describe_tables", "get_columns", "search_columns", "request_schema")
_TOOL_USE_NAMES = (*_SCHEMA_TOOL_NAMES, "run_code", "submit", "reveal_reference_result")
_ACTION_SEPARATOR = "<<<SWE_AGENT_ACTION_SEPARATOR>>>"


def _action_tool_name(action: str) -> str:
    text = (action or "").strip()
    if not text:
        return ""
    match = re.match(r"([A-Za-z_]\w*)\b", text)
    name = match.group(1) if match else text.split(None, 1)[0]
    return name if name in _TOOL_USE_NAMES else ""


def iter_action_tool_names(action: str) -> list[str]:
    """Return tool names from one trajectory action string.

    A single SWE-agent step may contain multiple tool calls separated by the
    action separator, so counting only the first token underestimates tool use.
    """
    names: list[str] = []
    for part in str(action or "").split(_ACTION_SEPARATOR):
        name = _action_tool_name(part)
        if name:
            names.append(name)
    return names


def count_tool_calls(trajectory: list[dict[str, Any]] | None) -> tuple[dict[str, int], int]:
    counts = {name: 0 for name in _TOOL_USE_NAMES}
    steps = trajectory or []
    for step in steps:
        if not isinstance(step, dict):
            continue
        for tool_name in iter_action_tool_names(str(step.get("action") or "")):
            counts[tool_name] += 1
    return counts, len(steps)


def build_tool_use_record(
    *,
    qid: str,
    difficulty: str,
    trajectory: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    tool_counts, total_turns = count_tool_calls(trajectory)
    run_code_cnt = tool_counts["run_code"]
    schema_explore_cnt = sum(tool_counts[name] for name in _SCHEMA_TOOL_NAMES)
    etl_ratio = run_code_cnt / total_turns if total_turns > 0 else 0.0
    return {
        "qid": qid,
        "difficulty": difficulty or "unknown",
        "total_turns": total_turns,
        "search_tables_cnt": tool_counts["search_tables"],
        "describe_tables_cnt": tool_counts["describe_tables"],
        "get_columns_cnt": tool_counts["get_columns"],
        "search_columns_cnt": tool_counts["search_columns"],
        "request_schema_cnt": tool_counts["request_schema"],
        "run_code_cnt": run_code_cnt,
        "submit_cnt": tool_counts["submit"],
        "reveal_reference_result_cnt": tool_counts["reveal_reference_result"],
        "schema_explore_cnt": schema_explore_cnt,
        "etl_ratio": round(etl_ratio, 3),
        "is_early_submit": run_code_cnt < 1 and total_turns > 0,
    }


def write_tool_use_csv(
    path: Path,
    per_instance_behavior: list[dict[str, Any]],
    eval_records: list[dict[str, Any]],
) -> None:
    eval_by_qid = {record["qid"]: record for record in eval_records}
    fieldnames = [
        "qid",
        "domain",
        "difficulty",
        "mode",
        "evaluation_kind",
        "passed",
        "score",
        "max_score",
        "evaluation_complete", "data_passed", "image_passed", "data_score", "image_score",
        "image_status", "error_type", "evidence_manifest",
        "pit_diagnostic_flags",
        "pit_review_candidates",
        "total_turns",
        "schema_explore_cnt",
        "run_code_cnt",
        "submit_cnt",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for behavior in per_instance_behavior:
            qid = str(behavior.get("qid") or "")
            eval_record = eval_by_qid.get(qid, {})
            row = {name: "" for name in fieldnames}
            row.update({
                "qid": qid,
                "domain": eval_record.get("domain", _domain_from_qid(qid)),
                "difficulty": behavior.get("difficulty", eval_record.get("difficulty", "")),
                "mode": eval_record.get("mode", ""),
                "evaluation_kind": eval_record.get("evaluation_kind", ""),
                "passed": eval_record.get("passed", ""),
                "score": eval_record.get("score", ""),
                "max_score": eval_record.get("max_score", ""),
                "pit_diagnostic_flags": ",".join(eval_record.get("pit_diagnostic_flags") or []),
                "pit_review_candidates": ",".join(eval_record.get("pit_review_candidates") or []),
            })
            for key, value in behavior.items():
                if key in row:
                    row[key] = value
            for name in ("evaluation_complete", "data_passed", "image_passed", "data_score", "image_score", "image_status", "error_type", "evidence_manifest"):
                row[name] = eval_record.get(name, "")
            if eval_record.get("evaluation_complete") is False:
                row["passed"] = row["score"] = ""
            writer.writerow(row)


def append_pit_diagnostic_breakdowns(lines: list[str], records: list[dict[str, Any]]) -> None:
    """Render formal PIT flags and manual-review candidates from all evaluated tasks."""
    for title, field, any_label in (
        ("## PIT Diagnostic Flags", "pit_diagnostic_flags", "any_pit_diagnostic_flag"),
        ("## PIT Manual Review Candidates", "pit_review_candidates", "any_pit_review_candidate"),
    ):
        counts: dict[str, list[int]] = {}
        any_counts = [0, 0, 0]
        for record in records:
            if record.get("evaluation_complete") is False:
                continue
            flags = record.get(field) or []
            if not flags:
                continue
            passed = bool(record.get("passed"))
            any_counts[0] += 1
            any_counts[1 if passed else 2] += 1
            for flag in flags:
                bucket = counts.setdefault(str(flag), [0, 0, 0])
                bucket[0] += 1
                bucket[1 if passed else 2] += 1
        if not counts:
            continue
        lines.append(title)
        lines.append("Counts use completed evaluations; flags for unavailable tasks remain in the per-task CSV.")
        lines.append("| flag | all_tasks | passed_tasks | failed_tasks |")
        lines.append("| --- | ---: | ---: | ---: |")
        lines.append(f"| {any_label} | {any_counts[0]} | {any_counts[1]} | {any_counts[2]} |")
        for flag, (all_count, passed_count, failed_count) in sorted(counts.items()):
            lines.append(f"| {flag} | {all_count} | {passed_count} | {failed_count} |")
        lines.append("")


class Text2SQLEvaluateHook(RunHook):
    """Evaluate BuySideFlow agent submissions against reference outputs/code."""

    def __init__(self, output_dir: Path, model_name: str) -> None:
        super().__init__()
        self.output_dir = output_dir
        self.model_name = model_name
        self.logger = get_logger("text2sql-eval", emoji="🧪")
        self._timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._current_problem_statement = None
        self._problem_statement_by_instance_id: dict[str, Any] = {}
        self._failures: list[dict[str, Any]] = []
        self._score_sum = 0.0
        self._score_total = 0.0
        self._stats = {
            "passed": 0,
            "mismatch": 0,
            "gen_error": 0,
            "ref_error": 0,
            "ai_mismatch": 0,
            "submission_error": 0,
            "syntax_error": 0,
            "timeout": 0,
            "schema_mismatch": 0,
            "format_mismatch": 0,
            "logic_mismatch": 0,
            "early_submit": 0,
        }
        # 行为统计（behavior statistics）
        self._behavior_stats = {
            "total_instances": 0,          # 总题数
            "early_submit_count": 0,       # Early Submit 次数（submit前run_code<1次）
            "navigation_trap_count": 0,    # Navigation Trap 次数（schema探索>=6次且失败）
            "edit_test_ratios": [],        # Edit-Test Loop Ratio 列表
        }
        # 每道题行为明细
        self._per_instance_behavior: list[dict[str, Any]] = []
        self._eval_records: list[dict[str, Any]] = []
        self._benchmark_source_path = ""
        self._provenance_asset_paths: set[str] = set()
        self._provenance_reference_paths: set[str] = set()
        self._provenance_sidecar_paths: set[str] = set()

    def on_start(self) -> None:
        require_db_snapshot_id()
        self.logger.info("BuySideFlow evaluation started: %s", self.output_dir)

    def on_instance_start(self, *, index, env, problem_statement) -> None:
        self._current_problem_statement = problem_statement
        self._problem_statement_by_instance_id[str(problem_statement.id)] = problem_statement
        extra_fields = getattr(problem_statement, "extra_fields", {}) or {}
        if extra_fields.get("benchmark_source_path") and not self._benchmark_source_path:
            self._benchmark_source_path = str(extra_fields.get("benchmark_source_path"))
        for key in (
            "schema_path",
            "catalog_schema_path",
            "business_metadata_path",
            "selection_guidance_path",
            "fund_rules_path",
            "bond_rules_path",
        ):
            value = extra_fields.get(key)
            if value:
                self._provenance_asset_paths.add(str(value))
        for raw in extra_fields.get("reference_artifact_paths", []) or []:
            if isinstance(raw, dict):
                value = raw.get("path") or raw.get("file") or raw.get("filename")
            else:
                value = raw
            if value:
                self._provenance_reference_paths.add(str(value))
        for raw in extra_fields.get("reference_sidecar_paths", []) or []:
            if isinstance(raw, dict):
                value = raw.get("path") or raw.get("file") or raw.get("filename")
            else:
                value = raw
            if value:
                self._provenance_reference_paths.add(str(value))
                self._provenance_sidecar_paths.add(str(value))
        result_dir = extra_fields.get("result_dir")
        for name in extra_fields.get("reference_links", []) or []:
            if result_dir:
                self._provenance_reference_paths.add(str(Path(result_dir) / str(name)))

    def _record_behavior(self, record: dict[str, Any]) -> None:
        """记录单题行为数据。"""
        self._per_instance_behavior.append(record)

    def _record_eval_outcome(
        self,
        *,
        qid: str,
        mode: str,
        evaluation_kind: str,
        strict_output_schema: bool,
        difficulty: str,
        passed: bool,
        score: float,
        max_score: float,
        pit_flags: list[str] | None = None,
        pit_candidates: list[str] | None = None,
        comparison_result: Any = None,
    ) -> None:
        self._eval_records.append({
            "qid": qid,
            "domain": _domain_from_qid(qid),
            "mode": mode or "unknown",
            "evaluation_kind": evaluation_kind or "unknown",
            "metric_type": _metric_type(evaluation_kind or "", strict_output_schema),
            "difficulty": difficulty or "unknown",
            "passed": bool(passed),
            "score": float(score),
            "max_score": float(max_score),
            "pit_diagnostic_flags": list(pit_flags or []),
            "pit_review_candidates": list(pit_candidates or []),
            **(comparison_metadata(comparison_result) if comparison_result is not None else {}),
        })

    def _render_record_breakdown(self, lines: list[str], *, title: str, field: str) -> None:
        groups: dict[str, dict[str, Any]] = {}
        for record in self._eval_records:
            key = str(record.get(field) or "unknown")
            bucket = groups.setdefault(key, {"total": 0, "passed": 0, "score": 0.0, "max_score": 0.0})
            bucket["total"] += 1
            bucket["passed"] += int(bool(record.get("passed")))
            bucket["score"] += float(record.get("score", 0.0))
            bucket["max_score"] += float(record.get("max_score", 0.0))
        if not groups:
            return
        lines.append(title)
        lines.append("| group | total | passed | score |")
        lines.append("| --- | ---: | ---: | ---: |")
        for key, bucket in sorted(groups.items()):
            score = f"{bucket['score']:.3f}/{bucket['max_score']:.0f}" if bucket["max_score"] else "-"
            lines.append(f"| {key} | {bucket['total']} | {bucket['passed']} | {score} |")
        lines.append("")

    def _render_pit_diagnostic_breakdown(self, lines: list[str]) -> None:
        append_pit_diagnostic_breakdowns(lines, self._eval_records)

    def on_instance_completed(self, *, result: AgentRunResult) -> None:
        instance_id = result.info.get("instance_id")
        if instance_id:
            problem_statement = self._problem_statement_by_instance_id.pop(str(instance_id), None)
            if problem_statement is None:
                self.logger.warning("No problem statement found for instance_id=%s; skipping evaluation.", instance_id)
                return
        else:
            problem_statement = self._current_problem_statement
        if problem_statement is None:
            return

        qid = str(problem_statement.id)
        question = problem_statement.question
        raw_submission = result.info.get("submission") or ""
        extra_fields = getattr(problem_statement, "extra_fields", {}) or {}
        evaluation_kind = str(extra_fields.get("evaluation_kind", "") or "")
        strict_output_schema = bool(extra_fields.get("strict_output_schema", False))
        mode = str(getattr(problem_statement, "mode", "") or "unknown")

        # Legacy heuristic labels are retired; human workflow annotation is pending.
        difficulty = "unrated"

        # 行为分析：遍历 trajectory 统计工具调用
        behavior_rec = build_tool_use_record(
            qid=qid,
            difficulty=difficulty,
            trajectory=result.trajectory or [],
        )
        total_turns = int(behavior_rec["total_turns"])
        run_code_cnt = int(behavior_rec["run_code_cnt"])
        schema_explore_cnt = int(behavior_rec["schema_explore_cnt"])
        is_early_submit = bool(behavior_rec["is_early_submit"])
        etl_ratio = float(behavior_rec["etl_ratio"])

        self._behavior_stats["total_instances"] += 1
        self._behavior_stats["edit_test_ratios"].append(etl_ratio)

        if extra_fields.get("reference_status") == "blocked":
            generated = {}
            submission_diagnostic = ""
            if not raw_submission:
                submission_diagnostic = "submission is empty"
            else:
                try:
                    generated = json.loads(raw_submission)
                    if not isinstance(generated, dict):
                        generated = {}
                        submission_diagnostic = "submission is not a JSON object"
                except json.JSONDecodeError as exc:
                    submission_diagnostic = f"submission is not valid JSON: {exc}"
            unavailable = compare_items(
                {"id": qid, "question": question, "evaluation_kind": evaluation_kind,
                 "reference_status": "blocked", "reference_error": extra_fields.get("reference_error"),
                 "sql_code": problem_statement.reference_sql,
                 "python_code": problem_statement.reference_python}, generated,
            )
            self._stats["ref_error"] += 1
            if is_early_submit:
                self._behavior_stats["early_submit_count"] += 1
            self._failures.append({
                "qid": qid, "question": question, "error_type": unavailable.error_type,
                "error_msg": unavailable.error_msg, "score": 0.0, "max_score": 0.0,
                **comparison_metadata(unavailable), "submission_diagnostic": submission_diagnostic,
                "raw_submission": raw_submission, "gen_sql": unavailable.gen_sql,
                "gen_python": unavailable.gen_python,
                "pit_diagnostic_flags": unavailable.pit_diagnostic_flags,
                "pit_review_candidates": unavailable.pit_review_candidates,
            })
            self._record_eval_outcome(
                qid=qid, mode=mode, evaluation_kind=evaluation_kind,
                strict_output_schema=strict_output_schema, difficulty=difficulty,
                passed=False, score=0.0, max_score=0.0,
                pit_flags=unavailable.pit_diagnostic_flags,
                pit_candidates=unavailable.pit_review_candidates, comparison_result=unavailable,
            )
            self._record_behavior(behavior_rec)
            return

        # 空提交（empty submission）
        if not raw_submission:
            self._stats["submission_error"] += 1
            self._score_total += 1.0
            if is_early_submit:
                self._behavior_stats["early_submit_count"] += 1
            if schema_explore_cnt >= 6:
                self._behavior_stats["navigation_trap_count"] += 1
            self._failures.append(
                {
                    "qid": qid,
                    "question": question,
                    "error_type": "submission_error",
                    "error_msg": "submission is empty",
                    "score": 0.0,
                    "max_score": 1.0,
                }
            )
            self._record_eval_outcome(
                qid=qid,
                mode=mode,
                evaluation_kind=evaluation_kind,
                strict_output_schema=strict_output_schema,
                difficulty=difficulty,
                passed=False,
                score=0.0,
                max_score=1.0,
            )
            self._record_behavior(behavior_rec)
            return

        # 解析生成结果
        try:
            generated = json.loads(raw_submission)
        except json.JSONDecodeError as exc:
            self._stats["submission_error"] += 1
            self._score_total += 1.0
            if is_early_submit:
                self._behavior_stats["early_submit_count"] += 1
            if schema_explore_cnt >= 6:
                self._behavior_stats["navigation_trap_count"] += 1
            self._failures.append(
                {
                    "qid": qid,
                    "question": question,
                    "error_type": "submission_error",
                    "error_msg": f"submission is not valid JSON: {exc}",
                    "score": 0.0,
                    "max_score": 1.0,
                    "raw_submission": raw_submission,
                }
            )
            self._record_eval_outcome(
                qid=qid,
                mode=mode,
                evaluation_kind=evaluation_kind,
                strict_output_schema=strict_output_schema,
                difficulty=difficulty,
                passed=False,
                score=0.0,
                max_score=1.0,
            )
            self._record_behavior(behavior_rec)
            return

        reference_item = {
            "id": qid,
            "question": question,
            "mode": problem_statement.mode,
            "sql_code": problem_statement.reference_sql,
            "python_code": problem_statement.reference_python,
            "result_vars": list(problem_statement.result_vars),
            "reference_results": list(getattr(problem_statement, "reference_results", []) or []),
            "reference_artifact_paths": list(problem_statement.extra_fields.get("reference_artifact_paths", []) or []),
            "reference_sidecar_paths": list(problem_statement.extra_fields.get("reference_sidecar_paths", []) or []),
            "evaluation_kind": evaluation_kind,
            "strict_output_schema": strict_output_schema,
            "single_numeric_output": extra_fields.get("single_numeric_output", False),
            "column_order_matters": bool(extra_fields.get("column_order_matters", True)),
            "comparison_rules": extra_fields.get("comparison_rules"),
            "reference_status": extra_fields.get("reference_status"),
            "reference_error": extra_fields.get("reference_error"),
        }
        generated_item = {
            "id": qid,
            "question": question,
            "mode": generated.get("mode", "sql"),
            "model_name": self.model_name,
            "sql_code": generated.get("sql_code") or "",
            "python_code": generated.get("python_code") or "",
            "result_vars": list(generated.get("result_vars", [])),
        }

        try:
            compare_result = compare_items(reference_item, generated_item)
        except Exception as exc:
            exception_pit_flags = pit_diagnostic_flags(
                question=question,
                sql_code=generated_item.get("sql_code", ""),
                python_code=generated_item.get("python_code", ""),
            )
            exception_pit_candidates = pit_review_candidates(
                question=question,
                sql_code=generated_item.get("sql_code", ""),
                python_code=generated_item.get("python_code", ""),
            )
            error_type = "gen_error"
            if is_early_submit:
                error_type = "early_submit"
            self._stats[error_type] = self._stats.get(error_type, 0) + 1
            self._score_total += 1.0
            if is_early_submit:
                self._behavior_stats["early_submit_count"] += 1
            if schema_explore_cnt >= 6:
                self._behavior_stats["navigation_trap_count"] += 1
            self._failures.append(
                {
                    "qid": qid,
                    "question": question,
                    "error_type": error_type,
                    "error_msg": f"compare_items raised {type(exc).__name__}: {exc}",
                    "score": 0.0,
                    "max_score": 1.0,
                    "pit_diagnostic_flags": exception_pit_flags,
                    "pit_review_candidates": exception_pit_candidates,
                }
            )
            self._record_eval_outcome(
                qid=qid,
                mode=mode,
                evaluation_kind=evaluation_kind,
                strict_output_schema=strict_output_schema,
                difficulty=difficulty,
                passed=False,
                score=0.0,
                max_score=1.0,
                pit_flags=exception_pit_flags,
                pit_candidates=exception_pit_candidates,
            )
            self._record_behavior(behavior_rec)
            return

        if compare_result.passed:
            self._stats["passed"] += 1
            self._score_sum += compare_result.score
            self._score_total += compare_result.max_score
            self._record_eval_outcome(
                qid=qid,
                mode=mode,
                evaluation_kind=evaluation_kind,
                strict_output_schema=strict_output_schema,
                difficulty=difficulty,
                passed=True,
                score=compare_result.score,
                max_score=compare_result.max_score,
                pit_flags=compare_result.pit_diagnostic_flags,
                pit_candidates=compare_result.pit_review_candidates,
                comparison_result=compare_result,
            )
            self._record_behavior(behavior_rec)
            return

        error_type = compare_result.error_type or "mismatch"
        # Early Submit 覆盖：如果 submit 前没 run_code 且失败了，归类为 early_submit
        if is_early_submit and error_type not in ("submission_error", "ref_error", "judge_error", "visual_contract_error", "artifact_error"):
            error_type = "early_submit"

        self._stats[error_type] = self._stats.get(error_type, 0) + 1
        self._score_sum += compare_result.score
        self._score_total += compare_result.max_score
        if is_early_submit:
            self._behavior_stats["early_submit_count"] += 1
        if schema_explore_cnt >= 6 and compare_result.evaluation_complete:
            self._behavior_stats["navigation_trap_count"] += 1

        self._failures.append(
            {
                "qid": qid,
                "question": question,
                "error_type": error_type,
                "error_msg": compare_result.error_msg or "",
                "score": compare_result.score,
                "max_score": compare_result.max_score,
                "score_details": compare_result.score_details,
                **comparison_metadata(compare_result),
                "pit_diagnostic_flags": compare_result.pit_diagnostic_flags,
                "pit_review_candidates": compare_result.pit_review_candidates,
                "ref_sql": compare_result.ref_sql,
                "ref_python": compare_result.ref_python,
                "gen_sql": compare_result.gen_sql,
                "gen_python": compare_result.gen_python,
                "ref_result": compare_result.ref_result,
                "gen_result": compare_result.gen_result,
            }
        )
        self._record_eval_outcome(
            qid=qid,
            mode=mode,
            evaluation_kind=evaluation_kind,
            strict_output_schema=strict_output_schema,
            difficulty=difficulty,
            passed=False,
            score=compare_result.score,
            max_score=compare_result.max_score,
            pit_flags=compare_result.pit_diagnostic_flags,
            pit_candidates=compare_result.pit_review_candidates,
            comparison_result=compare_result,
        )
        self._record_behavior(behavior_rec)

    def on_end(self) -> None:
        total = sum(self._stats.values())
        score_ratio = (self._score_sum / self._score_total * 100) if self._score_total else 0.0

        # ESR (Early Submit Rate，过早提交率)
        esr = (
            self._behavior_stats["early_submit_count"] / self._behavior_stats["total_instances"] * 100
        ) if self._behavior_stats["total_instances"] else 0.0
        # NTR (Navigation Trap Rate，探索陷阱率)
        ntr = (
            self._behavior_stats["navigation_trap_count"] / self._behavior_stats["total_instances"] * 100
        ) if self._behavior_stats["total_instances"] else 0.0
        # Avg ETL (Edit-Test Loop Ratio，编辑测试循环强度)
        avg_etl = (
            sum(self._behavior_stats["edit_test_ratios"]) / len(self._behavior_stats["edit_test_ratios"])
        ) if self._behavior_stats["edit_test_ratios"] else 0.0

        # run_code 次数分桶统计
        run_code_buckets = {"0次": 0, "1-3次": 0, ">3次": 0}
        for rec in self._per_instance_behavior:
            cnt = rec.get("run_code_cnt", 0)
            if cnt == 0:
                run_code_buckets["0次"] += 1
            elif cnt <= 3:
                run_code_buckets["1-3次"] += 1
            else:
                run_code_buckets[">3次"] += 1

        tool_use_path = self.output_dir / f"tool_use_{self.model_name}_{self._timestamp}.csv"
        write_tool_use_csv(tool_use_path, self._per_instance_behavior, self._eval_records)

        lines = [
            "# BuySideFlow Benchmark Report",
            f"- model: {self.model_name}",
            f"- timestamp: {self._timestamp}",
            "",
        ]
        lines.extend(render_provenance_markdown(build_provenance_rows(
            benchmark_path=self._benchmark_source_path or None,
            asset_paths=sorted(self._provenance_asset_paths),
            reference_paths=sorted(self._provenance_reference_paths),
            sidecar_paths=sorted(self._provenance_sidecar_paths),
        )))
        lines.extend([
            "## Summary",
            "| item | count | ratio |",
            "| --- | ---: | ---: |",
        ])
        # error_type 中文映射表（显示用，不改动底层统计键）
        # 报表层面合并：细粒度统计归回六大类展示，item 保持英文字段名
        _report_groups = {
            "passed": ["passed"],
            "mismatch": ["mismatch", "logic_mismatch", "format_mismatch"],
            "gen_error": ["gen_error", "syntax_error", "timeout", "schema_mismatch", "early_submit"],
            "ref_error": ["ref_error"],
            "ai_mismatch": ["ai_mismatch"],
            "submission_error": ["submission_error"],
            "evaluation_unavailable": ["judge_error", "visual_contract_error", "artifact_error"],
        }
        for key in ("passed", "mismatch", "gen_error", "ref_error", "ai_mismatch", "submission_error", "evaluation_unavailable"):
            count = sum(self._stats.get(k, 0) for k in _report_groups[key])
            if count == 0:
                continue
            ratio = f"{count / total * 100:.1f}%" if total else "-"
            lines.append(f"| {key} | {count} | {ratio} |")
        lines.append(f"| score | {self._score_sum:.3f}/{self._score_total:.0f} | {score_ratio:.1f}% |")
        lines.append("")

        append_visual_results(lines, self._eval_records)
        self._render_record_breakdown(lines, title="## Primary vs Secondary Metrics", field="metric_type")
        self._render_record_breakdown(lines, title="## Domain Breakdown", field="domain")
        self._render_record_breakdown(lines, title="## Mode Breakdown", field="mode")
        self._render_record_breakdown(lines, title="## Evaluation Kind Breakdown", field="evaluation_kind")

        lines.append("Difficulty stratification is suspended pending validated human workflow annotation.")
        lines.append("")

        self._render_pit_diagnostic_breakdown(lines)

        # 行为诊断统计
        lines.append("## Agent Behavior Diagnostics")
        lines.append("| metric | value | description |")
        lines.append("| --- | ---: | --- |")
        lines.append(f"| ESR (Early Submit Rate，过早提交率) | {esr:.1f}% | submit前run_code调用<1次的题目占比 |")
        lines.append(f"| NTR (Navigation Trap Rate，探索陷阱率) | {ntr:.1f}% | schema探索工具调用>=6次且最终失败的题目占比 |")
        lines.append(f"| Avg ETL (Edit-Test Loop Ratio，编辑测试循环强度) | {avg_etl:.3f} | run_code次数 / trajectory总步数 的平均值 |")
        lines.append(f"| per-task tool-use table | `{tool_use_path.name}` | 每题工具调用明细 CSV |")
        lines.append("")
        lines.append("### Run Code Distribution（run_code调用次数分布）")
        lines.append("| bucket | count |")
        lines.append("| --- | ---: |")
        for bucket, count in run_code_buckets.items():
            lines.append(f"| {bucket} | {count} |")
        lines.append("")

        if self._failures:
            lines.append("## Failures")
            for record in self._failures:
                lines.append(f"### {record['qid']}")
                lines.append(f"question: {record['question']}")
                lines.append(f"error_type: {record['error_type']}")
                if record.get("pit_diagnostic_flags"):
                    lines.append(f"pit_diagnostic_flags: {', '.join(record['pit_diagnostic_flags'])}")
                if record.get("pit_review_candidates"):
                    lines.append(f"pit_review_candidates: {', '.join(record['pit_review_candidates'])}")
                lines.append(f"error_msg: {record['error_msg']}")
                if record.get("evaluation_complete") is False:
                    lines.append("evaluation_status: unavailable (excluded from score denominator)")
                if record.get("submission_diagnostic"):
                    lines.append(f"submission_diagnostic: {record['submission_diagnostic']}")
                lines.append(f"score: {record.get('score', 0.0):.3f}/{record.get('max_score', 1.0):.0f}")
                if record.get("score_details"):
                    lines.append("score_details:")
                    lines.append("```json")
                    lines.append(json.dumps(record["score_details"], ensure_ascii=False, indent=2))
                    lines.append("```")
                for key, title, language in (
                    ("ref_sql", "reference sql", "sql"),
                    ("ref_python", "reference python", "python"),
                    ("gen_sql", "generated sql", "sql"),
                    ("gen_python", "generated python", "python"),
                ):
                    if record.get(key):
                        lines.append(f"{title}:")
                        lines.append(f"```{language}")
                        lines.append(str(record[key]))
                        lines.append("```")
                if record.get("ref_result") is not None:
                    lines.append("reference result:")
                    lines.append("```")
                    lines.append(format_result_for_report(record["ref_result"]))
                    lines.append("```")
                if record.get("gen_result") is not None:
                    lines.append("generated result:")
                    lines.append("```")
                    lines.append(format_result_for_report(record["gen_result"]))
                    lines.append("```")
                if record.get("raw_submission"):
                    lines.append("raw submission:")
                    lines.append("```json")
                    lines.append(str(record["raw_submission"]))
                    lines.append("```")
                lines.append("")

        report_path = self.output_dir / f"benchmark_{self.model_name}_{self._timestamp}.md"
        report_path.write_text(anonymize_report_text("\n".join(lines)), encoding="utf-8")
        self.logger.info("BuySideFlow evaluation report written to %s", report_path)

    def _write_tool_use_csv(self, path: Path) -> None:
        write_tool_use_csv(path, self._per_instance_behavior, self._eval_records)
