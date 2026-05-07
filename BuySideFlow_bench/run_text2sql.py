"""
BuySideFlow benchmark 启动脚本

用法:
    python run_text2sql.py                          # 跑默认 data/dataset
    python run_text2sql.py --slice :10              # 跑前10题
    python run_text2sql.py --slice :5 --model deepseek/deepseek-chat
    python run_text2sql.py --data data/dataset --slice 3:8
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# 确保项目根目录在 path 中
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
LITELLM_MODEL_REGISTRY_PATH = PROJECT_ROOT / "sweagent" / "config" / "litellm_openai_compat_models.json"


@dataclass(frozen=True)
class ModelPreset:
    resolved_name: str
    extra_configs: tuple[Path, ...] = ()
    extra_args: tuple[tuple[str, str], ...] = ()


def _resolve_model_preset(model_name: str) -> ModelPreset:
    normalized = model_name.strip()
    lowered = normalized.lower().removeprefix("openai/")

    if lowered == "deepseek":
        return ModelPreset(resolved_name="deepseek/deepseek-chat")
    if lowered == "deepseek/deepseek-chat":
        return ModelPreset(resolved_name="deepseek/deepseek-chat")
    if lowered == "qwen" or lowered.startswith("qwen3-max"):
        resolved_name = "qwen3-max" if lowered == "qwen" else lowered
        return ModelPreset(
            resolved_name=resolved_name,
            extra_configs=(PROJECT_ROOT / "sweagent" / "config" / "text2sql_qwen_latest.yaml",),
            extra_args=(("--agent.model.litellm_model_registry", str(LITELLM_MODEL_REGISTRY_PATH)),),
        )
    if lowered in {"kimi", "kimi-k2.5"}:
        return ModelPreset(
            resolved_name="kimi-k2.5",
            extra_configs=(PROJECT_ROOT / "sweagent" / "config" / "text2sql_kimi_latest.yaml",),
            extra_args=(("--agent.model.litellm_model_registry", str(LITELLM_MODEL_REGISTRY_PATH)),),
        )
    return ModelPreset(resolved_name=normalized)


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


def main():
    _patch_pexpect_on_windows()

    parser = argparse.ArgumentParser(description="BuySideFlow benchmark runner (SWE-agent)")
    parser.add_argument(
        "--data",
        dest="markdown",
        default=str(resolve_default_benchmark_path()),
        help="Alias for --markdown; accepts a new-format benchmark folder.",
    )
    parser.add_argument(
        "--markdown",
        default=str(resolve_default_benchmark_path()),
        help="题目 benchmark markdown 文件路径",
    )
    parser.add_argument("--slice", default="", help="题目切片，如 :10 表示前10题，3:8 表示第4~8题")
    parser.add_argument("--filter", default=".*", help="正则过滤题目 id，如 'q1|q2|q3'")
    parser.add_argument(
        "--model",
        default="deepseek/deepseek-chat",
        help="模型名称或预设别名，支持 deepseek / qwen / kimi，也支持原始模型名",
    )
    parser.add_argument("--output", default="", help="输出目录，默认自动生成")
    parser.add_argument("--num_workers", type=int, default=8, help="并行题目数，默认8")
    args = parser.parse_args()
    model_preset = _resolve_model_preset(args.model)

    # 构造 run-batch 的命令行参数
    batch_args = ["--config", str(DEFAULT_CONFIG_PATH)]
    for config_path in model_preset.extra_configs:
        batch_args += ["--config", str(config_path)]
    batch_args += [
        "--instances.type", "text2sql",
        "--instances.path", args.markdown,
        "--agent.model.name", model_preset.resolved_name,
        "--num_workers", str(args.num_workers),
    ]
    for key, value in model_preset.extra_args:
        batch_args += [key, value]
    if args.slice:
        batch_args += ["--instances.slice", args.slice]
    if args.filter != ".*":
        batch_args += ["--instances.filter", args.filter]
    if args.output:
        batch_args += ["--output_dir", args.output]

    print(f"=== BuySideFlow Benchmark ===")
    print(f"题目文件: {args.markdown}")
    print(f"切片:     {args.slice or '全部'}")
    if model_preset.resolved_name == args.model:
        print(f"模型:     {model_preset.resolved_name}")
    else:
        print(f"模型:     {args.model} -> {model_preset.resolved_name}")
    print(f"============================\n")

    from sweagent.run.run_batch import run_from_cli
    run_from_cli(batch_args)


if __name__ == "__main__":
    main()
