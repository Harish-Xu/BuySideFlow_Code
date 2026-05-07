from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from sweagent.text2sql.defaults import (
    DEFAULT_BACKGROUND_PATH,
    DEFAULT_CATALOG_SCHEMA_PATH,
    DEFAULT_FUND_RULES_PATH,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SELECTION_GUIDANCE_PATH,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_REFERENCE_PRIMARY_FILENAMES = {"refer.py", "refer.sql", "result.csv", "picture.png", "abstract.txt"}
_REFERENCE_SIDECAR_SUFFIXES = {
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


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_existing_paths(paths: list[str | Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for raw in paths:
        if not raw:
            continue
        path = Path(raw)
        if not path.exists() or not path.is_file():
            continue
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        out.append(resolved)
    return out


def _path_label(path: Path, *, base_path: str | Path | None = None) -> str:
    resolved = path.resolve()
    if base_path is not None:
        try:
            return resolved.relative_to(Path(base_path).resolve()).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


def aggregate_sha256(paths: list[str | Path], *, base_path: str | Path | None = None) -> tuple[str, int]:
    files = _unique_existing_paths(paths)
    h = hashlib.sha256()
    for path in sorted(files, key=lambda item: _path_label(item, base_path=base_path)):
        h.update(_path_label(path, base_path=base_path).encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(path).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest(), len(files)


def _benchmark_hash_root(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    source = Path(path)
    if source.is_dir():
        return source.resolve()
    if source.is_file() and source.parent.name == "question":
        return source.parent.parent.resolve()
    if source.is_file():
        return source.parent.resolve()
    return None


def benchmark_source_files(path: str | Path) -> list[Path]:
    source = Path(path)
    if source.is_file():
        return [source.resolve()]
    if source.is_dir():
        question_dir = source / "question"
        if question_dir.exists():
            return [p.resolve() for p in sorted(question_dir.glob("*.jsonl"))]
        return [p.resolve() for p in sorted([*source.glob("*.md"), *source.glob("*.markdown"), *source.glob("*.jsonl")])]
    return []


def reference_files_from_tasks(tasks: list[dict[str, Any]]) -> list[Path]:
    paths: list[Path] = []
    for task in tasks:
        for raw in task.get("reference_artifact_paths") or []:
            if isinstance(raw, dict):
                value = raw.get("path") or raw.get("file") or raw.get("filename")
            else:
                value = raw
            if value:
                paths.append(Path(str(value)))
        for raw in task.get("reference_sidecar_paths") or []:
            if isinstance(raw, dict):
                value = raw.get("path") or raw.get("file") or raw.get("filename")
            else:
                value = raw
            if value:
                paths.append(Path(str(value)))
        result_dir = task.get("result_dir")
        if result_dir:
            for name in task.get("reference_links") or []:
                paths.append(Path(result_dir) / str(name))
            paths.extend(_result_dir_sidecar_paths(result_dir))
    return _unique_existing_paths(paths)


def _result_dir_sidecar_paths(result_dir: str | Path) -> list[Path]:
    directory = Path(result_dir)
    if not directory.exists() or not directory.is_dir():
        return []
    paths: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name.lower() in _REFERENCE_PRIMARY_FILENAMES:
            continue
        if path.suffix.lower() in _REFERENCE_SIDECAR_SUFFIXES:
            paths.append(path)
    return paths


def reference_sidecar_files_from_tasks(tasks: list[dict[str, Any]]) -> list[Path]:
    paths: list[Path] = []
    for task in tasks:
        for raw in task.get("reference_sidecar_paths") or []:
            if isinstance(raw, dict):
                value = raw.get("path") or raw.get("file") or raw.get("filename")
            else:
                value = raw
            if value:
                paths.append(Path(str(value)))
        result_dir = task.get("result_dir")
        if result_dir:
            paths.extend(_result_dir_sidecar_paths(result_dir))
    return _unique_existing_paths(paths)


def default_asset_paths() -> list[Path]:
    return [
        DEFAULT_SCHEMA_PATH,
        DEFAULT_CATALOG_SCHEMA_PATH,
        DEFAULT_BACKGROUND_PATH,
        DEFAULT_FUND_RULES_PATH,
        DEFAULT_SELECTION_GUIDANCE_PATH,
    ]


def judge_cache_dir(default: str | Path = "trajectories/text2sql_judge_cache") -> Path:
    return Path(os.getenv("TEXT2SQL_JUDGE_CACHE_DIR", str(default)))


def directory_files(path: str | Path) -> list[Path]:
    directory = Path(path)
    if not directory.exists() or not directory.is_dir():
        return []
    return [p.resolve() for p in sorted(directory.rglob("*")) if p.is_file() and not p.name.startswith(".")]


def require_db_snapshot_id() -> str:
    snapshot_id = os.getenv("DB_SNAPSHOT_ID", "").strip()
    if not snapshot_id:
        raise RuntimeError(
            "DB_SNAPSHOT_ID is required for paper-grade BuySideFlow evaluation provenance. "
            "Set DB_SNAPSHOT_ID to the immutable database snapshot/version used for this run."
        )
    return snapshot_id


def build_provenance_rows(
    *,
    benchmark_path: str | Path | None = None,
    benchmark_files: list[str | Path] | None = None,
    asset_paths: list[str | Path] | None = None,
    reference_paths: list[str | Path] | None = None,
    sidecar_paths: list[str | Path] | None = None,
    judge_cache_path: str | Path | None = None,
    require_db_snapshot: bool = True,
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    db_snapshot_id = require_db_snapshot_id() if require_db_snapshot else os.getenv("DB_SNAPSHOT_ID", "").strip()
    hash_root = _benchmark_hash_root(benchmark_path)

    source_files = list(benchmark_files or [])
    if benchmark_path is not None:
        source_files.extend(benchmark_source_files(benchmark_path))
    source_hash, source_count = aggregate_sha256(source_files, base_path=hash_root)
    rows.append(("benchmark_sources", f"{source_count} files", source_hash if source_count else _EMPTY_SHA256))

    assets = list(asset_paths or default_asset_paths())
    schema_hash, schema_count = aggregate_sha256(assets, base_path=_PROJECT_ROOT)
    rows.append(("schema_assets_aggregate", f"{schema_count} files", schema_hash if schema_count else _EMPTY_SHA256))
    for path in _unique_existing_paths(assets):
        rows.append((path.name, str(path), sha256_file(path)))

    sidecar_hash, sidecar_count = aggregate_sha256(sidecar_paths or [], base_path=hash_root)
    if sidecar_count:
        rows.append(("reference_sidecar_files", f"{sidecar_count} files", sidecar_hash))

    all_reference_paths = list(reference_paths or []) + list(sidecar_paths or [])
    ref_hash, ref_count = aggregate_sha256(all_reference_paths, base_path=hash_root)
    rows.append(("reference_files_aggregate", f"{ref_count} files", ref_hash if ref_count else _EMPTY_SHA256))

    cache_dir = Path(judge_cache_path) if judge_cache_path is not None else judge_cache_dir()
    cache_files = directory_files(cache_dir)
    cache_hash, cache_count = aggregate_sha256(cache_files, base_path=cache_dir)
    rows.append(("judge_cache_files", f"{cache_count} files", cache_hash if cache_count else _EMPTY_SHA256))
    rows.append(("DB_SNAPSHOT_ID", db_snapshot_id, db_snapshot_id))
    rows.append(("judge_cache_dir", str(cache_dir), cache_hash if cache_count else _EMPTY_SHA256))
    return rows


def render_provenance_markdown(rows: list[tuple[str, str, str]]) -> list[str]:
    lines = [
        "## Provenance",
        "| item | path/value | sha256 |",
        "| --- | --- | --- |",
    ]
    for item, path, digest in rows:
        lines.append(f"| {item} | {path or '-'} | {digest} |")
    return lines + [""]
