"""
Generate a lightweight, derived manifest for the folder-format BuySideFlow benchmark.

The manifest is not an evaluation source of truth. It is rebuilt from:
  - question/*.jsonl
  - results/<instance_id>/*
  - time-audit heuristics
  - optional manifest_overrides.json

Usage:
    python run_manifest.py --data data/dataset
    python run_manifest.py --data data/dataset --output data/dataset/manifest.generated.jsonl
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
PRIMARY_ARTIFACT_NAMES = {"refer.py", "refer.sql", "result.csv", "picture.png", "abstract.txt"}
SIDECAR_SUFFIXES = {
    ".json",
    ".jsonl",
    ".parquet",
    ".pkl",
    ".pickle",
    ".feather",
    ".npy",
    ".npz",
    ".xlsx",
    ".xls",
}
OVERRIDE_FIELDS = {"level", "as_of_date", "empty_result_reason", "future_label_allowed", "future_label_reason"}


def _load_time_audit_module():
    module_path = PROJECT_ROOT / "sweagent" / "text2sql" / "time_audit.py"
    spec = importlib.util.spec_from_file_location("text2sql_time_audit", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load time_audit module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("text2sql_time_audit", module)
    spec.loader.exec_module(module)
    return module


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _aggregate_file_hash(files: list[Path], *, base_path: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(files, key=lambda item: _rel_path(item, base_path)):
        h.update(_rel_path(path, base_path).encode("utf-8"))
        h.update(b"\0")
        h.update(_sha256_file(path).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def _rel_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _is_full_date(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()) is not None


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
            payload["_question_file"] = path
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


def _benchmark_root(source_path: Path) -> Path:
    if source_path.is_file() and source_path.parent.name == "question":
        return source_path.parent.parent
    if source_path.is_file():
        return source_path.parent
    return source_path


def _load_questions(source_path: Path) -> list[dict[str, Any]]:
    files = _question_files(source_path)
    if not files:
        raise ValueError(f"No question/*.jsonl files found under {source_path}")
    records: list[dict[str, Any]] = []
    for question_file in files:
        records.extend(_read_jsonl_records(question_file))
    return records


def _read_csv_header_and_rows(path: Path) -> tuple[list[str], int, int]:
    if not path.exists() or not path.is_file():
        return [], 0, 0
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                rows = list(csv.reader(file))
            header = [cell.strip() for cell in rows[0]] if rows else []
            return header, max(0, len(rows) - 1), len(header)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return [], 0, 0


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if path.name == "result.csv":
        return "csv"
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix == ".txt":
        return "text"
    if suffix == ".sql":
        return "sql"
    if suffix == ".py":
        return "python"
    return "artifact"


def _artifact_descriptor(path: Path, root: Path, *, kind: str | None = None) -> dict[str, Any]:
    return {
        "name": path.name,
        "path": _rel_path(path, root),
        "kind": kind or _artifact_kind(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _primary_artifacts(result_dir: Path) -> list[Path]:
    if not result_dir.exists() or not result_dir.is_dir():
        return []
    return [
        path
        for path in sorted(result_dir.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.name in PRIMARY_ARTIFACT_NAMES
    ]


def _sidecar_files(result_dir: Path) -> list[Path]:
    if not result_dir.exists() or not result_dir.is_dir():
        return []
    return [
        path
        for path in sorted(result_dir.iterdir(), key=lambda item: item.name.lower())
        if path.is_file()
        and not path.name.startswith(".")
        and path.name not in PRIMARY_ARTIFACT_NAMES
        and path.suffix.lower() in SIDECAR_SUFFIXES
    ]


def _evaluation_kind(primary_files: list[Path]) -> str:
    kinds = {_artifact_kind(path) for path in primary_files}
    if "image" in kinds:
        return "vision"
    if "text" in kinds and "csv" in kinds:
        return "mixed"
    if "text" in kinds:
        return "text_ai"
    if "csv" in kinds:
        return "csv"
    return "unknown"


def _mode(result_dir: Path) -> str:
    has_sql = (result_dir / "refer.sql").exists()
    has_py = (result_dir / "refer.py").exists()
    if has_sql and has_py:
        return "sql+python"
    if has_py:
        return "python"
    if has_sql:
        return "sql"
    return "artifact"


def _level_auto(*, mode: str, evaluation_kind: str, sql_code: str, python_code: str) -> str:
    combined = f"{sql_code}\n{python_code}".lower()
    if evaluation_kind in {"vision", "mixed", "text_ai"}:
        return "L4_multimodal_or_report"
    if mode == "python" or any(
        token in combined
        for token in (
            "sklearn",
            "scipy",
            "np.linalg",
            "polyfit",
            "ridge",
            "regression",
            "回归",
            "optimize",
            "portfolio",
            "backtest",
            "rolling",
        )
    ):
        return "L3_sql_python_analytics"
    if any(token in combined for token in (" over(", "row_number", "rank()", "dense_rank", " group by ", " having ", " with ")):
        return "L2_sql_aggregation"
    return "L1_retrieval"


def _load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest overrides must be a JSON object: {path}")
    raw_overrides = payload.get("overrides", payload)
    if not isinstance(raw_overrides, dict):
        raise ValueError(f"manifest overrides must contain an object field named 'overrides': {path}")
    overrides: dict[str, dict[str, Any]] = {}
    for qid, raw in raw_overrides.items():
        if str(qid).startswith("_"):
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"override for {qid} must be an object")
        unknown = sorted(set(raw) - OVERRIDE_FIELDS)
        if unknown:
            raise ValueError(f"unsupported override fields for {qid}: {', '.join(unknown)}")
        overrides[str(qid)] = dict(raw)
    return overrides


def _apply_overrides(record: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    applied: list[str] = []
    if "level" in override:
        record["level"] = str(override["level"])
        record["level_source"] = "override"
        applied.append("level")
    if "as_of_date" in override:
        record["as_of_date"] = str(override["as_of_date"])
        record["as_of_date_source"] = "override"
        record["needs_as_of_date_override"] = False
        applied.append("as_of_date")
    if "empty_result_reason" in override:
        record["empty_result_reason"] = str(override["empty_result_reason"])
        applied.append("empty_result_reason")
    if "future_label_allowed" in override:
        record["future_label_allowed"] = bool(override["future_label_allowed"])
        record["future_label_source"] = "override"
        applied.append("future_label_allowed")
    if "future_label_reason" in override:
        record["future_label_reason"] = str(override["future_label_reason"])
        record["needs_future_label_reason"] = False
        applied.append("future_label_reason")
    record["override_fields"] = applied
    return record


def _build_manifest_record(
    *,
    row: dict[str, Any],
    result_dir: Path,
    root: Path,
    time_audit: Any,
    snapshot_default: str,
    override: dict[str, Any],
) -> dict[str, Any]:
    qid = str(row.get("instance_id") or row.get("id") or row.get("qid") or "").strip()
    question = str(row.get("instruction") or row.get("question") or row.get("query") or "").strip()
    inputs = row.get("inputs") or {}
    primary = _primary_artifacts(result_dir)
    sidecars = _sidecar_files(result_dir)
    output_schema, result_row_count, result_col_count = _read_csv_header_and_rows(result_dir / "result.csv")
    sql_code = _read_text(result_dir / "refer.sql")
    python_code = _read_text(result_dir / "refer.py")
    mode = _mode(result_dir)
    evaluation_kind = _evaluation_kind(primary)
    audit = time_audit.audit_time_anchor_record(
        qid=qid,
        question=question,
        inputs=inputs,
        sql_code=sql_code,
        python_code=python_code,
        snapshot_default=snapshot_default,
    )
    level_auto = _level_auto(mode=mode, evaluation_kind=evaluation_kind, sql_code=sql_code, python_code=python_code)
    empty_result = bool(output_schema and result_row_count == 0)
    inferred_as_of_date = audit["time_anchor"] if _is_full_date(audit["time_anchor"]) else ""
    inferred_as_of_date_source = audit["anchor_source"] if inferred_as_of_date else "missing"
    future_label_allowed = bool(audit["future_label_allowed"])

    record: dict[str, Any] = {
        "id": qid,
        "domain": qid.split("_", 1)[0] if "_" in qid else "unknown",
        "question_file": _rel_path(Path(row.get("_question_file", "")), root) if row.get("_question_file") else "",
        "question_sha256": _sha256_bytes(question.encode("utf-8")),
        "result_dir": _rel_path(result_dir, root),
        "inputs": inputs,
        "mode": mode,
        "evaluation_kind": evaluation_kind,
        "level": level_auto,
        "level_auto": level_auto,
        "level_source": "auto",
        "time_anchor": audit["time_anchor"],
        "anchor_source": audit["anchor_source"],
        "as_of_date": inferred_as_of_date,
        "as_of_date_source": inferred_as_of_date_source,
        "needs_as_of_date_override": not bool(inferred_as_of_date),
        "runtime_date_function": audit["runtime_date_function"],
        "financial_publish_guard": audit["financial_publish_guard"],
        "snapshot_guard": audit["snapshot_guard"],
        "future_label_allowed": future_label_allowed,
        "future_label_source": "auto",
        "future_label_reason": "",
        "needs_future_label_reason": future_label_allowed,
        "output_schema": output_schema,
        "strict_output_schema": bool(output_schema),
        "result_row_count": result_row_count,
        "result_col_count": result_col_count,
        "empty_result": empty_result,
        "empty_result_reason": "",
        "needs_empty_result_reason": empty_result,
        "reference_artifacts": [_artifact_descriptor(path, root) for path in primary],
        "reference_sidecars": [_artifact_descriptor(path, root, kind="sidecar") for path in sidecars],
        "reference_hash": _aggregate_file_hash(primary, base_path=root) if primary else hashlib.sha256(b"").hexdigest(),
        "sidecar_hash": _aggregate_file_hash(sidecars, base_path=root) if sidecars else hashlib.sha256(b"").hexdigest(),
        "override_fields": [],
    }
    _apply_overrides(record, override)
    if record["empty_result_reason"]:
        record["needs_empty_result_reason"] = False
    if record["future_label_reason"]:
        record["needs_future_label_reason"] = False
    return record


def build_manifest(
    *,
    data_path: Path,
    overrides_path: Path,
    snapshot_default: str,
) -> list[dict[str, Any]]:
    root = _benchmark_root(data_path)
    results_dir = root / "results"
    rows = _load_questions(data_path)
    overrides = _load_overrides(overrides_path)
    time_audit = _load_time_audit_module()
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        qid = str(row.get("instance_id") or row.get("id") or row.get("qid") or "").strip()
        question = str(row.get("instruction") or row.get("question") or row.get("query") or "").strip()
        if not qid or not question:
            continue
        records.append(
            _build_manifest_record(
                row=row,
                result_dir=_find_result_dir(results_dir, qid, index),
                root=root,
                time_audit=time_audit,
                snapshot_default=snapshot_default,
                override=overrides.get(qid, {}),
            )
        )
    missing = sorted(set(overrides) - {record["id"] for record in records})
    if missing:
        raise ValueError(f"manifest overrides refer to unknown ids: {', '.join(missing[:20])}")
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )


def _summary(records: list[dict[str, Any]]) -> str:
    fields = {
        "domain": Counter(record["domain"] for record in records),
        "level": Counter(record["level"] for record in records),
        "mode": Counter(record["mode"] for record in records),
        "evaluation_kind": Counter(record["evaluation_kind"] for record in records),
    }
    lines = [f"Generated {len(records)} manifest records."]
    for name, counter in fields.items():
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(counter.items()))
        lines.append(f"{name}: {rendered}")
    review = [record["id"] for record in records if record.get("needs_empty_result_reason")]
    if review:
        lines.append("empty_result_reason needed: " + ", ".join(review))
    as_of_review = [record["id"] for record in records if record.get("needs_as_of_date_override")]
    if as_of_review:
        lines.append(f"full as_of_date not inferred / review needed: {len(as_of_review)} records")
    future_review = [record["id"] for record in records if record.get("needs_future_label_reason")]
    if future_review:
        lines.append("future_label_reason needed: " + ", ".join(future_review))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate derived manifest.generated.jsonl for BuySideFlow.")
    parser.add_argument("--data", default=str(PROJECT_ROOT / "data" / "dataset"), help="Benchmark folder or question JSONL.")
    parser.add_argument("--output", default="", help="Output JSONL path. Defaults to <data>/manifest.generated.jsonl.")
    parser.add_argument("--overrides", default="", help="Override JSON path. Defaults to <data>/manifest_overrides.json.")
    parser.add_argument(
        "--snapshot-as-of-date",
        default=os.getenv("TEXT2SQL_SNAPSHOT_AS_OF_DATE", ""),
        help="Fallback anchor for latest/current snapshot-style tasks.",
    )
    args = parser.parse_args()

    data_path = Path(args.data)
    root = _benchmark_root(data_path)
    output_path = Path(args.output) if args.output else root / "manifest.generated.jsonl"
    overrides_path = Path(args.overrides) if args.overrides else root / "manifest_overrides.json"
    records = build_manifest(data_path=data_path, overrides_path=overrides_path, snapshot_default=args.snapshot_as_of_date)
    write_jsonl(output_path, records)
    print(_summary(records))
    print(f"output: {output_path}")
    print(f"overrides: {overrides_path}")


if __name__ == "__main__":
    main()
