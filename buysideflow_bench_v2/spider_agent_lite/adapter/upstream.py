from __future__ import annotations

import importlib
import sys
from pathlib import Path


UPSTREAM_ROOT = Path(__file__).resolve().parents[1] / "upstream"


def ensure_upstream_importable() -> Path:
    """Put the pinned upstream package first on sys.path and reject collisions."""
    upstream = str(UPSTREAM_ROOT)
    if upstream in sys.path:
        sys.path.remove(upstream)
    sys.path.insert(0, upstream)

    loaded = sys.modules.get("spider_agent")
    if loaded is not None:
        candidates: list[Path] = []
        loaded_file = getattr(loaded, "__file__", None)
        if loaded_file:
            candidates.append(Path(loaded_file).resolve())
        for package_path in getattr(loaded, "__path__", []) or []:
            candidates.append(Path(package_path).resolve())
        if not any(
            path == UPSTREAM_ROOT.resolve() or UPSTREAM_ROOT.resolve() in path.parents
            for path in candidates
        ):
            rendered = ", ".join(str(path) for path in candidates) or "unknown location"
            raise RuntimeError(f"A different spider_agent package is already loaded: {rendered}")
    return UPSTREAM_ROOT


def load_upstream_prompt_agent():
    ensure_upstream_importable()
    try:
        module = importlib.import_module("spider_agent.agent.agents")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Spider-Agent-Lite runtime dependency is missing: "
            f"{exc.name}. Install spider_agent_lite/requirements-runtime.txt first."
        ) from exc
    return module.PromptAgent
