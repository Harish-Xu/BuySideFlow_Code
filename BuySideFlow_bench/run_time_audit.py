"""
Audit time anchoring for the folder-format BuySideFlow benchmark.

Usage:
    python run_time_audit.py --data data/dataset
    python run_time_audit.py --data data/dataset --output time_audit.md --jsonl time_audit.jsonl
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent


def _load_time_audit_module():
    module_path = PROJECT_ROOT / "sweagent" / "text2sql" / "time_audit.py"
    spec = importlib.util.spec_from_file_location("text2sql_time_audit", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load time_audit module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("text2sql_time_audit", module)
    spec.loader.exec_module(module)
    return module


def _numeric_suffix(value: str) -> int | None:
    matches = re.findall(r"\d+", value or "")
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def _result_dir_sort_key(path: Path) -> tuple[int, str]:
    number = _numeric_suffix(path.name)
    return (number if number is not None else 10**9, path.name.lower())


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _question_files(source_path: Path) -> list[Path]:
    if source_path.is_file() and source_path.suffix.lower() == ".jsonl":
        return [source_path]
    question_dir = source_path / "question"
    if not question_dir.exists():
        return []
    return sorted(question_dir.glob("*.jsonl"), key=lambda path: path.name.lower())


def _find_result_dir(results_dir: Path, qid: str, index: int) -> Path:
    exact = results_dir / qid
    if exact.is_dir():
        return exact
    qid_lower = qid.lower()
    for candidate in sorted((p for p in results_dir.iterdir() if p.is_dir()), key=_result_dir_sort_key):
        if candidate.name.lower() == qid_lower:
            return candidate
    qnum = _numeric_suffix(qid)
    if qnum is not None:
        for candidate in sorted((p for p in results_dir.iterdir() if p.is_dir()), key=_result_dir_sort_key):
            if _numeric_suffix(candidate.name) == qnum:
                return candidate
    ordered = sorted((p for p in results_dir.iterdir() if p.is_dir()), key=_result_dir_sort_key)
    return ordered[index] if 0 <= index < len(ordered) else results_dir / qid


def _load_folder_records(source_path: Path) -> list[dict[str, Any]]:
    root = source_path
    if source_path.is_file() and source_path.suffix.lower() == ".jsonl":
        root = source_path.parent.parent if source_path.parent.name == "question" else source_path.parent

    files = _question_files(source_path)
    if not files:
        raise ValueError(f"No question/*.jsonl files found under {source_path}")

    results_dir = root / "results"
    rows: list[dict[str, Any]] = []
    index = 0
    for question_file in files:
        for record in _read_jsonl_records(question_file):
            qid = str(record.get("instance_id") or record.get("id") or record.get("qid") or "").strip()
            question = str(record.get("instruction") or record.get("question") or record.get("query") or "").strip()
            if not qid or not question:
                index += 1
                continue
            rows.append(
                {
                    "id": qid,
                    "question": question,
                    "inputs": record.get("inputs") or {},
                    "result_dir": _find_result_dir(results_dir, qid, index),
                }
            )
            index += 1
    return rows


def _render_bool(value: bool) -> str:
    return "yes" if value else "no"


def _render_violations(record: dict[str, Any]) -> str:
    violations = record.get("runtime_date_violations") or []
    if not violations:
        return "ok"
    return ", ".join(f"{item.get('location')}:{item.get('token')}" for item in violations)


def _build_report(records: list[dict[str, Any]], *, data_path: Path, snapshot_default: str) -> str:
    source_counts = Counter(str(record.get("anchor_source") or "missing") for record in records)
    runtime_violations = [record for record in records if record.get("runtime_date_function") == "violation"]
    financial_missing = [record for record in records if record.get("financial_publish_guard") == "missing"]
    riskalert_publish_missing = [record for record in records if record.get("cs_riskalert_publish_guard") == "missing"]
    snapshot_missing = [record for record in records if record.get("snapshot_guard") == "missing"]
    future_labels = [record for record in records if record.get("future_label_allowed")]

    lines = [
        "# BuySideFlow Time Anchor Audit",
        f"- data: {data_path}",
        f"- snapshot_default: {snapshot_default or '(unset)'}",
        "",
        "## Summary",
        "| item | count |",
        "| --- | ---: |",
        f"| tasks | {len(records)} |",
        f"| runtime_date_function_violation | {len(runtime_violations)} |",
        f"| financial_publish_guard_missing | {len(financial_missing)} |",
        f"| cs_riskalert_publish_guard_missing | {len(riskalert_publish_missing)} |",
        f"| snapshot_guard_missing | {len(snapshot_missing)} |",
        f"| future_label_allowed | {len(future_labels)} |",
    ]
    for key, count in sorted(source_counts.items()):
        lines.append(f"| anchor_source:{key} | {count} |")

    lines.extend([
        "",
        "## Per Task",
        "| id | time_anchor | anchor_source | runtime_date_function | financial_publish_guard | cs_riskalert_publish_guard | snapshot_guard | future_label_allowed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for record in records:
        lines.append(
            "| {id} | {anchor} | {source} | {runtime} | {financial} | {riskalert} | {snapshot} | {future} |".format(
                id=record.get("id", ""),
                anchor=record.get("time_anchor") or "-",
                source=record.get("anchor_source") or "-",
                runtime=_render_violations(record),
                financial=record.get("financial_publish_guard") or "-",
                riskalert=record.get("cs_riskalert_publish_guard") or "-",
                snapshot=record.get("snapshot_guard") or "-",
                future=_render_bool(bool(record.get("future_label_allowed"))),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit time anchoring for BuySideFlow folder benchmarks.")
    parser.add_argument("--data", default=str(PROJECT_ROOT / "data" / "dataset"), help="Benchmark folder or question JSONL.")
    parser.add_argument("--output", default="", help="Markdown report path. Defaults to stdout only.")
    parser.add_argument("--jsonl", default="", help="Optional JSONL output path with per-task audit records.")
    parser.add_argument(
        "--snapshot-as-of-date",
        default=os.getenv("TEXT2SQL_SNAPSHOT_AS_OF_DATE", ""),
        help="Fallback anchor for tasks whose instruction intentionally uses latest/current snapshot semantics.",
    )
    parser.add_argument("--fail-on-violation", action="store_true", help="Exit non-zero on runtime date function violations.")
    args = parser.parse_args()

    audit_mod = _load_time_audit_module()
    data_path = Path(args.data)
    source_rows = _load_folder_records(data_path)
    records = [
        audit_mod.audit_folder_record(
            qid=row["id"],
            question=row["question"],
            inputs=row["inputs"],
            result_dir=row["result_dir"],
            snapshot_default=args.snapshot_as_of_date,
        )
        for row in source_rows
    ]

    report = _build_report(records, data_path=data_path, snapshot_default=args.snapshot_as_of_date)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    print(report)

    if args.jsonl:
        audit_mod.write_jsonl(args.jsonl, records)

    if args.fail_on_violation and any(record.get("runtime_date_function") == "violation" for record in records):
        sys.exit(1)


if __name__ == "__main__":
    main()
