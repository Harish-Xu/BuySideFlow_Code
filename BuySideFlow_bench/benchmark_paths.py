from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


BENCHMARK_ROOT = Path(__file__).resolve().parent
DATA_DIR = BENCHMARK_ROOT / "data"
BENCHMARKS_DIR = DATA_DIR / "benchmarks"
LEGACY_RESULTS_DIR = BENCHMARK_ROOT.parent / "text2sql-schema-filter-main_v8" / "results"
DEFAULT_BENCHMARK_NAME = "jq_2025_benchmark.md"
DEFAULT_FOLDER_BENCHMARK = DATA_DIR / "dataset"
LEGACY_FOLDER_BENCHMARK = DATA_DIR / "1"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def benchmark_search_dirs() -> list[Path]:
    return _dedupe_paths([BENCHMARKS_DIR, LEGACY_RESULTS_DIR])


def resolve_default_benchmark_path() -> Path:
    candidates = [
        DEFAULT_FOLDER_BENCHMARK,
        LEGACY_FOLDER_BENCHMARK,
        BENCHMARKS_DIR / DEFAULT_BENCHMARK_NAME,
        LEGACY_RESULTS_DIR / DEFAULT_BENCHMARK_NAME,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def guess_markdown_from_run_name(run_name: str) -> Path:
    markdowns: list[Path] = []
    for directory in benchmark_search_dirs():
        if not directory.exists():
            continue
        markdowns.extend(
            sorted(
                [*directory.glob("*.md"), *directory.glob("*.markdown")],
                key=lambda path: len(path.stem),
                reverse=True,
            )
        )
    for candidate in markdowns:
        if candidate.stem in run_name:
            return candidate
    return resolve_default_benchmark_path()


def _coerce_run_config_payload(raw_text: str) -> dict | None:
    payload: object = raw_text
    if yaml is not None:
        try:
            payload = yaml.safe_load(raw_text)
        except Exception:
            payload = raw_text
    for _ in range(2):
        if isinstance(payload, dict):
            return payload
        if not isinstance(payload, str):
            return None
        text = payload.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def resolve_benchmark_from_run_dir(run_dir: str | Path) -> Path | None:
    run_dir_path = Path(run_dir)
    config_path = run_dir_path / "run_batch.config.yaml"
    if not config_path.exists():
        return None

    payload = _coerce_run_config_payload(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None

    instances = payload.get("instances")
    if not isinstance(instances, dict):
        return None

    raw_path = instances.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (run_dir_path / candidate).resolve()
    return candidate if candidate.exists() else None


def load_benchmark_env() -> None:
    for path in [
        BENCHMARK_ROOT / ".env",
        BENCHMARK_ROOT / "sweagent" / ".env",
    ]:
        if path.exists():
            load_dotenv(path, override=False)

    # Keep cwd-based overrides working for users who launch from a different shell cwd.
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists() and cwd_env.parent != BENCHMARK_ROOT:
        load_dotenv(cwd_env, override=False)

    os.environ.setdefault("SWE_AGENT_CONFIG_ROOT", str(BENCHMARK_ROOT))
