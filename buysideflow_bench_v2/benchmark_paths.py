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
DEFAULT_FOLDER_BENCHMARK = DATA_DIR / "dataset"
RUN_CONFIG_NAME = "run_batch.config.yaml"


def resolve_default_benchmark_path() -> Path:
    return DEFAULT_FOLDER_BENCHMARK


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


def load_run_instances_config(run_dir: str | Path) -> dict | None:
    """Load the instances section recorded for a benchmark run."""
    config_path = Path(run_dir) / RUN_CONFIG_NAME
    if not config_path.exists():
        return None
    if not config_path.is_file():
        raise ValueError(f"Run config is not a file: {config_path}")

    payload = _coerce_run_config_payload(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Unable to parse run config: {config_path}")

    instances = payload.get("instances")
    if not isinstance(instances, dict):
        raise ValueError(f"Run config has no valid 'instances' section: {config_path}")
    return instances


def resolve_run_config_path(raw_path: str | Path) -> Path:
    """Resolve relative paths in saved run configs from the project root."""
    value = raw_path.strip() if isinstance(raw_path, str) else raw_path
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = BENCHMARK_ROOT / candidate
    return candidate.resolve()


def resolve_benchmark_from_run_dir(run_dir: str | Path) -> Path | None:
    run_dir_path = Path(run_dir)
    instances = load_run_instances_config(run_dir_path)
    if instances is None:
        return None

    raw_path = instances.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        config_path = run_dir_path / RUN_CONFIG_NAME
        raise ValueError(f"Run config has no valid 'instances.path': {config_path}")

    candidate = resolve_run_config_path(raw_path)
    if not candidate.exists():
        config_path = run_dir_path / RUN_CONFIG_NAME
        raise FileNotFoundError(
            f"Run config records benchmark source {raw_path!r}, but it does not exist at "
            f"{candidate}: {config_path}. Refusing to fall back to another benchmark."
        )
    return candidate


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

    _sync_env_aliases(
        ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        ("ZHIPU_API_KEY", "BIGMODEL_API_KEY", "GLM_API_KEY"),
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        ("DASHSCOPE_API_BASE", "QWEN_API_BASE"),
        ("MOONSHOT_API_BASE", "KIMI_API_BASE"),
        ("ZHIPU_API_BASE", "BIGMODEL_API_BASE", "GLM_API_BASE"),
        ("GEMINI_API_BASE", "GOOGLE_API_BASE"),
    )
    _set_env_default("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    _set_env_default("MOONSHOT_API_BASE", "https://api.moonshot.cn/v1")
    _set_env_default("ZHIPU_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
    _set_env_default("ANTHROPIC_API_BASE", "https://api.anthropic.com")

    os.environ.setdefault("SWE_AGENT_CONFIG_ROOT", str(BENCHMARK_ROOT))


def _sync_env_aliases(*groups: tuple[str, ...]) -> None:
    for group in groups:
        value = next((os.getenv(name) for name in group if os.getenv(name)), None)
        if not value:
            continue
        for name in group:
            if not os.getenv(name):
                os.environ[name] = value


def _set_env_default(name: str, value: str) -> None:
    if not os.getenv(name):
        os.environ[name] = value
