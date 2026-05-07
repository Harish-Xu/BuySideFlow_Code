"""
BuySideFlow ablation runner.

This variant removes the old prompt-injected preview path and instead exposes
an ablation-only tool, `reveal_reference_result`, to the agent.
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from buysideflow.benchmark_paths import load_benchmark_env, resolve_default_benchmark_path
except ImportError:
    try:
        from text2sql_benchmark.benchmark_paths import load_benchmark_env, resolve_default_benchmark_path
    except ImportError:
        from benchmark_paths import load_benchmark_env, resolve_default_benchmark_path

load_benchmark_env()

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "sweagent" / "config" / "text2sql_default.yaml"
ABLATION_CONFIG_PATH = PROJECT_ROOT / "sweagent" / "config" / "text2sql_ablation_reveal_tool.yaml"


def _resolve_model_preset(model_name: str):
    try:
        from buysideflow.run_text2sql import _resolve_model_preset as resolve_model_preset
    except ImportError:
        try:
            from text2sql_benchmark.run_text2sql import _resolve_model_preset as resolve_model_preset
        except ImportError:
            from run_text2sql import _resolve_model_preset as resolve_model_preset

    return resolve_model_preset(model_name)


def _patch_pexpect_on_windows() -> None:
    """Replace pexpect.spawn with subprocess-based implementation on Windows."""
    if sys.platform != "win32":
        return
    try:
        import pexpect
        from swerex.runtime._windows_spawn import WindowsSpawn

        pexpect.spawn = WindowsSpawn
        print("[INFO] Windows bash patch applied (subprocess-based)")
    except Exception as exc:
        print(f"[WARN] Windows pexpect compatibility patch failed: {exc}")


def main() -> None:
    _patch_pexpect_on_windows()

    parser = argparse.ArgumentParser(description="BuySideFlow ablation runner (Reveal Reference Result Tool)")
    parser.add_argument(
        "--markdown",
        default=str(resolve_default_benchmark_path()),
        help="Path to a new-format benchmark folder (question/results) or a legacy markdown task file.",
    )
    parser.add_argument("--data", dest="markdown", help="Alias for --markdown; accepts a new-format benchmark folder.")
    parser.add_argument("--slice", default="", help="Task slice, e.g. :10 or 3:8.")
    parser.add_argument("--filter", default=".*", help="Regex filter for task ids, e.g. q1|q2|q3.")
    parser.add_argument(
        "--model",
        default="deepseek/deepseek-chat",
        help="Model name or preset alias. Supports deepseek / qwen / kimi / raw model names.",
    )
    parser.add_argument("--output", default="", help="Output directory.")
    parser.add_argument("--num_workers", type=int, default=8, help="Parallel worker count.")
    args = parser.parse_args()

    model_preset = _resolve_model_preset(args.model)

    batch_args = [
        "--config",
        str(DEFAULT_CONFIG_PATH),
        "--config",
        str(ABLATION_CONFIG_PATH),
    ]
    for config_path in model_preset.extra_configs:
        batch_args += ["--config", str(config_path)]
    batch_args += [
        "--instances.type",
        "text2sql",
        "--instances.path",
        args.markdown,
        "--agent.model.name",
        model_preset.resolved_name,
        "--num_workers",
        str(args.num_workers),
    ]
    for key, value in model_preset.extra_args:
        batch_args += [key, value]
    if args.slice:
        batch_args += ["--instances.slice", args.slice]
    if args.filter != ".*":
        batch_args += ["--instances.filter", args.filter]
    if args.output:
        batch_args += ["--output_dir", args.output]
    else:
        batch_args += ["--suffix", "ablation_reveal_result"]

    os.environ.setdefault("TEXT2SQL_ABLATION_MAX_STEPS", "200")

    print("=== BuySideFlow Ablation (Reveal Reference Result Tool) ===")
    print(f"Tasks:      {args.markdown}")
    print(f"Slice:      {args.slice or 'all'}")
    if model_preset.resolved_name == args.model:
        print(f"Model:      {model_preset.resolved_name}")
    else:
        print(f"Model:      {args.model} -> {model_preset.resolved_name}")
    print(f"Step limit: {os.environ.get('TEXT2SQL_ABLATION_MAX_STEPS')}")
    print("===========================================================\n")

    from sweagent.run.run_batch import run_from_cli

    run_from_cli(batch_args)


if __name__ == "__main__":
    main()
