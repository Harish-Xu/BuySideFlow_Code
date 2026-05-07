"""
Lightweight sanity checks for the folder-format BuySideFlow dataset.

This script is not part of evaluation. It is a pre-release audit for obvious
reference/output anomalies and files that should not enter a public package.

Usage:
    python run_dataset_sanity.py --data data/dataset
    python run_dataset_sanity.py --data data/dataset --fail-on-publish-contaminants
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
PUBLISH_BLOCKED_DIR_NAMES = {".cache", "__pycache__", ".pytest_cache"}
PUBLISH_BLOCKED_SUFFIXES = {".pyc", ".pyo"}
PUBLISH_BLOCKED_FILE_NAMES = {".DS_Store"}
SUSPICIOUS_HEADER_PATTERNS = [
    r"\*\*",
    r"\n",
    r"一行",
    r"输出",
    r"列出",
    r"字段包括",
    r"如并列",
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _question_files(data_path: Path) -> list[Path]:
    if data_path.is_file() and data_path.suffix.lower() == ".jsonl":
        return [data_path]
    question_dir = data_path / "question"
    return sorted(question_dir.glob("*.jsonl")) if question_dir.exists() else []


def _task_ids(data_path: Path) -> list[str]:
    ids: list[str] = []
    for path in _question_files(data_path):
        for row in _read_jsonl(path):
            qid = str(row.get("instance_id") or row.get("id") or row.get("qid") or "").strip()
            if qid:
                ids.append(qid)
    return ids


def _read_csv(path: Path) -> tuple[list[list[str]] | None, str, str]:
    last_error = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                return list(csv.reader(file)), encoding, ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return None, "", last_error


def _blocked_publish_paths(results_dir: Path) -> list[Path]:
    blocked: list[Path] = []
    if not results_dir.exists():
        return blocked
    for path in results_dir.rglob("*"):
        parts = set(path.relative_to(results_dir).parts)
        if parts & PUBLISH_BLOCKED_DIR_NAMES:
            blocked.append(path)
            continue
        if path.name in PUBLISH_BLOCKED_FILE_NAMES or path.suffix.lower() in PUBLISH_BLOCKED_SUFFIXES:
            blocked.append(path)
    return sorted(set(blocked))


def _csv_checks(data_path: Path, qids: list[str], *, wide_threshold: int) -> tuple[list[str], list[str], list[tuple[int, int, int, str]]]:
    results_dir = data_path / "results"
    errors: list[str] = []
    warnings: list[str] = []
    shapes: list[tuple[int, int, int, str]] = []
    manifest_empty_reason: dict[str, bool] = {}
    manifest_path = data_path / "manifest.generated.jsonl"
    if manifest_path.exists():
        for row in _read_jsonl(manifest_path):
            qid = str(row.get("id") or "").strip()
            manifest_empty_reason[qid] = bool(row.get("empty_result_reason"))

    for qid in qids:
        result_dir = results_dir / qid
        csv_path = result_dir / "result.csv"
        if not csv_path.exists():
            errors.append(f"{qid}: missing result.csv")
            continue
        rows, encoding, error = _read_csv(csv_path)
        if rows is None:
            errors.append(f"{qid}: cannot read result.csv ({error})")
            continue
        if not rows:
            errors.append(f"{qid}: empty result.csv file")
            continue
        header = [cell.strip() for cell in rows[0]]
        data_rows = rows[1:]
        shapes.append((len(header), len(data_rows), csv_path.stat().st_size, qid))
        if not data_rows and not manifest_empty_reason.get(qid):
            errors.append(f"{qid}: zero data rows without empty_result_reason")
        blank_headers = [index + 1 for index, value in enumerate(header) if not value]
        if blank_headers:
            errors.append(f"{qid}: blank header positions {blank_headers}")
        duplicate_headers = [name for name, count in Counter(header).items() if name and count > 1]
        if duplicate_headers:
            errors.append(f"{qid}: duplicate headers {duplicate_headers}")
        ragged_rows = [index + 2 for index, row in enumerate(data_rows) if len(row) != len(header)]
        if ragged_rows:
            errors.append(f"{qid}: ragged rows {ragged_rows[:20]}")
        if len(header) >= wide_threshold:
            warnings.append(f"{qid}: wide CSV with {len(header)} columns")
        suspicious = [
            name
            for name in header
            if any(re.search(pattern, name) for pattern in SUSPICIOUS_HEADER_PATTERNS)
        ]
        if suspicious:
            warnings.append(f"{qid}: suspicious header text {suspicious[:5]}")
        if encoding != "utf-8-sig":
            warnings.append(f"{qid}: result.csv encoding read as {encoding}")
    return errors, warnings, shapes


def _render_top_shapes(shapes: list[tuple[int, int, int, str]]) -> list[str]:
    lines: list[str] = []
    for title, ordered in (
        ("widest_csv", sorted(shapes, reverse=True)[:10]),
        ("largest_row_count", sorted(shapes, key=lambda item: item[1], reverse=True)[:10]),
        ("largest_bytes", sorted(shapes, key=lambda item: item[2], reverse=True)[:10]),
    ):
        lines.append(f"\n## {title}")
        lines.append("| id | columns | rows | bytes |")
        lines.append("| --- | ---: | ---: | ---: |")
        for cols, rows, size, qid in ordered:
            lines.append(f"| {qid} | {cols} | {rows} | {size} |")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanity-check BuySideFlow dataset outputs before release.")
    parser.add_argument("--data", default=str(PROJECT_ROOT / "data" / "dataset"), help="Benchmark folder.")
    parser.add_argument("--wide-threshold", type=int, default=30, help="Warn when result.csv has at least this many columns.")
    parser.add_argument(
        "--fail-on-publish-contaminants",
        action="store_true",
        help="Exit non-zero when .cache, __pycache__, .pyc, or similar files are found under results/.",
    )
    parser.add_argument("--fail-on-errors", action="store_true", help="Exit non-zero on structural CSV errors.")
    args = parser.parse_args()

    data_path = Path(args.data)
    results_dir = data_path / "results"
    qids = _task_ids(data_path)
    result_dirs = sorted(path for path in results_dir.iterdir() if path.is_dir()) if results_dir.exists() else []
    non_task_dirs = [path for path in result_dirs if path.name not in set(qids)]
    blocked = _blocked_publish_paths(results_dir)
    blocked_bytes = sum(path.stat().st_size for path in blocked if path.is_file())
    errors, warnings, shapes = _csv_checks(data_path, qids, wide_threshold=args.wide_threshold)

    lines = [
        "# Dataset Sanity Report",
        f"- data: {data_path}",
        f"- tasks: {len(qids)}",
        f"- result_dirs: {len(result_dirs)}",
        f"- non_task_result_dirs: {len(non_task_dirs)}",
        f"- publish_contaminants: {len(blocked)}",
        f"- publish_contaminant_bytes: {blocked_bytes}",
        f"- csv_errors: {len(errors)}",
        f"- csv_warnings: {len(warnings)}",
        "",
    ]
    if non_task_dirs:
        lines.append("## Non-task result directories")
        for path in non_task_dirs[:100]:
            lines.append(f"- {path.relative_to(data_path)}")
        if len(non_task_dirs) > 100:
            lines.append(f"- ... {len(non_task_dirs) - 100} more")
        lines.append("")
    if blocked:
        lines.append("## Publish contaminants")
        for path in blocked[:100]:
            lines.append(f"- {path.relative_to(data_path)}")
        if len(blocked) > 100:
            lines.append(f"- ... {len(blocked) - 100} more")
        lines.append("")
    if errors:
        lines.append("## CSV errors")
        lines.extend(f"- {item}" for item in errors)
        lines.append("")
    if warnings:
        lines.append("## CSV warnings")
        lines.extend(f"- {item}" for item in warnings[:120])
        if len(warnings) > 120:
            lines.append(f"- ... {len(warnings) - 120} more")
        lines.append("")
    lines.extend(_render_top_shapes(shapes))
    print("\n".join(lines))

    failed = False
    if args.fail_on_publish_contaminants and blocked:
        failed = True
    if args.fail_on_errors and errors:
        failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
