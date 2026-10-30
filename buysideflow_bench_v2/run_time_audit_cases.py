"""Static PIT screening of model submissions listed in a workbook source manifest.

Reads saved task contracts and final predictions; never executes model code
or edits the workbook. The default batch CLI is offline; audit_case() may use
explicit time-rule/date callbacks. Relative source paths are resolved against
the repository root. The manifest defines the audited workbook version.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parent
ENGINE_PATH = ROOT / "sweagent/text2sql/time_audit_single.py"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_engine():
    spec = importlib.util.spec_from_file_location("case_time_audit_single", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_sources(data: bytes) -> list[dict]:
    rows = []
    seen_cases, seen_instances, seen_rows = set(), set(), set()
    for number, line in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise ValueError(f"Invalid source JSON at line {number}") from None
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ("case_id", "model", "qid")
        ) or type(row.get("workbook_row")) is not int or row["workbook_row"] < 2:
            raise ValueError(f"Invalid source identity at line {number}")
        identity = (row["model"], row["qid"])
        if row["case_id"] in seen_cases or identity in seen_instances or row["workbook_row"] in seen_rows:
            raise ValueError(f"Duplicate source identity at line {number}")
        seen_cases.add(row["case_id"])
        seen_instances.add(identity)
        seen_rows.add(row["workbook_row"])
        rows.append(row)
    if not rows:
        raise ValueError("Source manifest is empty")
    return rows


def read_verified(row: dict, kind: str, evidence: dict) -> bytes:
    path, expected = row.get(f"current_{kind}_file"), row.get(f"current_{kind}_sha256")
    if not isinstance(path, str) or not path or not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"missing_source_metadata:{kind}")
    try:
        data = (ROOT / path).read_bytes()
    except OSError:
        raise ValueError(f"source_unreadable:{kind}") from None
    actual = sha256(data)
    evidence[kind] = {"path": path, "expected_sha256": expected, "sha256": actual}
    if actual != expected.lower():
        raise ValueError(f"source_hash_mismatch:{kind}")
    return data


def audit_case(row: dict, engine, **time_options) -> dict:
    record = {
        "id": row["case_id"], "case_id": row["case_id"], "qid": row["qid"],
        "model": row["model"], "workbook_row": row["workbook_row"],
        "recorded_workbook_auto_outcome": row.get("workbook_auto_outcome"),
        "source_files": {}, "audit_status": "error", "screening_status": "input_error",
    }
    try:
        pred_bytes = read_verified(row, "pred", record["source_files"])
        config_bytes = read_verified(row, "config", record["source_files"])
        # The trajectory is linked evidence; this adapter does not review its steps.
        read_verified(row, "traj", record["source_files"])
        try:
            config = yaml.load(config_bytes, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
            if isinstance(config, str):
                config = json.loads(config)
            statement = config["problem_statement"]
            question = statement["question"]
            inputs = statement.get("extra_fields", {}).get("inputs", {})
        except (ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
            raise ValueError("invalid_saved_task_contract") from None
        if statement.get("id") != row["qid"]:
            raise ValueError("config_instance_id_mismatch")
        if not isinstance(question, str) or not question.strip() or not isinstance(inputs, dict):
            raise ValueError("invalid_saved_question_or_inputs")
        record.update(question=question, inputs=inputs, question_sha256=sha256(question.encode("utf-8")))
        try:
            pred = json.loads(pred_bytes)
        except ValueError:
            raise ValueError("invalid_prediction_json") from None
        if not isinstance(pred, dict) or pred.get("instance_id") != row["qid"]:
            raise ValueError("prediction_instance_id_mismatch")
        patch = pred.get("model_patch")
        if patch is None or patch == "":
            raise ValueError("missing_final_submission")
        if isinstance(patch, str):
            try:
                patch = json.loads(patch)
            except ValueError:
                raise ValueError("invalid_final_submission_json") from None
        if not isinstance(patch, dict):
            raise ValueError("invalid_final_submission_object")
        sql, python = patch.get("sql_code"), patch.get("python_code")
        if any(value is not None and not isinstance(value, str) for value in (sql, python)):
            raise ValueError("invalid_code_type")
        sql, python = sql or "", python or ""
        if not sql.strip() and not python.strip():
            raise ValueError("empty_final_submission")
        record["mode"] = patch.get("mode")
        result = engine.audit_time_anchor_record(
            qid=row["case_id"], question=question, inputs=inputs, sql_code=sql, python_code=python,
            **time_options,
        )
        record.update(result)
        # Two independent views, not two competing verdicts. A case can have
        # both a checker limitation and a code-level clue requiring expertise.
        record["semantic_candidates"] = [flag for flag in result["pit_diagnostic_flags"]
                                         if flag != "guard_unanchored"] + result["pit_review_candidates"]
        if any(h.get("evidence_level") == "schema" for values in result["pit_flag_details"].values()
               for h in values):
            record["coverage_notes"].append("registry_evidence:schema_only")
        record["audit_status"] = engine.audit_status(result)
        record["screening_status"] = {
            "error": "input_error", "fail": "flagged", "warn": "review", "pass": "no_flag",
        }[record["audit_status"]]
        if record["screening_status"] == "no_flag" and record.get("pit_review_candidates"):
            record["screening_status"] = "review"
    except ValueError as exc:
        record["input_error"] = str(exc)
    return record


def render_report(records: list[dict], provenance: dict) -> str:
    counts = Counter(r["screening_status"] for r in records)
    lines = ["# 模型实例 PIT 静态初审", "",
             f"- 来源清单：`{provenance['source_manifest']}`",
             f"- 清单 SHA-256：`{provenance['source_manifest_sha256']}`",
             f"- 引擎 SHA-256：`{provenance['engine_sha256']}`",
             f"- 实例数：{len(records)}", "",
             "| 初筛状态 | 数量 |", "| --- | ---: |"]
    lines += [f"| {key} | {counts[key]} |" for key in ("flagged", "review", "no_flag", "input_error")]
    lines += ["", "flagged 表示规则命中，review 表示需复核，no_flag 表示未命中现有规则；均不是最终 PIT 裁决。"
              "input_error 表示来源、提交或日期契约无法用于初审。原工作簿自动结果仅按来源清单保留，不参与本次判定。",
              "", "本报告使用保存的实际题面、参数和最终提交，不读取当前参考作为模型代码。"
              "轨迹仅校验指纹，未逐步审读；未连接数据库、执行模型代码或修改工作簿。"
              "年份级锚点、多观察日和滚动回测仍须逐决策时点核验。工作簿版本以输入来源清单为准。", "",
              "日期比较不再默认容忍 ±7 天，保留日级观察日的“< 次日零点”边界；Python 仅对简单直接行过滤核对截止日期，"
              "无法绑定的表达式列为复核。时序操作保留行号，但未核实排序、数据日期及最终决策依赖，"
              "仅列 temporal_operator_candidate，不据此确认 PIT 违规。程序违规与输出变化仍须分别核验。", "",
              "JSONL 中 coverage_notes 记录检查器覆盖或证据不足；semantic_candidates 记录程序疑点，"
              "两者可同时存在，均不等同于确认错误。字段按实际别名归属，无法解析时保留覆盖说明；"
              "不再把普通 ret/return 名称或跨查询的输出别名当成未来标签。", "",
              "| Excel行 | 案例 | 模型 / 题号 | 原自动结果 | 初筛 | 时间锚点 | 线索或输入错误 |",
              "| ---: | --- | --- | --- | --- | --- | --- |"]
    for r in records:
        reasons = list(r.get("pit_diagnostic_flags", [])) + list(r.get("pit_review_candidates", []))
        for key in ("financial_publish_guard", "cs_riskalert_effective_guard",
                    "cs_riskalert_information_guard", "snapshot_guard"):
            value = str(r.get(key) or "")
            if value == "missing" or value.startswith("present_"):
                reasons.append(f"{key}={value}")
        reasons += r.get("time_anchor_details", {}).get("issues", [])
        reasons += [f"coverage:{note}" for note in r.get("coverage_notes", [])]
        reasons += [r["input_error"]] if r.get("input_error") else []
        source = r.get("anchor_source", "")
        if source in {"missing", "snapshot_default", "instruction_year"}:
            reasons.append(f"anchor_source={source}")
        cells = (r["workbook_row"], r["case_id"], f"{r['model']} / {r['qid']}",
                 r["recorded_workbook_auto_outcome"], r["screening_status"],
                 r.get("time_anchor", ""), "; ".join(dict.fromkeys(reasons)))
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def run(sources: Path, out: Path) -> list[dict]:
    if out.exists():
        raise ValueError("Output directory already exists; choose a new directory")
    data = sources.read_bytes()
    rows = load_sources(data)
    provenance = {"source_manifest": str(sources.resolve()), "source_manifest_sha256": sha256(data),
                  "engine_sha256": sha256(ENGINE_PATH.read_bytes()),
                  "adapter_sha256": sha256(Path(__file__).read_bytes())}
    engine = load_engine()
    records = [dict(audit_case(row, engine), provenance=provenance) for row in rows]
    out.mkdir(parents=True, exist_ok=False)
    engine.write_jsonl(out / "audit.jsonl", records)
    (out / "report.md").write_text(render_report(records, provenance), encoding="utf-8")
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True, help="workbook instance source manifest JSONL")
    parser.add_argument("--out", type=Path, required=True, help="new output directory (never overwritten)")
    args = parser.parse_args(argv)
    try:
        records = run(args.sources, args.out)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps({"instances": len(records), "screening": dict(Counter(
        r["screening_status"] for r in records)), "output": str(args.out.resolve())}, ensure_ascii=False))
    return int(any(r["screening_status"] == "input_error" for r in records))


if __name__ == "__main__":
    sys.exit(main())
