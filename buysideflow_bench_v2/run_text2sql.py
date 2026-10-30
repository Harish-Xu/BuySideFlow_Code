"""
Text-to-SQL 基准测试启动脚本

用法:
    python run_text2sql.py                          # 跑默认 data/dataset
    python run_text2sql.py --slice :10              # 跑前10题
    python run_text2sql.py --slice :5 --model deepseek
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
    from text2sql_benchmark.benchmark_paths import load_benchmark_env, resolve_default_benchmark_path
except ImportError:
    from benchmark_paths import load_benchmark_env, resolve_default_benchmark_path

load_benchmark_env()

from sweagent.text2sql.defaults import (
    DEFAULT_BACKGROUND_PATH,
    DEFAULT_BOND_RULES_PATH,
    DEFAULT_FUND_RULES_PATH,
    TEXT2SQL_ASSETS_DIR,
)

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "sweagent" / "config" / "text2sql_default.yaml"
NO_PIT_CONFIG_PATH = PROJECT_ROOT / "sweagent" / "config" / "text2sql_no_pit.yaml"
NO_PIT_BACKGROUND_PATH = TEXT2SQL_ASSETS_DIR / "business_background_no_pit.txt"
NO_PIT_FUND_RULES_PATH = TEXT2SQL_ASSETS_DIR / "fund_business_rules_no_pit.txt"
NO_PIT_BOND_RULES_PATH = TEXT2SQL_ASSETS_DIR / "bond_business_rules_no_pit.txt"
LITELLM_MODEL_REGISTRY_PATH = PROJECT_ROOT / "sweagent" / "config" / "litellm_openai_compat_models.json"
DEEPSEEK_RESILIENCE_ARGS = (
    ("--agent.model.litellm_model_registry", str(LITELLM_MODEL_REGISTRY_PATH)),
    ("--agent.model.timeout", "300"),
    ("--agent.model.concurrency_limit", "4"),
    ("--agent.model.delay", "0.25"),
)
@dataclass(frozen=True)
class ModelPreset:
    resolved_name: str
    extra_configs: tuple[Path, ...] = ()
    extra_args: tuple[tuple[str, str], ...] = ()


def _resolve_model_preset(model_name: str) -> ModelPreset:
    normalized = model_name.strip()
    lowered = normalized.lower().removeprefix("openai/")

    if lowered == "deepseek":
        return ModelPreset(
            resolved_name="deepseek/deepseek-v4-flash",
            extra_args=DEEPSEEK_RESILIENCE_ARGS,
        )
    if lowered in {"deepseek-v4-flash", "deepseek/deepseek-v4-flash"}:
        return ModelPreset(
            resolved_name="deepseek/deepseek-v4-flash",
            extra_args=DEEPSEEK_RESILIENCE_ARGS,
        )
    if lowered in {"deepseek-v4-pro", "deepseek/deepseek-v4-pro"}:
        return ModelPreset(
            resolved_name="deepseek/deepseek-v4-pro",
            extra_args=DEEPSEEK_RESILIENCE_ARGS,
        )
    if lowered == "qwen" or lowered.startswith("qwen3.6-") or lowered.startswith("qwen3-max"):
        resolved_name = "qwen3.6-plus" if lowered == "qwen" else lowered
        return ModelPreset(
            resolved_name=resolved_name,
            extra_configs=(PROJECT_ROOT / "sweagent" / "config" / "text2sql_qwen_latest.yaml",),
            extra_args=(("--agent.model.litellm_model_registry", str(LITELLM_MODEL_REGISTRY_PATH)),),
        )
    if lowered == "kimi" or lowered.startswith("kimi-k2."):
        return ModelPreset(
            resolved_name="kimi-k2.6" if lowered == "kimi" else lowered,
            extra_configs=(PROJECT_ROOT / "sweagent" / "config" / "text2sql_kimi_latest.yaml",),
            extra_args=(("--agent.model.litellm_model_registry", str(LITELLM_MODEL_REGISTRY_PATH)),),
        )
    if lowered in {"gpt", "gpt5.5", "gpt-5.5"}:
        return ModelPreset(
            resolved_name="gpt-5.5",
        )
    if lowered in {"claude", "opus", "opus-4.7", "claude-opus", "claude-opus-4.7", "claude-opus-4-7"}:
        return ModelPreset(
            resolved_name="claude-opus-4-7",
            extra_args=(("--agent.model.api_base", "$ANTHROPIC_API_BASE"),),
        )
    if lowered in {"glm", "glm5.1", "glm-5.1", "zhipu", "zhipuai", "bigmodel"} or lowered.startswith("glm-5"):
        return ModelPreset(
            resolved_name="glm-5.1" if lowered in {"glm", "glm5.1", "zhipu", "zhipuai", "bigmodel"} else lowered,
            extra_configs=(PROJECT_ROOT / "sweagent" / "config" / "text2sql_glm_latest.yaml",),
            extra_args=(("--agent.model.litellm_model_registry", str(LITELLM_MODEL_REGISTRY_PATH)),),
        )
    if lowered in {"gemini", "gemini-pro", "gemini-3.1-pro-preview", "gemini/gemini-3.1-pro-preview"}:
        return ModelPreset(
            resolved_name="gemini/gemini-3.1-pro-preview",
            extra_configs=(PROJECT_ROOT / "sweagent" / "config" / "text2sql_gemini_latest.yaml",),
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

    parser = argparse.ArgumentParser(description="Text-to-SQL Benchmark Runner (SWE-agent)")
    parser.add_argument(
        "--data",
        "--markdown",
        dest="benchmark",
        default=str(resolve_default_benchmark_path()),
        help="Benchmark 数据目录；默认使用 data/dataset。",
    )
    parser.add_argument("--slice", default="", help="题目切片，如 :10 表示前10题，3:8 表示第4~8题")
    parser.add_argument("--filter", default=".*", help="正则过滤题目 id，如 'q1|q2|q3'")
    parser.add_argument(
        "--model",
        default="deepseek",
        help="模型名称或预设别名，支持 deepseek / qwen / kimi / gpt / claude / glm / gemini，也支持原始模型名",
    )
    parser.add_argument("--output", default="", help="输出目录，默认自动生成")
    parser.add_argument("--num_workers", type=int, default=8, help="并行题目数，默认8")
    parser.add_argument(
        "--prompt-variant",
        choices=("full", "no-pit"),
        default="full",
        help="提示版本：full 为正式配置；no-pit 仅移除显式 PIT 指导。",
    )
    args = parser.parse_args()
    model_preset = _resolve_model_preset(args.model)

    if args.prompt_variant == "no-pit":
        background_path = NO_PIT_BACKGROUND_PATH
        fund_rules_path = NO_PIT_FUND_RULES_PATH
        bond_rules_path = NO_PIT_BOND_RULES_PATH
    else:
        background_path = DEFAULT_BACKGROUND_PATH
        fund_rules_path = DEFAULT_FUND_RULES_PATH
        bond_rules_path = DEFAULT_BOND_RULES_PATH

    # 构造 run-batch 的命令行参数
    batch_args = ["--config", str(DEFAULT_CONFIG_PATH)]
    if args.prompt_variant == "no-pit":
        batch_args += ["--config", str(NO_PIT_CONFIG_PATH)]
    for config_path in model_preset.extra_configs:
        batch_args += ["--config", str(config_path)]
    batch_args += [
        "--instances.type", "text2sql",
        "--instances.path", args.benchmark,
        "--instances.business_metadata_path", str(background_path),
        "--instances.fund_rules_path", str(fund_rules_path),
        "--instances.bond_rules_path", str(bond_rules_path),
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
    elif args.prompt_variant == "no-pit":
        batch_args += ["--suffix", "no_pit_guidance"]

    print(f"=== Text-to-SQL Benchmark ===")
    print(f"Benchmark: {args.benchmark}")
    print(f"切片:     {args.slice or '全部'}")
    print(f"提示版本: {args.prompt_variant}")
    print(f"背景文件: {background_path}")
    print(f"基金规则: {fund_rules_path}")
    print(f"债券规则: {bond_rules_path}")
    if model_preset.resolved_name == args.model:
        print(f"模型:     {model_preset.resolved_name}")
    else:
        print(f"模型:     {args.model} -> {model_preset.resolved_name}")
    print(f"============================\n")

    from sweagent.run.run_batch import run_from_cli
    run_from_cli(batch_args)


if __name__ == "__main__":
    main()
