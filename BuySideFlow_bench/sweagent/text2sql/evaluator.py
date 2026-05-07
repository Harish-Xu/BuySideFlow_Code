from __future__ import annotations

import ast
import base64
import builtins
import hashlib
import io
import json
import math
import os
import keyword
import logging
import re
import sys
import tempfile
import types
import warnings
from contextlib import contextmanager, redirect_stdout
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sweagent.text2sql.time_audit import (
    format_runtime_date_lint_error,
    lint_runtime_date_functions,
    lint_sql_runtime_date_functions,
    pit_diagnostic_flags,
)

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _MODULE_DIR.parents[1]
_IMAGE_OUTPUT_DIR = Path.cwd() / "trajectories" / "text2sql_images"
_IMAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_JUDGE_CACHE_DIR = Path(os.getenv("TEXT2SQL_JUDGE_CACHE_DIR", str(Path.cwd() / "trajectories" / "text2sql_judge_cache")))
_JUDGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_SCALE_FACTORS = [100, 1e4, 1e6, 1e8, 1 / 100, 1 / 1e4, 1 / 1e6, 1 / 1e8]
_PERCENT_SCALE_FACTORS = {100, 1 / 100}
_DEFAULT_COMPARE_DECIMALS = 4
_REFERENCE_RESULT_ROOTS = [_PROJECT_ROOT / "data" / "dataset" / "results"]
_REFERENCE_FILE_NAMES = {"refer.py", "refer.sql", "result.csv", "picture.png", "abstract.txt"}


def _adjust_decimals_for_scale(decimals: int, scale: float) -> int:
    """Lower decimal precision when a unit/percent scale shifts decimal places."""
    if scale <= 0 or scale == 1.0:
        return decimals
    magnitude = int(round(abs(math.log10(scale))))
    return max(0, decimals - magnitude)


def _is_percent_scale(scale: float) -> bool:
    return scale in _PERCENT_SCALE_FACTORS


def _normalize_decimal_value(value: Any, *, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> str:
    try:
        number = Decimal(str(value))
        quant = Decimal("1").scaleb(-decimals)
        rounded = number.quantize(quant, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return _normalize_value(value, decimals=decimals)
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def _normalized_decimal_places(value: Any, *, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> int:
    text = _normalize_decimal_value(value, decimals=decimals).lower()
    if text in {"", "nan"} or "e" in text:
        return decimals
    if "." not in text:
        return 0
    return len(text.rsplit(".", 1)[1])


def _scaled_compare_decimals(
    gen_value: float,
    *,
    decimals: int = _DEFAULT_COMPARE_DECIMALS,
    scale: float,
) -> int:
    if decimals <= 0:
        return 0
    shifted_decimals = _adjust_decimals_for_scale(decimals, scale)
    gen_decimals = _normalized_decimal_places(gen_value, decimals=decimals)
    percent_floor = min(decimals, 2) if _is_percent_scale(scale) else 0
    return min(decimals, max(shifted_decimals, gen_decimals, percent_floor))


def _numeric_values_match(
    ref_value: float,
    gen_value: float,
    *,
    decimals: int = _DEFAULT_COMPARE_DECIMALS,
    scale: float = 1.0,
) -> bool:
    expected = ref_value * scale
    if scale == 1.0:
        return _normalize_value(expected, decimals=decimals) == _normalize_value(gen_value, decimals=decimals)
    if not _is_percent_scale(scale):
        return _normalize_value(expected, decimals=decimals) == _normalize_value(gen_value, decimals=decimals)

    scaled_decimals = _scaled_compare_decimals(gen_value, decimals=decimals, scale=scale)
    try:
        decimal_expected = Decimal(str(ref_value)) * Decimal(str(scale))
    except (InvalidOperation, ValueError):
        decimal_expected = expected
    if _normalize_decimal_value(decimal_expected, decimals=scaled_decimals) == _normalize_decimal_value(gen_value, decimals=scaled_decimals):
        return True
    return False


def _numeric_sequences_match(
    ref_values: Any,
    gen_values: Any,
    *,
    decimals: int = _DEFAULT_COMPARE_DECIMALS,
    scale: float = 1.0,
) -> bool:
    return all(
        _numeric_values_match(float(ref_value), float(gen_value), decimals=decimals, scale=scale)
        for ref_value, gen_value in zip(ref_values, gen_values, strict=True)
    )
_RESULT_NAME_RE = re.compile(r"^[A-Za-z_]\w*$")
_LITELLM_MODEL_REGISTRY = _PROJECT_ROOT / "sweagent" / "config" / "litellm_openai_compat_models.json"
_LITELLM_REGISTRY_LOADED = False
_LITELLM_FORCED_TEMPERATURES: dict[str, float] = {}


def _configure_matplotlib_for_evaluation() -> None:
    import matplotlib
    from matplotlib import font_manager, rcParams

    preferred_fonts = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "KaiTi",
        "NSimSun",
        "Arial Unicode MS",
    ]
    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    selected_fonts = [name for name in preferred_fonts if name in available_fonts]
    if selected_fonts:
        existing_fonts = list(rcParams.get("font.sans-serif", []))
        rcParams["font.sans-serif"] = selected_fonts + [name for name in existing_fonts if name not in selected_fonts]
        rcParams["font.family"] = ["sans-serif"]
    rcParams["axes.unicode_minus"] = False
    warnings.filterwarnings(
        "ignore",
        message=r"Glyph .* missing from font\(s\) .*",
        category=UserWarning,
    )


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if text.lower() in {"nan", "nat", "none", "null"}:
        return ""
    code_match = re.fullmatch(r"([A-Za-z0-9]{4,12})(?:\.([A-Za-z]{2,8}))?", text)
    if code_match:
        return code_match.group(1).upper()
    return re.sub(r"\s+", " ", text)


def _extract_compare_decimals(question: str) -> int:
    text = question or ""
    patterns = [
        r"保留\s*(\d+)\s*位小数",
        r"保留到\s*小数点后\s*(\d+)\s*位",
        r"小数点后\s*(\d+)\s*位",
        r"rounded?\s+to\s+(\d+)\s+decimal",
        r"(\d+)\s*decimal\s+places",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return max(0, int(match.group(1)))
            except ValueError:
                pass
    return _DEFAULT_COMPARE_DECIMALS


def _normalize_value(value: Any, *, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> str:
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    try:
        numeric = float(value)
        if np.isnan(numeric):
            return "nan"
        return str(round(numeric, decimals))
    except (TypeError, ValueError):
        pass
    return _normalized_text(value)


@contextmanager
def _prepend_sys_path(path: Path):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
        try:
            yield
        finally:
            try:
                sys.path.remove(path_str)
            except ValueError:
                pass
    else:
        yield


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _judge_cache_file(kind: str, payload: dict[str, Any]) -> Path:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return _JUDGE_CACHE_DIR / f"{kind}_{key}.json"


def _read_judge_cache(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    path = _judge_cache_file(kind, payload)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _write_judge_cache(kind: str, payload: dict[str, Any], value: dict[str, Any]) -> None:
    path = _judge_cache_file(kind, payload)
    try:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("failed to write judge cache %s: %s", path, exc)


def _is_read_mode(mode: Any) -> bool:
    text = str(mode or "r")
    return "r" in text or "+" in text or not any(flag in text for flag in ("w", "a", "x"))


def _is_reference_leak_path(path_like: Any) -> bool:
    try:
        path = Path(os.fspath(path_like))
    except (TypeError, ValueError):
        return False
    name = path.name.lower()
    try:
        resolved = path.expanduser().resolve(strict=False)
    except Exception:
        resolved = path

    resolved_text = str(resolved).replace("\\", "/").lower()
    if "/data/dataset/results/" in resolved_text:
        return True

    for root in _REFERENCE_RESULT_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return True
        except Exception:
            pass

    if name in _REFERENCE_FILE_NAMES:
        parts = [part.lower() for part in resolved.parts]
        if "results" in parts and "dataset" in parts:
            return True
    return False


@contextmanager
def _reference_leak_guard(enabled: bool):
    if not enabled:
        yield
        return

    original_open = builtins.open
    original_io_open = io.open
    original_path_open = Path.open
    original_path_read_text = Path.read_text
    original_path_read_bytes = Path.read_bytes
    pandas_readers = {
        name: getattr(pd, name)
        for name in ("read_csv", "read_excel", "read_json", "read_parquet", "read_pickle", "read_feather")
        if hasattr(pd, name)
    }

    def check_path(file: Any, mode: Any = "r") -> None:
        if _is_read_mode(mode) and _is_reference_leak_path(file):
            raise PermissionError(f"Reference leakage guard blocked reading benchmark reference file: {file}")

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        check_path(file, mode)
        return original_open(file, mode, *args, **kwargs)

    def guarded_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        check_path(file, mode)
        return original_io_open(file, mode, *args, **kwargs)

    def guarded_path_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any):
        check_path(self, mode)
        return original_path_open(self, mode, *args, **kwargs)

    def guarded_read_text(self: Path, *args: Any, **kwargs: Any):
        check_path(self, "r")
        return original_path_read_text(self, *args, **kwargs)

    def guarded_read_bytes(self: Path, *args: Any, **kwargs: Any):
        check_path(self, "rb")
        return original_path_read_bytes(self, *args, **kwargs)

    def make_pandas_reader(func):
        def wrapper(filepath_or_buffer: Any, *args: Any, **kwargs: Any):
            check_path(filepath_or_buffer, "r")
            return func(filepath_or_buffer, *args, **kwargs)
        return wrapper

    builtins.open = guarded_open
    io.open = guarded_io_open
    Path.open = guarded_path_open
    Path.read_text = guarded_read_text
    Path.read_bytes = guarded_read_bytes
    for name, func in pandas_readers.items():
        setattr(pd, name, make_pandas_reader(func))
    try:
        yield
    finally:
        builtins.open = original_open
        io.open = original_io_open
        Path.open = original_path_open
        Path.read_text = original_path_read_text
        Path.read_bytes = original_path_read_bytes
        for name, func in pandas_readers.items():
            setattr(pd, name, func)


def _default_leak_guard_for_label(label: str) -> bool:
    lowered = (label or "").lower()
    return "_reference" not in lowered and "reference" not in lowered


def _default_time_guard_for_label(label: str) -> bool:
    lowered = (label or "").lower()
    return "_reference" not in lowered and "reference" not in lowered


@contextmanager
def _runtime_date_query_guard(enabled: bool):
    if not enabled:
        yield
        return

    try:
        import sweagent.text2sql.db_connector as db_module
    except Exception:
        yield
        return

    original_query_to_dataframe = getattr(db_module, "query_to_dataframe", None)
    connector_cls = getattr(db_module, "_DbConnector", None)
    original_methods: dict[str, Any] = {}

    def check_sql(sql: Any, *, location: str) -> None:
        violations = lint_sql_runtime_date_functions(str(sql or ""), location=location)
        if violations:
            raise ValueError(format_runtime_date_lint_error(violations))

    if original_query_to_dataframe is not None:
        def guarded_query_to_dataframe(sql: str, *args: Any, **kwargs: Any):
            check_sql(sql, location="query_to_dataframe.sql")
            return original_query_to_dataframe(sql, *args, **kwargs)

        db_module.query_to_dataframe = guarded_query_to_dataframe

    if connector_cls is not None:
        for method_name in (
            "execute_sql_to_dataframe",
            "query",
            "execute",
            "get_dataframe",
            "fetchall",
        ):
            original = getattr(connector_cls, method_name, None)
            if original is None:
                continue
            original_methods[method_name] = original

            def make_guarded(func, name):
                def guarded(self, sql, *args: Any, **kwargs: Any):
                    check_sql(sql, location=f"db_connector.{name}.sql")
                    return func(self, sql, *args, **kwargs)
                return guarded

            setattr(connector_cls, method_name, make_guarded(original, method_name))

        for method_name in ("_exec_on_conn", "execute_query_on_connection", "execute_sql_to_dataframe_on_connection"):
            original = getattr(connector_cls, method_name, None)
            if original is None:
                continue
            original_methods[method_name] = original

            def make_conn_guarded(func, name):
                def guarded(self, conn_or_sql, sql_or_none=None, *args: Any, **kwargs: Any):
                    sql = conn_or_sql if isinstance(conn_or_sql, str) else (sql_or_none or "")
                    check_sql(sql, location=f"db_connector.{name}.sql")
                    return func(self, conn_or_sql, sql_or_none, *args, **kwargs)
                return guarded

            setattr(connector_cls, method_name, make_conn_guarded(original, method_name))

    try:
        yield
    finally:
        if original_query_to_dataframe is not None:
            db_module.query_to_dataframe = original_query_to_dataframe
        if connector_cls is not None:
            for method_name, original in original_methods.items():
                setattr(connector_cls, method_name, original)


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if "```" not in raw:
        return raw
    parts = raw.split("```")
    if len(parts) < 2:
        return raw
    fenced = parts[1].strip()
    if fenced.startswith("json"):
        fenced = fenced[4:].strip()
    return fenced


def _extract_json_from_text(raw: str) -> Any:
    text = _strip_code_fence(raw or "")
    try:
        return json.loads(text)
    except Exception:
        pass

    for left, right in (("[", "]"), ("{", "}")):
        start = text.find(left)
        end = text.rfind(right)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass
    return None


def _ensure_litellm_registry(litellm_module: Any) -> None:
    global _LITELLM_REGISTRY_LOADED
    if _LITELLM_REGISTRY_LOADED:
        return
    if _LITELLM_MODEL_REGISTRY.exists():
        try:
            litellm_module.register_model(json.loads(_LITELLM_MODEL_REGISTRY.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.debug("failed to register LiteLLM model registry: %s", exc)
    _LITELLM_REGISTRY_LOADED = True


def _litellm_completion_kwargs(model: str) -> dict[str, Any]:
    lowered = (model or "").lower()
    kwargs: dict[str, Any] = {}
    if "kimi" in lowered or "moonshot" in lowered:
        kwargs["api_base"] = os.getenv("MOONSHOT_API_BASE", "https://api.moonshot.cn/v1")
        api_key = os.getenv("MOONSHOT_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif "qwen" in lowered:
        kwargs["api_base"] = os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key
    elif "deepseek" in lowered:
        api_base = os.getenv("DEEPSEEK_API_BASE")
        if api_base:
            kwargs["api_base"] = api_base
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key
    return kwargs


def _litellm_model_key(model: str) -> str:
    return (model or "").strip().lower()


def _extract_fixed_temperature(exc: Exception) -> float | None:
    message = str(exc or "")
    match = re.search(
        r"invalid temperature:\s*only\s*([0-9]+(?:\.[0-9]+)?)\s*is allowed(?: for this model)?",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _evaluator_litellm_completion(
    litellm_module: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.0,
    **kwargs: Any,
) -> Any:
    model_key = _litellm_model_key(model)
    chosen_temperature = _LITELLM_FORCED_TEMPERATURES.get(model_key, temperature)
    try:
        return litellm_module.completion(
            model=model,
            messages=messages,
            temperature=chosen_temperature,
            **kwargs,
        )
    except Exception as exc:
        fixed_temperature = _extract_fixed_temperature(exc)
        if fixed_temperature is None or abs(fixed_temperature - chosen_temperature) < 1e-9:
            raise
        _LITELLM_FORCED_TEMPERATURES[model_key] = fixed_temperature
        logger.info(
            "retrying evaluator completion for model %s with fixed temperature %.3f",
            model,
            fixed_temperature,
        )
        return litellm_module.completion(
            model=model,
            messages=messages,
            temperature=fixed_temperature,
            **kwargs,
        )


def _sanitize_result_vars(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not _RESULT_NAME_RE.match(name):
            continue
        if name not in out:
            out.append(name)
    return out


def _extract_assigned_names_ast(code: str) -> list[str]:
    if not code.strip():
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    assigned: list[str] = []

    def add_target(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            assigned.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                add_target(item)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                add_target(target)
        elif isinstance(node, ast.AnnAssign):
            add_target(node.target)
        elif isinstance(node, ast.AugAssign):
            add_target(node.target)

    cleaned = [name for name in assigned if not name.startswith("_") and not name.isupper()]
    if not cleaned:
        return []

    preferred = [
        name for name in cleaned
        if any(token in name.lower() for token in ("result", "output", "answer", "final", "summary", "table", "df"))
    ]
    if preferred:
        return preferred[-3:]
    return cleaned[-2:]


def get_result_plan_ai(
    question: str,
    code: str,
    *,
    expected_count: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    if not code or not code.strip():
        return {"result_vars": [], "inject_code": "", "needs_ai_judge": False}

    import os

    try:
        import litellm
    except ImportError:
        return {"result_vars": [], "inject_code": "", "needs_ai_judge": None}
    _ensure_litellm_registry(litellm)

    if model is None:
        model = os.getenv("VAR_EXTRACT_MODEL", "deepseek/deepseek-chat")

    count_hint = (
        f"The other side currently has {expected_count} final result object(s). "
        f"If possible, expose exactly {expected_count} final result object(s) in the same semantic order.\n"
        if expected_count and expected_count > 0
        else ""
    )

    prompt = f"""You are extracting final evaluation outputs from Python code.

Question:
{question}

Python code:
{code}

Return strict JSON only:
{{
  "result_vars": ["var1", "var2"],
  "inject_code": "...",
  "needs_ai_judge": false
}}

Rules:
- result_vars must be final output variable names in question order.
- If the outputs are already available as global variables, keep inject_code as an empty string.
- If the outputs are only available through print(), function returns, or other hard-to-extract paths, write minimal append-only inject_code that creates explicit global variables with meaningful snake_case names, such as coverage_summary, candidate_pool_table, observation_pool_table.
- When inject_code is non-empty, result_vars should list those explicit variables in order.
- Prefer meaningful variable names over generic numbered names, because they may be used later as labels or column names.
- Prefer splitting the final answer into multiple explicit result variables when that helps align with the expected output structure.
- If you cannot split safely, assign the whole final object to one meaningful variable such as final_result.
- Categorize the output into exactly one of three types:
  1. Contains images/charts/plots/figures → needs_ai_judge=true
  2. Contains text descriptions, analysis, explanations, conclusions, or narrative (even if mixed with tables) → needs_ai_judge=true
  3. Pure structured data (DataFrames, Series, arrays, dicts, lists of numbers) with NO images and NO text descriptions → needs_ai_judge=false
- Mixed outputs (tables + text/images) always go to AI judge because text cannot be structurally compared.
- Only pure numeric/tabular outputs should use strict structural comparison.
- Prefer reusing variables that already exist after the script runs.
- Avoid re-running expensive workflows unless there is no other way.
- If you must call a function, call it once and store its return value.
- Do not include markdown fences or explanations.
- Do not invent variables that do not exist after execution.
- If you cannot determine anything safely, return {{"result_vars": [], "inject_code": "", "needs_ai_judge": false}}.

{count_hint}"""

    last_exc: Exception | None = None
    for _ in range(3):
        try:
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                **_litellm_completion_kwargs(model),
            )
            raw = _strip_code_fence((response.choices[0].message.content or "").strip())
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {"result_vars": _sanitize_result_vars(parsed), "inject_code": "", "needs_ai_judge": False}
            if isinstance(parsed, dict):
                return {
                    "result_vars": _sanitize_result_vars(parsed.get("result_vars")),
                    "inject_code": str(parsed.get("inject_code") or ""),
                    "needs_ai_judge": bool(parsed.get("needs_ai_judge", False)),
                }
        except Exception as exc:
            last_exc = exc

    if last_exc is not None:
        logger.warning("get_result_plan_ai failed, fallback to local extraction: %s", last_exc)

    return {"result_vars": [], "inject_code": "", "needs_ai_judge": None}


def get_result_vars_ai(question: str, code: str, model: str | None = None) -> list[str]:
    plan = get_result_plan_ai(question, code, model=model)
    if plan["result_vars"]:
        return plan["result_vars"]
    return _extract_assigned_names_ast(code)


def _unwrap_json_dumps_arg(expr: ast.AST) -> ast.AST:
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and isinstance(expr.func.value, ast.Name)
        and expr.func.value.id == "json"
        and expr.func.attr == "dumps"
        and expr.args
    ):
        return expr.args[0]
    return expr


def _extract_expr_source(code: str, expr: ast.AST) -> str:
    source = ast.get_source_segment(code, expr) or ""
    return source.strip()


def _make_safe_identifier(text: str, fallback: str = "result") -> str:
    name = re.sub(r"[^0-9A-Za-z_]+", "_", text).strip("_").lower()
    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"{fallback}_{name}"
    if keyword.iskeyword(name):
        name = f"{name}_value"
    return name


def _guess_result_name_from_expr(expr_source: str) -> str:
    expr_source = expr_source.strip()
    call_match = re.match(r"([A-Za-z_]\w*)\s*\(", expr_source)
    if call_match:
        return _make_safe_identifier(f"{call_match.group(1)}_result", fallback="final_result")
    attr_match = re.match(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(", expr_source)
    if attr_match:
        return _make_safe_identifier(f"{attr_match.group(2)}_result", fallback="final_result")
    return "final_result"


def _build_single_result_plan(expr_source: str) -> dict[str, Any]:
    result_name = _guess_result_name_from_expr(expr_source)
    return {"result_vars": [result_name], "inject_code": f"{result_name} = {expr_source}", "needs_ai_judge": False}


def _guess_needs_ai_judge(question: str, code: str) -> bool:
    question_text = (question or "").lower()
    code_text = (code or "").lower()
    if any(keyword in question_text for keyword in ["图片", "图表", "图形", "绘图", "画图", "plot", "chart", "figure"]):
        return True
    if any(keyword in question_text for keyword in ["说明", "描述", "文字", "分析", "解读", "原因", "评价", "判断", "研究", "专题研究", "结论"]):
        return True
    if _has_plot_code(code):
        return True
    if any(token in code_text for token in ["plt.", "matplotlib", " seaborn", "analysis", "summary", "explain"]):
        return True
    return False


def _guess_inject_plan_locally(question: str, code: str) -> dict[str, Any]:
    if not code.strip():
        return {"result_vars": [], "inject_code": "", "needs_ai_judge": _guess_needs_ai_judge(question, code)}

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"result_vars": [], "inject_code": "", "needs_ai_judge": _guess_needs_ai_judge(question, code)}

    needs_ai_judge = _guess_needs_ai_judge(question, code)

    main_func: ast.FunctionDef | None = None
    top_level_print_exprs: list[ast.AST] = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "print" and call.args:
                top_level_print_exprs.append(_unwrap_json_dumps_arg(call.args[0]))
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "result_vars":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        names: list[str] = []
                        for item in node.value.elts:
                            if isinstance(item, ast.Name):
                                names.append(item.id)
                        if names:
                            return {"result_vars": names, "inject_code": "", "needs_ai_judge": needs_ai_judge}

    if main_func is not None:
        for sub in ast.walk(main_func):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "print" and sub.args:
                expr = _unwrap_json_dumps_arg(sub.args[0])
                if isinstance(expr, ast.Name):
                    return {"result_vars": [expr.id], "inject_code": "", "needs_ai_judge": needs_ai_judge}
                expr_source = _extract_expr_source(code, expr)
                if expr_source:
                    plan = _build_single_result_plan(expr_source)
                    plan["needs_ai_judge"] = needs_ai_judge
                    return plan

    for expr in top_level_print_exprs:
        if isinstance(expr, ast.Name):
            return {"result_vars": [expr.id], "inject_code": "", "needs_ai_judge": needs_ai_judge}
        expr_source = _extract_expr_source(code, expr)
        if expr_source:
            plan = _build_single_result_plan(expr_source)
            plan["needs_ai_judge"] = needs_ai_judge
            return plan

    ast_names = _extract_assigned_names_ast(code)
    if ast_names:
        return {"result_vars": ast_names, "inject_code": "", "needs_ai_judge": needs_ai_judge}

    return {"result_vars": [], "inject_code": "", "needs_ai_judge": needs_ai_judge}


def _get_result_plan(
    question: str,
    code: str,
    *,
    expected_count: int | None = None,
) -> dict[str, Any]:
    plan = get_result_plan_ai(question, code, expected_count=expected_count)
    result_vars = _sanitize_result_vars(plan.get("result_vars"))
    inject_code = str(plan.get("inject_code") or "").strip()
    needs_ai_judge = plan.get("needs_ai_judge")
    if result_vars == ["_eval_results"] and inject_code:
        inject_code += "\nif '_eval_results' in globals() and isinstance(_eval_results, (list, tuple)):\n"
        inject_code += "    _tmp_eval_results = list(_eval_results)\n"
        inject_code += "    result_part_0 = _tmp_eval_results[0] if len(_tmp_eval_results) > 0 else None\n"
        inject_code += "    result_part_1 = _tmp_eval_results[1] if len(_tmp_eval_results) > 1 else None\n"
        inject_code += "    result_part_2 = _tmp_eval_results[2] if len(_tmp_eval_results) > 2 else None\n"
        inject_code += "    result_part_3 = _tmp_eval_results[3] if len(_tmp_eval_results) > 3 else None\n"
        result_vars = [name for name in ["result_part_0", "result_part_1", "result_part_2", "result_part_3"]]
    if result_vars or inject_code:
        return {"result_vars": result_vars, "inject_code": inject_code, "needs_ai_judge": needs_ai_judge}
    return _guess_inject_plan_locally(question, code)


def _extract_ai_judge_response(raw_text: str) -> tuple[bool | None, str]:
    text = (raw_text or "").strip()
    lowered = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = lines[0].lower() if lines else ""

    if first.startswith("yes"):
        reason = "\n".join(lines[1:]).strip()
        return True, reason
    if first.startswith("no"):
        reason = "\n".join(lines[1:]).strip()
        return False, reason

    if lowered == "yes":
        return True, ""
    if lowered == "no":
        return False, ""
    if "yes" in lowered and "no" not in lowered:
        return True, ""
    if "no" in lowered:
        return False, ""
    return None, text


def _compare_images_with_ai(img1: bytes, img2: bytes) -> tuple[bool | None, str]:
    import base64
    import os

    model = os.getenv("VISION_JUDGE_MODEL") or os.getenv("VISION_MODEL", "kimi-k2.5")
    prompt = (
        "The first image is the reference output and the second image is the generated output. "
        "Judge whether they express the same underlying data/content. Ignore styling differences. "
        "Reply in exactly two lines:\n"
        "Line 1: yes or no\n"
        "Line 2: a brief reason in Chinese."
    )
    cache_payload = {
        "model": model,
        "prompt": prompt,
        "ref_image_sha256": _sha256_bytes(img1),
        "gen_image_sha256": _sha256_bytes(img2),
    }
    cached = _read_judge_cache("image_compare", cache_payload)
    if cached is not None:
        parsed = cached.get("parsed")
        if isinstance(parsed, bool):
            return parsed, str(cached.get("reason") or "")

    try:
        import litellm
    except ImportError:
        return None, ""
    _ensure_litellm_registry(litellm)

    def to_b64(value: bytes) -> str:
        return base64.b64encode(value).decode()
    try:
        response = _evaluator_litellm_completion(
            litellm,
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{to_b64(img1)}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{to_b64(img2)}"}},
                ],
            }],
            temperature=0.0,
            **_litellm_completion_kwargs(model),
        )
        answer = response.choices[0].message.content or ""
        parsed, reason = _extract_ai_judge_response(answer)
        if parsed is not None:
            _write_judge_cache("image_compare", cache_payload, {"raw": answer, "parsed": parsed, "reason": reason})
            return parsed, reason
    except Exception as exc:
        logger.warning("image comparison failed: %s", exc)
    return None, ""


def _compare_text_with_ai(
    ref_text: str,
    gen_text: str,
    question: str,
    model: str | None = None,
) -> tuple[bool | None, str]:
    import os

    try:
        import litellm
    except ImportError:
        return None, ""
    _ensure_litellm_registry(litellm)

    if model is None:
        model = os.getenv("TEXT_JUDGE_MODEL", "deepseek/deepseek-v3")

    prompt = f"""Judge whether the reference output and the generated output are semantically equivalent.
Ignore formatting differences, variable grouping differences, table splitting/merging differences, and column-name differences.
Focus on whether the underlying data or textual conclusion matches.

Question:
{question}

Reference output:
{ref_text}

Generated output:
{gen_text}

Reply in exactly two lines:
Line 1: yes or no
Line 2: a brief reason in Chinese."""

    cache_payload = {
        "model": model,
        "prompt": prompt,
        "ref_text_sha256": hashlib.sha256(ref_text.encode("utf-8", errors="replace")).hexdigest(),
        "gen_text_sha256": hashlib.sha256(gen_text.encode("utf-8", errors="replace")).hexdigest(),
    }
    cached = _read_judge_cache("text_compare", cache_payload)
    if cached is not None:
        parsed = cached.get("parsed")
        if isinstance(parsed, bool):
            return parsed, str(cached.get("reason") or "")

    try:
        response = _evaluator_litellm_completion(
            litellm,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            **_litellm_completion_kwargs(model),
        )
        answer = response.choices[0].message.content or ""
        parsed, reason = _extract_ai_judge_response(answer)
        if parsed is not None:
            _write_judge_cache("text_compare", cache_payload, {"raw": answer, "parsed": parsed, "reason": reason})
            return parsed, reason
    except Exception as exc:
        logger.warning("text comparison failed: %s", exc)
    return None, ""


def _normalize_column_name(value: Any) -> str:
    text = _normalized_text(value).lstrip("\ufeff")
    return text.lower() if text.isascii() else text


def _df_schema_matches(ref_df: pd.DataFrame, gen_df: pd.DataFrame) -> bool:
    if len(ref_df.columns) != len(gen_df.columns):
        return False
    ref_columns = [_normalize_column_name(column) for column in ref_df.columns]
    gen_columns = [_normalize_column_name(column) for column in gen_df.columns]
    return ref_columns == gen_columns


def _normalize_df(
    df: pd.DataFrame,
    *,
    sort_rows: bool = True,
    decimals: int = _DEFAULT_COMPARE_DECIMALS,
    sort_columns: bool = True,
) -> pd.DataFrame:
    df = df.copy()
    for col in df.select_dtypes(include="number").columns:
        df[col] = df[col].round(decimals)
    if sort_columns:
        ordered_columns = sorted(
            df.columns,
            key=lambda column: (tuple(_normalized_text(value) for value in df[column].tolist()), str(column)),
        )
        df = df[ordered_columns]
    df.columns = list(range(len(df.columns)))
    if sort_rows:
        row_keys = [tuple(_normalized_text(v) for v in row) for row in df.itertuples(index=False, name=None)]
        order = sorted(range(len(row_keys)), key=lambda index: row_keys[index])
        df = df.iloc[order].reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)
    return df


def _question_has_order(question: str) -> bool:
    keywords = [
        "\u5347\u5e8f", "\u964d\u5e8f", "\u4ece\u5c0f\u5230\u5927", "\u4ece\u5927\u5230\u5c0f",
        "\u7531\u5c0f\u5230\u5927", "\u7531\u5927\u5230\u5c0f", "\u4ece\u9ad8\u5230\u4f4e", "\u4ece\u4f4e\u5230\u9ad8",
        "\u7531\u9ad8\u5230\u4f4e", "\u7531\u4f4e\u5230\u9ad8", "ascending", "descending",
    ]
    text = question or ""
    return any(keyword in text for keyword in keywords)


def _is_scalar_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, bytes, int, float, bool, complex, pd.Timestamp, np.generic)):
        return True
    return False


def _looks_like_record_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(item, dict) for item in value)


def _to_df(value: Any) -> pd.DataFrame | None:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, pd.Series):
        return value.to_frame()
    if isinstance(value, np.ndarray):
        return pd.DataFrame(value)

    if isinstance(value, dict):
        if not value:
            return pd.DataFrame([{}])
        values = list(value.values())
        if len(value) == 1:
            inner = values[0]
            if isinstance(inner, pd.DataFrame):
                return inner.copy()
            if isinstance(inner, pd.Series):
                return inner.to_frame()
            if isinstance(inner, np.ndarray):
                return pd.DataFrame(inner)
            if _looks_like_record_list(inner):
                return pd.DataFrame(inner)
            if isinstance(inner, (list, tuple)) and all(_is_scalar_like(item) for item in inner):
                return pd.DataFrame(inner)
        if all(_is_scalar_like(item) for item in values):
            return pd.DataFrame([value])
        return None

    if isinstance(value, (list, tuple)):
        if not value:
            return pd.DataFrame()
        if _looks_like_record_list(value):
            return pd.DataFrame(value)
        if all(_is_scalar_like(item) for item in value):
            return pd.DataFrame(value)
        if all(isinstance(item, (list, tuple, np.ndarray)) for item in value):
            try:
                return pd.DataFrame(value)
            except Exception:
                return None
        return None

    if _is_scalar_like(value):
        return pd.DataFrame([[value]])
    return None


def _has_plot_code(code: str) -> bool:
    patterns = [r"plt\.savefig", r"plt\.show", r"import matplotlib", r"\.plot\(", r"\.bar\(", r"\.hist\("]
    return any(re.search(pattern, code or "") for pattern in patterns)


def _capture_figure(label: str) -> bytes | None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    if not plt.get_fignums():
        return None
    buffer = io.BytesIO()
    plt.savefig(buffer, format="png", bbox_inches="tight")
    plt.close("all")
    buffer.seek(0)
    data = buffer.read()
    (_IMAGE_OUTPUT_DIR / f"{label}.png").write_bytes(data)
    return data


def _install_db_aliases() -> None:
    try:
        import sweagent.text2sql.db_connector as db_module
    except Exception:
        return

    sys.modules.setdefault("db_connector", db_module)

    src_module = sys.modules.get("src")
    if src_module is None:
        src_module = types.ModuleType("src")
        sys.modules["src"] = src_module
    setattr(src_module, "db_connector", db_module)
    sys.modules.setdefault("src.db_connector", db_module)


def _heuristic_result_vars(exec_globals: dict[str, Any]) -> list[str]:
    skip_names = {
        "__name__", "__file__", "__builtins__", "pd", "np", "json", "Path", "tempfile",
        "re", "sql_code", "io", "sys", "types",
    }
    scored: list[tuple[int, int, str]] = []

    for index, (name, obj) in enumerate(exec_globals.items()):
        if name in skip_names or name.startswith("_"):
            continue
        if callable(obj) or isinstance(obj, type):
            continue
        if isinstance(obj, types.ModuleType):
            continue

        score = 0
        if isinstance(obj, pd.DataFrame):
            score += 8
        elif isinstance(obj, (pd.Series, np.ndarray)):
            score += 7
        elif isinstance(obj, dict):
            score += 6
        elif isinstance(obj, (list, tuple)):
            score += 5
        elif _is_scalar_like(obj):
            score += 2

        lowered = name.lower()
        if any(token in lowered for token in ("result", "output", "answer", "final", "summary", "table", "df")):
            score += 4
        if name.isupper():
            score -= 3

        if score > 0:
            scored.append((score, index, name))

    scored.sort(key=lambda item: (item[0], item[1]))
    return [name for _, _, name in scored[-3:]]


class ExecuteResult:
    def __init__(self) -> None:
        self.success = False
        self.results: list[Any] = []
        self.raw_results: list[Any] = []
        self.result_types: list[str] = []
        self.error: str | None = None
        self.has_image = False
        self.stdout_text = ""
        self.printed_objects: list[Any] = []
        self.missing_vars: list[str] = []
        self.exec_globals: dict[str, Any] = {}


def _artifact_kind_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix == ".txt":
        return "text"
    return ""


def _normalize_artifact_descriptor(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, str):
        path = Path(raw)
        kind = _artifact_kind_from_path(path)
        if kind:
            return {"kind": kind, "path": raw, "name": path.name}
        return None
    if not isinstance(raw, dict):
        return None
    path_value = raw.get("path") or raw.get("file") or raw.get("filename")
    if not path_value:
        return None
    path = Path(str(path_value))
    kind = str(raw.get("kind") or raw.get("type") or _artifact_kind_from_path(path)).lower()
    if kind in {"png", "jpg", "jpeg", "webp"}:
        kind = "image"
    if kind not in {"csv", "image", "text"}:
        return None
    return {"kind": kind, "path": str(path_value), "name": str(raw.get("name") or path.name)}


def _read_text_artifact(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_csv_artifact(path: Path) -> pd.DataFrame:
    last_exc: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return pd.read_csv(path, encoding=encoding, dtype=str, keep_default_na=False)
        except UnicodeDecodeError as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _load_reference_artifact_objects(item: dict[str, Any]) -> list[Any]:
    raw_artifacts = item.get("reference_artifact_paths") or item.get("reference_artifacts") or []
    if not isinstance(raw_artifacts, list):
        return []

    objects: list[Any] = []
    for raw in raw_artifacts:
        descriptor = _normalize_artifact_descriptor(raw)
        if descriptor is None:
            continue
        path = Path(descriptor["path"])
        if not path.exists() or not path.is_file():
            continue
        try:
            if descriptor["kind"] == "csv":
                objects.append(_read_csv_artifact(path))
            elif descriptor["kind"] == "image":
                objects.append(path.read_bytes())
            elif descriptor["kind"] == "text":
                objects.append(_read_text_artifact(path).strip())
        except Exception as exc:
            logger.warning("failed to load reference artifact %s: %s", path, exc)
    return objects


def _get_direct_result_objects(item: dict[str, Any]) -> list[Any] | None:
    artifact_objects = _load_reference_artifact_objects(item)
    if artifact_objects:
        return artifact_objects

    if "reference_results" in item:
        value = item.get("reference_results")
        if value is None:
            return None
        if isinstance(value, list):
            return value
        return [value]
    if "reference_result" in item:
        value = item.get("reference_result")
        if value is None:
            return None
        if isinstance(value, list):
            return value
        return [value]
    return None


def _build_execute_result_from_objects(objects: list[Any], *, label: str) -> ExecuteResult:
    result = ExecuteResult()
    result.success = True
    for obj in objects:
        _append_result_object(result, obj, label)
    return result


def _contains_non_tabular(obj: Any) -> bool:
    if isinstance(obj, (bytes, str)):
        return True
    if isinstance(obj, (pd.DataFrame, pd.Series, np.ndarray)):
        return False
    if isinstance(obj, dict):
        return any(_contains_non_tabular(value) for value in obj.values())
    if isinstance(obj, (list, tuple, set)):
        return any(_contains_non_tabular(value) for value in obj)
    return _to_df(obj) is None


def _is_ai_preferred_output(obj: Any, kind: str) -> bool:
    if kind == "image" or isinstance(obj, bytes):
        return True
    if isinstance(obj, str):
        return True
    if kind != "other":
        return False
    return not bool(_object_to_comparison_frames(obj))


def _should_force_ai_judge(ref_exec: ExecuteResult, gen_exec: ExecuteResult, question: str) -> bool:
    # 兜底逻辑：仅在 AI plan 失败/缺失时启用。
    # 只基于实际执行结果判断，不再用题面关键词（题面语义由 AI plan 处理）。
    # 触发条件：任何输出包含图片，或任何输出包含纯字符串/不可结构化文本（文字说明题）。
    # 只要有文字说明成分，就不能结构化比较。
    if "image" in ref_exec.result_types or "image" in gen_exec.result_types:
        return True
    if any(
        _is_ai_preferred_output(item, kind)
        for item, kind in zip(ref_exec.raw_results, ref_exec.result_types, strict=False)
    ):
        return True
    if any(
        _is_ai_preferred_output(item, kind)
        for item, kind in zip(gen_exec.raw_results, gen_exec.result_types, strict=False)
    ):
        return True
    return False


def _append_result_object(result: ExecuteResult, obj: Any, label: str) -> None:
    try:
        import matplotlib.figure

        if isinstance(obj, matplotlib.figure.Figure):
            buffer = io.BytesIO()
            obj.savefig(buffer, format="png", bbox_inches="tight")
            buffer.seek(0)
            png_bytes = buffer.read()
            (_IMAGE_OUTPUT_DIR / f"{label}_{len(result.raw_results)}.png").write_bytes(png_bytes)
            result.raw_results.append(png_bytes)
            result.results.append(png_bytes)
            result.result_types.append("image")
            result.has_image = True
            return
    except ImportError:
        pass

    if isinstance(obj, bytes):
        result.raw_results.append(obj)
        result.results.append(obj)
        result.result_types.append("image")
        result.has_image = True
        return

    if isinstance(obj, str):
        result.raw_results.append(obj)
        result.results.append(obj)
        result.result_types.append("other")
        return

    df = _to_df(obj)
    if df is not None:
        result.raw_results.append(df.copy())
        result.results.append(df)
        result.result_types.append("dataframe")
        return

    result.raw_results.append(obj)
    result.results.append(obj)
    result.result_types.append("other")


def _extract_requested_objects(exec_globals: dict[str, Any], result_vars: list[str]) -> tuple[list[Any], list[str]]:
    objects: list[Any] = []
    missing: list[str] = []

    for var_name in result_vars:
        if var_name not in exec_globals or exec_globals[var_name] is None:
            missing.append(var_name)
            continue
        value = exec_globals[var_name]
        if var_name == "_eval_results" and isinstance(value, (list, tuple)):
            objects.extend(list(value))
        else:
            objects.append(value)

    return objects, missing


def _parse_structured_stdout(stdout_text: str) -> Any | None:
    text = (stdout_text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 1:
        try:
            return json.loads(lines[0])
        except Exception:
            return None
    return None


def execute_code(
    *,
    mode: str,
    sql_code: str = "",
    python_code: str = "",
    result_vars: list[str] | None = None,
    label: str = "unnamed",
    sort_rows: bool = True,
    allow_implicit_fallback: bool = True,
    leak_guard: bool | None = None,
    time_guard: bool | None = None,
    force_subprocess: bool = False,
) -> ExecuteResult:
    del sort_rows

    result = ExecuteResult()
    time_guard_enabled = _default_time_guard_for_label(label) if time_guard is None else time_guard
    if time_guard_enabled:
        violations = lint_runtime_date_functions(
            sql_code=sql_code,
            python_code=python_code,
            location_prefix=label,
        )
        if violations:
            result.error = format_runtime_date_lint_error(violations)
            return result

    use_subprocess = force_subprocess or os.getenv("TEXT2SQL_SUBPROCESS", "0") != "0"
    if use_subprocess:
        return _execute_python_subprocess(
            mode=mode,
            sql_code=sql_code,
            python_code=python_code,
            result_vars=result_vars,
            label=label,
            allow_implicit_fallback=allow_implicit_fallback,
            leak_guard=leak_guard,
            time_guard=time_guard,
        )

    if mode == "sql" or (mode not in {"python", "sql+python"} and sql_code and not python_code):
        try:
            from sweagent.text2sql.db_connector import query_to_dataframe

            df = query_to_dataframe(sql_code, max_rows=0)
            result.success = True
            result.raw_results = [df.copy()]
            result.results = [df]
            result.result_types = ["dataframe"]
        except Exception as exc:
            result.error = str(exc)
        return result

    stdout_buffer = io.StringIO()
    printed_objects: list[Any] = []

    def tracing_print(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("file") is None:
            printed_objects.extend(args)
            kwargs["file"] = stdout_buffer
        builtins.print(*args, **kwargs)

    exec_globals: dict[str, Any] = {
        "__name__": "__main__",
        "__file__": str(_MODULE_DIR / f"{label}.py"),
        "__builtins__": {**vars(builtins), "print": tracing_print},
        "pd": pd,
        "np": np,
        "json": json,
        "Path": Path,
        "tempfile": tempfile,
        "re": re,
        "sql_code": sql_code,
    }

    if _has_plot_code(python_code):
        try:
            import matplotlib

            matplotlib.use("Agg")
            _configure_matplotlib_for_evaluation()
            import matplotlib.pyplot as plt

            plt.close("all")
            plt.show = lambda *args, **kwargs: None
            warnings.filterwarnings(
                "ignore",
                message="FigureCanvasAgg is non-interactive, and thus cannot be shown",
                category=UserWarning,
            )
            exec_globals["plt"] = plt
        except ImportError:
            pass

    result = ExecuteResult()
    try:
        from sweagent.text2sql.db_connector import shared_connection_session

        tempdir = tempfile.gettempdir().replace("\\", "/")
        patched_code = python_code.replace("/tmp/", tempdir + "/")
        guard_enabled = _default_leak_guard_for_label(label) if leak_guard is None else leak_guard
        time_query_guard_enabled = _default_time_guard_for_label(label) if time_guard is None else time_guard
        with shared_connection_session():
            with redirect_stdout(stdout_buffer):
                with _prepend_sys_path(_MODULE_DIR):
                    with _reference_leak_guard(guard_enabled):
                        _install_db_aliases()
                        with _runtime_date_query_guard(time_query_guard_enabled):
                            exec(patched_code, exec_globals)
    except Exception as exc:
        result.error = str(exc)
        result.stdout_text = stdout_buffer.getvalue().strip()
        result.printed_objects = printed_objects
        result.exec_globals = exec_globals
        return result

    result.success = True
    result.stdout_text = stdout_buffer.getvalue().strip()
    result.printed_objects = printed_objects
    result.exec_globals = exec_globals

    requested_vars = _sanitize_result_vars(result_vars or [])
    extracted_objects: list[Any] = []

    if requested_vars:
        extracted_objects, result.missing_vars = _extract_requested_objects(exec_globals, requested_vars)

    figure_bytes = _capture_figure(label)

    if not requested_vars and figure_bytes is not None:
        _append_result_object(result, figure_bytes, label)
        return result

    if extracted_objects:
        for obj in extracted_objects:
            _append_result_object(result, obj, label)
        if figure_bytes is not None:
            _append_result_object(result, figure_bytes, label)
        return result

    if not allow_implicit_fallback:
        if requested_vars and result.missing_vars:
            result.success = False
            result.error = f"未找到结果变量: {result.missing_vars}"
        else:
            result.success = False
            result.error = "未提取到结果变量，请先生成变量列表或注入提取代码"
        return result

    heuristic_vars = _heuristic_result_vars(exec_globals)
    if heuristic_vars:
        heuristic_objects, _ = _extract_requested_objects(exec_globals, heuristic_vars)
        for obj in heuristic_objects:
            _append_result_object(result, obj, label)
        if result.raw_results and figure_bytes is not None:
            _append_result_object(result, figure_bytes, label)
        if result.raw_results:
            return result

    printed_candidates = [obj for obj in printed_objects if not isinstance(obj, str)]
    if printed_candidates:
        for obj in printed_candidates:
            _append_result_object(result, obj, label)
        if result.raw_results and figure_bytes is not None:
            _append_result_object(result, figure_bytes, label)
        if result.raw_results:
            return result

    if figure_bytes is not None:
        _append_result_object(result, figure_bytes, label)
        return result

    structured_stdout = _parse_structured_stdout(result.stdout_text)
    if structured_stdout is not None:
        _append_result_object(result, structured_stdout, label)
        return result

    if result.stdout_text:
        _append_result_object(result, result.stdout_text, label)
        return result

    if requested_vars and result.missing_vars:
        result.success = False
        result.error = f"未找到结果变量: {result.missing_vars}"
    else:
        result.success = False
        result.error = "未能提取最终结果"
    return result


def _record_db_cooldown_after_execution_timeout() -> None:
    raw = os.getenv("TEXT2SQL_EVAL_TIMEOUT_DB_COOLDOWN_SEC", "20")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = 20.0
    if seconds <= 0:
        return
    try:
        from sweagent.text2sql.db_connector import _record_connection_cooldown

        _record_connection_cooldown(seconds)
    except Exception:
        pass


def _record_db_cooldown_for_execution_error(error: str | None) -> None:
    lowered = (error or "").lower()
    if any(
        keyword in lowered
        for keyword in (
            "timed out",
            "lost connection",
            "can't connect",
            "too many connections",
            "blocked because of many connection errors",
        )
    ):
        _record_db_cooldown_after_execution_timeout()


def _execute_python_subprocess(
    mode: str,
    sql_code: str,
    python_code: str,
    result_vars: list[str] | None,
    label: str,
    allow_implicit_fallback: bool,
    leak_guard: bool | None,
    time_guard: bool | None,
) -> ExecuteResult:
    """在独立子进程中执行 Python 代码，通过 pickle 回传 ExecuteResult。"""
    import os as _os
    import pickle
    import subprocess
    import sys
    import tempfile

    project_root = str(Path(__file__).resolve().parents[2])

    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as out_f:
        output_path = out_f.name

    script_lines = [
        "import os, pickle, sys",
        "os.environ['TEXT2SQL_SUBPROCESS'] = '0'",
        f"sys.path.insert(0, {repr(project_root)})",
        "from sweagent.text2sql.evaluator import execute_code",
        f"result = execute_code(",
        f"    mode={repr(mode)},",
        f"    sql_code={repr(sql_code)},",
        f"    python_code={repr(python_code)},",
        f"    result_vars={repr(result_vars)},",
        f"    label={repr(label)},",
        f"    allow_implicit_fallback={repr(allow_implicit_fallback)},",
        f"    leak_guard={repr(leak_guard)},",
        f"    time_guard={repr(time_guard)},",
        "    force_subprocess=False,",
        f")",
        "if hasattr(result, 'exec_globals'): result.exec_globals = {}",
        f"with open({repr(output_path)}, 'wb') as f:",
        "    pickle.dump(result, f)",
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write("\n".join(script_lines))
        script_path = f.name

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            result = ExecuteResult()
            result.success = False
            result.error = f"子进程执行失败 (exit={proc.returncode}): {proc.stderr or proc.stdout or 'unknown error'}"
            return result

        with open(output_path, "rb") as f:
            return pickle.load(f)
    except subprocess.TimeoutExpired:
        _record_db_cooldown_after_execution_timeout()
        result = ExecuteResult()
        result.success = False
        result.error = "子进程执行超时 (timed out)"
        return result
    except Exception as exc:
        result = ExecuteResult()
        result.success = False
        result.error = f"子进程结果读取失败: {exc}"
        return result
    finally:
        try:
            _os.unlink(script_path)
        except Exception:
            pass
        try:
            _os.unlink(output_path)
        except Exception:
            pass



def _cols_match(s1: pd.Series, s2: pd.Series, *, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> bool:
    if len(s1) != len(s2):
        return False
    try:
        n1 = s1.astype(float).values
        n2 = s2.astype(float).values
        if _numeric_sequences_match(n1, n2, decimals=decimals):
            return True
        for scale in _SCALE_FACTORS:
            if _numeric_sequences_match(n1, n2, decimals=decimals, scale=scale):
                return True
        return False
    except (ValueError, TypeError):
        pass
    return [_normalized_text(value) for value in s1.tolist()] == [_normalized_text(value) for value in s2.tolist()]


def _df_values_match(
    ref_df: pd.DataFrame,
    gen_df: pd.DataFrame,
    *,
    decimals: int = _DEFAULT_COMPARE_DECIMALS,
    allow_extra_columns: bool = True,
    match_by_position: bool = False,
) -> bool:
    if len(ref_df) != len(gen_df):
        return False
    if allow_extra_columns:
        if len(gen_df.columns) < len(ref_df.columns):
            return False
    elif len(gen_df.columns) != len(ref_df.columns):
        return False
    if ref_df.shape == gen_df.shape:
        try:
            pd.testing.assert_frame_equal(ref_df, gen_df, check_dtype=False, check_exact=True)
            return True
        except Exception:
            pass

    if match_by_position:
        return all(
            _cols_match(ref_df[ref_col], gen_df[gen_col], decimals=decimals)
            for ref_col, gen_col in zip(ref_df.columns, gen_df.columns, strict=True)
        )

    remaining = list(gen_df.columns)
    for ref_col in ref_df.columns:
        matched = None
        for gen_col in remaining:
            if _cols_match(ref_df[ref_col], gen_df[gen_col], decimals=decimals):
                matched = gen_col
                break
        if matched is None:
            return False
        remaining.remove(matched)
    return True


def _dataframes_match(
    ref_df: pd.DataFrame,
    gen_df: pd.DataFrame,
    *,
    sort_rows: bool,
    decimals: int,
    strict_schema: bool = False,
) -> bool:
    if strict_schema and not _df_schema_matches(ref_df, gen_df):
        return False
    return _df_values_match(
        _normalize_df(ref_df, sort_rows=sort_rows, decimals=decimals, sort_columns=not strict_schema),
        _normalize_df(gen_df, sort_rows=sort_rows, decimals=decimals, sort_columns=not strict_schema),
        decimals=decimals,
        allow_extra_columns=not strict_schema,
        match_by_position=strict_schema,
    )


def _iter_atomic_values(obj: Any, *, decimals: int = _DEFAULT_COMPARE_DECIMALS):
    if isinstance(obj, bytes):
        raise TypeError("image values cannot be flattened")
    if isinstance(obj, pd.DataFrame):
        for column in obj.columns:
            for value in obj[column]:
                yield _normalize_value(value, decimals=decimals)
        return
    if isinstance(obj, pd.Series):
        for value in obj.tolist():
            yield _normalize_value(value, decimals=decimals)
        return
    if isinstance(obj, np.ndarray):
        for value in obj.reshape(-1).tolist():
            yield _normalize_value(value, decimals=decimals)
        return
    if isinstance(obj, dict):
        for key in sorted(obj.keys(), key=lambda item: str(item)):
            yield from _iter_atomic_values(obj[key], decimals=decimals)
        return
    if isinstance(obj, (list, tuple, set)):
        for value in obj:
            yield from _iter_atomic_values(value, decimals=decimals)
        return
    yield _normalize_value(obj, decimals=decimals)


def _flat_values(raw_results: list[Any], *, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> list[str] | None:
    flattened: list[str] = []
    try:
        for item in raw_results:
            flattened.extend(list(_iter_atomic_values(item, decimals=decimals)))
    except TypeError:
        return None
    return sorted(flattened, key=lambda item: (item, len(item)))


def _flat_values_match(ref_flat: list[str], gen_flat: list[str], *, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> bool:
    if not ref_flat:
        return not gen_flat
    if ref_flat == gen_flat:
        return True

    def try_match(scale: float = 1.0) -> bool:
        remaining = list(gen_flat)
        for ref_value in ref_flat:
            if ref_value in remaining:
                remaining.remove(ref_value)
                continue

            matched = False
            try:
                ref_number = float(ref_value)
                for index, gen_value in enumerate(remaining):
                    try:
                        if _numeric_values_match(ref_number, float(gen_value), decimals=decimals, scale=scale):
                            remaining.pop(index)
                            matched = True
                            break
                    except (TypeError, ValueError):
                        pass
            except (TypeError, ValueError):
                pass

            if not matched:
                return False
        return True

    if len(gen_flat) >= len(ref_flat) and try_match():
        return True
    for scale in _SCALE_FACTORS:
        if len(gen_flat) >= len(ref_flat) and try_match(scale):
            return True
    return False


def _object_to_comparison_frames(obj: Any) -> list[pd.DataFrame]:
    if isinstance(obj, bytes):
        return []
    if isinstance(obj, pd.DataFrame):
        return [obj.copy()]
    if isinstance(obj, pd.Series):
        return [obj.to_frame()]
    if isinstance(obj, np.ndarray):
        return [pd.DataFrame(obj)]

    if isinstance(obj, dict):
        frames: list[pd.DataFrame] = []
        scalar_row: dict[str, Any] = {}
        for key, value in sorted(obj.items(), key=lambda item: str(item[0])):
            key_str = str(key)
            if isinstance(value, dict):
                nested_frames = _object_to_comparison_frames(value)
                if nested_frames:
                    for frame in nested_frames:
                        renamed = frame.copy()
                        renamed.columns = [f"{key_str}.{col}" for col in renamed.columns]
                        frames.append(renamed)
                continue
            if _looks_like_record_list(value):
                frames.append(pd.DataFrame(value))
                continue
            if isinstance(value, pd.DataFrame):
                frames.append(value.copy())
                continue
            if isinstance(value, pd.Series):
                frames.append(value.to_frame())
                continue
            if isinstance(value, np.ndarray):
                frames.append(pd.DataFrame(value))
                continue
            if isinstance(value, (list, tuple)):
                if all(_is_scalar_like(item) for item in value):
                    frames.append(pd.DataFrame(value))
                else:
                    for nested in value:
                        frames.extend(_object_to_comparison_frames(nested))
                continue
            scalar_row[key_str] = value
        if scalar_row:
            frames.insert(0, pd.DataFrame([scalar_row]))
        return frames

    if _looks_like_record_list(obj):
        return [pd.DataFrame(obj)]

    if isinstance(obj, (list, tuple)):
        if all(_is_scalar_like(item) for item in obj):
            return [pd.DataFrame(obj)]
        frames: list[pd.DataFrame] = []
        for item in obj:
            frames.extend(_object_to_comparison_frames(item))
        return frames

    if _is_scalar_like(obj):
        return [pd.DataFrame([[obj]])]
    return []


def _merge_results_for_comparison(
    raw_results: list[Any],
    *,
    sort_rows: bool,
    decimals: int = _DEFAULT_COMPARE_DECIMALS,
) -> pd.DataFrame | None:
    rows: list[list[str]] = []
    width = 0

    for item in raw_results:
        frames = _object_to_comparison_frames(item)
        if not frames and item is not None:
            return None
        for frame in frames:
            normalized = _normalize_df(frame, sort_rows=sort_rows, decimals=decimals)
            if normalized.empty and len(normalized.columns) == 0:
                continue
            for row in normalized.itertuples(index=False, name=None):
                cell_values = [_normalize_value(value, decimals=decimals) for value in row]
                width = max(width, len(cell_values))
                rows.append(cell_values)

    if not rows:
        return pd.DataFrame()

    padded = [row + [""] * (width - len(row)) for row in rows]
    merged = pd.DataFrame(padded)
    return _normalize_df(merged, sort_rows=sort_rows, decimals=decimals)


def _normalize_for_text_judge(obj: Any, *, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> Any:
    if isinstance(obj, pd.DataFrame):
        normalized = _normalize_df(obj, sort_rows=True, decimals=decimals)
        return {
            "type": "dataframe",
            "rows": [[_normalize_value(value, decimals=decimals) for value in row] for row in normalized.itertuples(index=False, name=None)],
        }
    if isinstance(obj, pd.Series):
        return {"type": "series", "values": [_normalize_value(value, decimals=decimals) for value in obj.tolist()]}
    if isinstance(obj, np.ndarray):
        return _normalize_for_text_judge(obj.tolist(), decimals=decimals)
    if isinstance(obj, dict):
        return {str(key): _normalize_for_text_judge(value, decimals=decimals) for key, value in sorted(obj.items(), key=lambda item: str(item[0]))}
    if isinstance(obj, (list, tuple)):
        return [_normalize_for_text_judge(value, decimals=decimals) for value in obj]
    if isinstance(obj, bytes):
        return "<image bytes>"
    return _normalize_value(obj, decimals=decimals)


def _serialize_for_text_judge(obj: Any) -> str:
    try:
        return json.dumps(_normalize_for_text_judge(obj), ensure_ascii=False, sort_keys=True, indent=2)
    except Exception:
        return str(obj)


def _split_image_and_non_image(results: list[Any], result_types: list[str]) -> tuple[list[bytes], list[Any]]:
    images: list[bytes] = []
    non_images: list[Any] = []
    for item, kind in zip(results, result_types, strict=False):
        if kind == "image" and isinstance(item, bytes):
            images.append(item)
        else:
            non_images.append(item)
    return images, non_images


def _split_visual_parts(exec_result: ExecuteResult) -> tuple[list[bytes], list[Any], list[str]]:
    images: list[bytes] = []
    tables: list[Any] = []
    texts: list[str] = []
    for item, kind in zip(exec_result.raw_results, exec_result.result_types, strict=False):
        if kind == "image" and isinstance(item, bytes):
            images.append(item)
        elif kind == "dataframe":
            tables.append(item)
        elif isinstance(item, str):
            if item.strip():
                texts.append(item.strip())
        elif kind != "image":
            df = _to_df(item)
            if df is not None:
                tables.append(df)
    return images, tables, texts


def _summarize_for_visual_prompt(obj: Any, *, max_rows: int = 8, decimals: int = _DEFAULT_COMPARE_DECIMALS) -> str:
    if isinstance(obj, pd.DataFrame):
        preview = obj.head(max_rows).copy()
        for col in preview.select_dtypes(include="number").columns:
            preview[col] = preview[col].round(decimals)
        return f"DataFrame shape={obj.shape}, columns={list(obj.columns)}\n{preview.to_string(index=False)}"
    try:
        return json.dumps(_normalize_for_text_judge(obj, decimals=decimals), ensure_ascii=False, indent=2)[:4000]
    except Exception:
        return str(obj)[:4000]


def _reference_code_summary(ref_item: dict[str, Any], *, max_chars: int = 8000) -> str:
    parts: list[str] = []
    if ref_item.get("sql_code"):
        parts.append("SQL:\n" + str(ref_item.get("sql_code")))
    if ref_item.get("python_code"):
        parts.append("Python:\n" + str(ref_item.get("python_code")))
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"
    return text


def _normalize_checklist_item(raw: Any, fallback_id: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id") or fallback_id).strip()
    if not item_id:
        item_id = fallback_id
    try:
        weight = float(raw.get("weight", 1))
    except (TypeError, ValueError):
        weight = 1.0
    if weight <= 0:
        weight = 1.0
    return {
        "id": item_id,
        "weight": weight,
        "mandatory": bool(raw.get("mandatory", False)),
        "op": str(raw.get("op") or "visual_semantic_match"),
        "path": str(raw.get("path") or raw.get("gen_path") or ""),
        "ref_path": str(raw.get("ref_path") or ""),
        "gen_path": str(raw.get("gen_path") or ""),
        "expected": raw.get("expected", raw.get("description", "")),
    }


def _extract_visual_checklist_ai(
    *,
    question: str,
    ref_item: dict[str, Any],
    ref_images: list[bytes],
    ref_tables: list[Any],
    ref_texts: list[str],
    decimals: int,
) -> list[dict[str, Any]]:
    try:
        import litellm
    except ImportError:
        return []
    _ensure_litellm_registry(litellm)

    model = os.getenv("VISION_CHECKLIST_MODEL", "kimi-k2.5")

    table_summary = "\n\n".join(
        f"[table {index}]\n{_summarize_for_visual_prompt(table, decimals=decimals)}"
        for index, table in enumerate(ref_tables, start=1)
    )
    text_summary = "\n\n".join(ref_texts)[:4000]
    ref_code = _reference_code_summary(ref_item)
    prompt = f"""Build a grading checklist for a generated chart answer.

Return strict JSON only: a list of checklist items. Each item must contain:
id, weight, mandatory, op, expected.

Use visual/chart items only. Do not create table-value matching items for CSV outputs; table matching is handled deterministically outside the VLM.
Good ops include chart_type, title_match, axes_match, legend_match, series_match, label_match, annotation_match, visual_semantic_match.

Grading philosophy — emphasize chart semantics over cosmetic styling:
- The most important items are: chart_type, axes_match, series_count/series_match (number and identity of plotted series), and visual_semantic_match (data trend, magnitude, conclusion). Give these the largest weights.
- Cosmetic styling items (exact colors, line styles, marker shapes, exact title wording, exact legend wording, font, gridlines) are LOW-IMPORTANCE. Each such item must have weight <= 0.05 of the total, and mandatory MUST be false.
- title_match and legend_match should grade *semantic* equivalence, not exact-string match. Different wording that conveys the same meaning is acceptable.
- mandatory should be true ONLY for fundamental correctness: wrong chart type, missing required series, wrong axis variable, or a trend that contradicts the reference. Never mark color/line-style/title-wording/legend-wording items as mandatory.
- Prefer fewer, higher-signal items over many nitpicky styling items. If you would emit more than two pure-styling items, drop the rest.
Weights should reflect importance under the rules above.

Question:
{question}

Reference code:
{ref_code or "(not provided)"}

Reference table summary:
{table_summary or "(no CSV table)"}

Reference text output:
{text_summary or "(no text output)"}
"""

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for index, image in enumerate(ref_images[:3], start=1):
        encoded = base64.b64encode(image).decode("ascii")
        content.append({"type": "text", "text": f"Reference image {index}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})

    cache_payload = {
        "model": model,
        "prompt": prompt,
        "ref_image_sha256": [_sha256_bytes(image) for image in ref_images[:3]],
    }
    cached = _read_judge_cache("visual_checklist", cache_payload)
    if cached is not None and isinstance(cached.get("checklist"), list):
        checklist: list[dict[str, Any]] = []
        for index, raw in enumerate(cached["checklist"], start=1):
            item = _normalize_checklist_item(raw, f"cached_{index}")
            if item is not None:
                checklist.append(item)
        return checklist

    try:
        response = _evaluator_litellm_completion(
            litellm,
            model=model,
            messages=[{"role": "user", "content": content}],
            temperature=0.0,
            **_litellm_completion_kwargs(model),
        )
        parsed = _extract_json_from_text(response.choices[0].message.content or "")
    except Exception as exc:
        logger.warning("visual checklist extraction failed: %s", exc)
        return []

    if isinstance(parsed, dict):
        parsed = parsed.get("checklist") or parsed.get("items") or parsed.get("results")
    if not isinstance(parsed, list):
        return []

    checklist: list[dict[str, Any]] = []
    for index, raw in enumerate(parsed, start=1):
        item = _normalize_checklist_item(raw, f"visual_{index}")
        if item is not None:
            checklist.append(item)
    _write_judge_cache("visual_checklist", cache_payload, {"raw": parsed, "checklist": checklist})
    return checklist


def _build_visual_checklist(
    ref_item: dict[str, Any],
    ref_exec: ExecuteResult,
    *,
    question: str,
    decimals: int,
) -> list[dict[str, Any]]:
    provided = ref_item.get("visual_checklist")
    checklist: list[dict[str, Any]] = []
    if isinstance(provided, list):
        for index, raw in enumerate(provided, start=1):
            item = _normalize_checklist_item(raw, f"provided_{index}")
            if item is not None:
                checklist.append(item)

    ref_images, ref_tables, ref_texts = _split_visual_parts(ref_exec)
    if not checklist and ref_images:
        checklist.extend(
            _extract_visual_checklist_ai(
                question=question,
                ref_item=ref_item,
                ref_images=ref_images,
                ref_tables=ref_tables,
                ref_texts=ref_texts,
                decimals=decimals,
            )
        )

    deterministic: list[dict[str, Any]] = []
    if ref_tables:
        deterministic.append({
            "id": "table_match",
            "weight": 6.0,
            "mandatory": True,
            "op": "table_ref_equal",
            "ref_path": "table",
            "gen_path": "table",
            "expected": "generated tabular outputs should match reference CSV outputs",
        })
    if ref_texts:
        deterministic.append({
            "id": "text_match",
            "weight": 2.0,
            "mandatory": False,
            "op": "text_semantic_match",
            "ref_path": "text",
            "gen_path": "text",
            "expected": "generated textual outputs should match reference text outputs",
        })
    if ref_images and not any(str(item.get("op", "")).startswith("visual") or "chart" in str(item.get("op", "")) for item in checklist):
        checklist.append({
            "id": "visual_equivalence",
            "weight": 4.0,
            "mandatory": True,
            "op": "visual_semantic_match",
            "expected": "generated image should communicate the same chart type, plotted data, labels, and conclusion",
        })

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in deterministic + checklist:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        merged.append(item)
    return merged


def _judge_visual_checklist_ai(
    *,
    question: str,
    checklist: list[dict[str, Any]],
    ref_images: list[bytes],
    gen_images: list[bytes],
) -> dict[str, dict[str, Any]]:
    visual_items = [
        item for item in checklist
        if item.get("op") not in {"table_ref_equal", "text_semantic_match"}
    ]
    if not visual_items:
        return {}
    if not ref_images or not gen_images:
        return {
            str(item["id"]): {"score": 0.0, "passed": False, "reason": "missing reference or generated image"}
            for item in visual_items
        }

    try:
        import litellm
    except ImportError:
        return {
            str(item["id"]): {"score": 0.0, "passed": False, "reason": "litellm is not installed"}
            for item in visual_items
        }
    _ensure_litellm_registry(litellm)

    model = os.getenv("VISION_JUDGE_MODEL") or os.getenv("VISION_MODEL", "kimi-k2.5")
    prompt = f"""The reference image(s) come first, followed by generated image(s).
Grade each checklist item independently based on chart semantics, not pixel-level or string-level identity.

Scoring guidance (apply to every item unless its description explicitly demands strict styling):
- Colors: near-equivalent colors should score 1.0 (e.g. blue vs deep/navy blue, orange vs red/amber, green vs teal). Only penalize when the color choice changes which series is which, or violates an explicit semantic encoding stated in the question.
- Line styles and markers: solid vs dashed, circle vs square markers, line thickness — score 1.0 unless the checklist item is explicitly about line style AND the difference makes the chart misleading.
- Titles: different wording that conveys the same subject/comparison should score 1.0. Only penalize when the title states a different metric, time range, or subject.
- Legends: different wording for the same series should score 1.0. Penalize only when the legend mislabels a series or omits one.
- Axes: score 1.0 if the axis variable, unit, and approximate range match. Tick formatting and minor range padding do not matter.
- Series count and chart type: be strict — these are semantic.
- visual_semantic_match (trend/magnitude): score 1.0 if the overall shape and direction match and individual values are within ~10–15% of the reference. Score 0.7–0.9 for mostly-correct trend with one notable deviation. Score 0.3–0.6 only when the trend direction is partly wrong. Score 0 only for opposite or unrelated trend.

Return strict JSON only:
{{"results":[{{"id":"item_id","score":0.0,"passed":false,"reason":"brief Chinese reason"}}]}}
score must be between 0 and 1. Use passed=true when score >= 0.8 for cosmetic items, or score >= 0.999 for strict semantic items (chart_type, series_count).

Question:
{question}

Checklist JSON:
{json.dumps(visual_items, ensure_ascii=False, indent=2)}
"""

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for index, image in enumerate(ref_images[:3], start=1):
        content.append({"type": "text", "text": f"Reference image {index}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}"}})
    for index, image in enumerate(gen_images[:3], start=1):
        content.append({"type": "text", "text": f"Generated image {index}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}"}})

    cache_payload = {
        "model": model,
        "prompt": prompt,
        "ref_image_sha256": [_sha256_bytes(image) for image in ref_images[:3]],
        "gen_image_sha256": [_sha256_bytes(image) for image in gen_images[:3]],
    }
    cached = _read_judge_cache("visual_judge", cache_payload)
    if cached is not None and isinstance(cached.get("outcomes"), dict):
        return cached["outcomes"]

    try:
        response = _evaluator_litellm_completion(
            litellm,
            model=model,
            messages=[{"role": "user", "content": content}],
            temperature=0.0,
            **_litellm_completion_kwargs(model),
        )
        parsed = _extract_json_from_text(response.choices[0].message.content or "")
    except Exception as exc:
        logger.warning("visual checklist judge failed: %s", exc)
        same, reason = _compare_images_with_ai(ref_images[0], gen_images[0])
        fallback_score = 1.0 if same else 0.0
        fallback_reason = reason or ("fallback image comparison passed" if same else "fallback image comparison failed")
        return {
            str(item["id"]): {"score": fallback_score, "passed": bool(same), "reason": fallback_reason}
            for item in visual_items
        }

    if isinstance(parsed, dict):
        parsed = parsed.get("results") or parsed.get("items") or parsed.get("checklist")
    if not isinstance(parsed, list):
        return {
            str(item["id"]): {"score": 0.0, "passed": False, "reason": "VLM did not return JSON results"}
            for item in visual_items
        }

    outcomes: dict[str, dict[str, Any]] = {}
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            continue
        try:
            score = float(raw.get("score", 1.0 if raw.get("passed") else 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))
        outcomes[item_id] = {
            "score": score,
            "passed": bool(raw.get("passed", score >= 0.999)),
            "reason": str(raw.get("reason") or ""),
        }

    for item in visual_items:
        item_id = str(item["id"])
        outcomes.setdefault(item_id, {"score": 0.0, "passed": False, "reason": "VLM omitted this checklist item"})
    _write_judge_cache("visual_judge", cache_payload, {"outcomes": outcomes})
    return outcomes


def _score_visual_outputs(
    ref_item: dict[str, Any],
    ref_exec: ExecuteResult,
    gen_exec: ExecuteResult,
    *,
    question: str,
    sort_rows: bool,
    decimals: int,
) -> tuple[bool, float, list[dict[str, Any]], str | None]:
    checklist = _build_visual_checklist(ref_item, ref_exec, question=question, decimals=decimals)
    if not checklist:
        return False, 0.0, [], "no visual checklist could be built"

    ref_images, ref_tables, ref_texts = _split_visual_parts(ref_exec)
    gen_images, gen_tables, gen_texts = _split_visual_parts(gen_exec)
    vlm_outcomes = _judge_visual_checklist_ai(
        question=question,
        checklist=checklist,
        ref_images=ref_images,
        gen_images=gen_images,
    )

    details: list[dict[str, Any]] = []

    for item in checklist:
        item_id = str(item["id"])
        weight = float(item.get("weight", 1.0))
        op = str(item.get("op") or "")
        score = 0.0
        reason = ""

        if op == "table_ref_equal":
            ref_table_exec = _build_execute_result_from_objects(ref_tables, label=f"{ref_item.get('id', 'unknown')}_ref_tables")
            gen_table_exec = _build_execute_result_from_objects(gen_tables, label=f"{ref_item.get('id', 'unknown')}_gen_tables")
            ok, msg = _compare_result_lists(
                ref_table_exec,
                gen_table_exec,
                question=question,
                sort_rows=sort_rows,
                force_ai_judge=False,
                strict_schema=bool(ref_item.get("strict_output_schema", False)),
            )
            score = 1.0 if ok else 0.0
            reason = msg or ""
        elif op == "text_semantic_match":
            ref_text = "\n\n".join(ref_texts)
            gen_text = "\n\n".join(gen_texts) if gen_texts else json.dumps(
                _normalize_for_text_judge(gen_exec.raw_results, decimals=decimals),
                ensure_ascii=False,
                sort_keys=True,
            )
            same, msg = _compare_text_with_ai(ref_text, gen_text, question)
            score = 1.0 if same else 0.0
            reason = msg or ("AI text judge failed" if same is None else "")
        else:
            outcome = vlm_outcomes.get(item_id, {})
            try:
                score = float(outcome.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(1.0, score))
            reason = str(outcome.get("reason") or "")

        details.append({
            "id": item_id,
            "op": op,
            "weight": weight,
            "mandatory": bool(item.get("mandatory", False)),
            "score": score,
            "passed": score >= 0.999,
            "reason": reason,
            "expected": item.get("expected", ""),
        })

    # 按输出类型重新归一化大类权重：csv:image:text = 6:4:2（按存在性缩放）
    def _item_category(item: dict[str, Any]) -> str:
        op = item.get("op", "")
        if op == "table_ref_equal":
            return "csv"
        if op == "text_semantic_match":
            return "text"
        return "image"

    has_csv = any(_item_category(d) == "csv" for d in details)
    has_text = any(_item_category(d) == "text" for d in details)
    has_image = any(_item_category(d) == "image" for d in details)
    csv_base = 6.0 if has_csv else 0.0
    image_base = 4.0 if has_image else 0.0
    text_base = 2.0 if has_text else 0.0
    total_base = csv_base + image_base + text_base

    category_ratios = {
        "csv": csv_base / total_base,
        "image": image_base / total_base,
        "text": text_base / total_base,
    }

    if len([r for r in category_ratios.values() if r > 0]) > 1:
        from collections import defaultdict
        current_sums: dict[str, float] = defaultdict(float)
        for d in details:
            current_sums[_item_category(d)] += d["weight"]

        total_weight = 0.0
        earned = 0.0
        for d in details:
            cat = _item_category(d)
            current = current_sums.get(cat, 0.0)
            ratio = category_ratios.get(cat, 0.0)
            if current > 0 and ratio > 0:
                new_weight = d["weight"] / current * ratio
            else:
                new_weight = 0.0
            d["weight"] = new_weight
            total_weight += new_weight
            earned += new_weight * d["score"]
    else:
        total_weight = sum(d["weight"] for d in details)
        earned = sum(d["weight"] * d["score"] for d in details)

    final_score = earned / total_weight if total_weight > 0 else 0.0
    mandatory_failed = [item for item in details if item["mandatory"] and item["score"] < 0.999]
    failed = [item for item in details if item["score"] < 0.999]
    passed = final_score >= 0.999 and not mandatory_failed
    if passed:
        return True, 1.0, details, None
    failed_ids = ", ".join(str(item["id"]) for item in failed[:8])
    msg = f"visual checklist score {final_score:.3f}; failed items: {failed_ids or '(none)'}"
    return False, final_score, details, msg


def _compare_with_ai_judge(
    ref_exec: ExecuteResult,
    gen_exec: ExecuteResult,
    *,
    question: str,
    decimals: int,
) -> tuple[bool | None, str]:
    ref_images, ref_non_images = _split_image_and_non_image(ref_exec.raw_results, ref_exec.result_types)
    gen_images, gen_non_images = _split_image_and_non_image(gen_exec.raw_results, gen_exec.result_types)

    if ref_images or gen_images:
        if len(ref_images) != len(gen_images):
            return False, "AI裁判判定不一致：图片数量不同"
        for index, (ref_img, gen_img) in enumerate(zip(ref_images, gen_images, strict=False), start=1):
            same, reason = _compare_images_with_ai(ref_img, gen_img)
            if same is None:
                return None, f"第{index}个图片结果 AI 判图失败"
            if not same:
                suffix = f"；理由：{reason}" if reason else ""
                return False, f"AI裁判判定不一致：第{index}个图片结果不一致{suffix}"

    if ref_non_images or gen_non_images:
        ref_text = json.dumps(_normalize_for_text_judge(ref_non_images, decimals=decimals), ensure_ascii=False, sort_keys=True)
        gen_text = json.dumps(_normalize_for_text_judge(gen_non_images, decimals=decimals), ensure_ascii=False, sort_keys=True)
        same, reason = _compare_text_with_ai(ref_text, gen_text, question)
        if same is None:
            return None, "AI裁判失败"
        if not same:
            suffix = f"；理由：{reason}" if reason else ""
            return False, f"AI裁判判定不一致{suffix}"

    return True, ""


def _compare_result_lists(
    ref_exec: ExecuteResult,
    gen_exec: ExecuteResult,
    qid: str = "",
    question: str = "",
    sort_rows: bool = True,
    force_ai_judge: bool | None = None,
    strict_schema: bool = False,
) -> tuple[bool, str | None]:
    del qid

    pairwise_error: str | None = None
    decimals = _extract_compare_decimals(question)
    outputs_require_ai_judge = _should_force_ai_judge(ref_exec, gen_exec, question)

    # 1) AI 明确判断需要 AI judge
    if force_ai_judge is True:
        ai_same, ai_msg = _compare_with_ai_judge(ref_exec, gen_exec, question=question, decimals=decimals)
        if ai_same is True:
            return True, None
        if ai_same is None:
            return False, ai_msg
        return False, ai_msg

    # 2) AI 明确判断不需要 AI judge
    # 但如果真实执行结果已经包含文本/图片等非结构化输出，仍然交给 AI judge；
    # 严格 CSV schema 评测除外，额外文本/图片应按输出结构不一致处理。
    if force_ai_judge is False:
        if outputs_require_ai_judge and not strict_schema:
            ai_same, ai_msg = _compare_with_ai_judge(ref_exec, gen_exec, question=question, decimals=decimals)
            if ai_same is True:
                return True, None
            if ai_same is None:
                return False, ai_msg
            return False, ai_msg

        if len(ref_exec.results) == len(gen_exec.results):
            pairwise_ok = True
            for index, (ref_item, gen_item, ref_type, gen_type) in enumerate(
                zip(ref_exec.results, gen_exec.results, ref_exec.result_types, gen_exec.result_types, strict=False),
                start=1,
            ):
                if ref_type == "image" or gen_type == "image":
                    pairwise_error = f"第{index}个结果类型不一致：参考={ref_type}，生成={gen_type}"
                    pairwise_ok = False
                    break

                ref_df = _to_df(ref_item)
                gen_df = _to_df(gen_item)
                if ref_df is not None and gen_df is not None:
                    if _dataframes_match(
                        ref_df,
                        gen_df,
                        sort_rows=sort_rows,
                        decimals=decimals,
                        strict_schema=strict_schema,
                    ):
                        continue
                    pairwise_error = f"第{index}个结果表格结构或数据不一致" if strict_schema else f"第{index}个结果表格数据不一致"
                    pairwise_ok = False
                    break

                if _serialize_for_text_judge(ref_item) == _serialize_for_text_judge(gen_item):
                    continue

                pairwise_error = f"第{index}个结果不一致"
                pairwise_ok = False
                break

            if pairwise_ok:
                return True, None
        elif strict_schema:
            return False, f"结果数量不一致：参考={len(ref_exec.results)}，生成={len(gen_exec.results)}"

        if strict_schema:
            return False, pairwise_error or "严格输出结构不一致"

        ref_merged = _merge_results_for_comparison(ref_exec.raw_results, sort_rows=sort_rows, decimals=decimals)
        gen_merged = _merge_results_for_comparison(gen_exec.raw_results, sort_rows=sort_rows, decimals=decimals)
        if ref_merged is not None and gen_merged is not None:
            if _df_values_match(ref_merged, gen_merged, decimals=decimals):
                return True, None

        ref_flat = _flat_values(ref_exec.raw_results, decimals=decimals)
        gen_flat = _flat_values(gen_exec.raw_results, decimals=decimals)
        if ref_flat is not None and gen_flat is not None and _flat_values_match(ref_flat, gen_flat, decimals=decimals):
            return True, None

        if pairwise_error:
            return False, pairwise_error
        return False, "整体结果不一致"

    # 3) AI 未判断（None）→ 保守兜底：先看执行结果是否需要 AI，否则规则 + fallback AI
    if outputs_require_ai_judge:
        ai_same, ai_msg = _compare_with_ai_judge(ref_exec, gen_exec, question=question, decimals=decimals)
        if ai_same is True:
            return True, None
        if ai_same is None:
            return False, ai_msg
        return False, ai_msg

    if len(ref_exec.results) == len(gen_exec.results):
        pairwise_ok = True
        for index, (ref_item, gen_item, ref_type, gen_type) in enumerate(
            zip(ref_exec.results, gen_exec.results, ref_exec.result_types, gen_exec.result_types, strict=False),
            start=1,
        ):
            if ref_type == "image" or gen_type == "image":
                if isinstance(ref_item, bytes) and isinstance(gen_item, bytes):
                    same = _compare_images_with_ai(ref_item, gen_item)
                    if same is None:
                        return False, f"第{index}个结果是图片，AI 判图失败"
                    if not same:
                        pairwise_error = f"第{index}个结果图片内容不一致"
                        pairwise_ok = False
                        break
                    continue
                pairwise_error = f"第{index}个结果类型不一致：参考={ref_type}，生成={gen_type}"
                pairwise_ok = False
                break

            ref_df = _to_df(ref_item)
            gen_df = _to_df(gen_item)
            if ref_df is not None and gen_df is not None:
                if _dataframes_match(
                    ref_df,
                    gen_df,
                    sort_rows=sort_rows,
                    decimals=decimals,
                    strict_schema=strict_schema,
                ):
                    continue
                pairwise_error = f"第{index}个结果表格结构或数据不一致" if strict_schema else f"第{index}个结果表格数据不一致"
                pairwise_ok = False
                break

            if _serialize_for_text_judge(ref_item) == _serialize_for_text_judge(gen_item):
                continue

            pairwise_error = f"第{index}个结果不一致"
            pairwise_ok = False
            break

        if pairwise_ok:
            return True, None
    elif strict_schema:
        return False, f"结果数量不一致：参考={len(ref_exec.results)}，生成={len(gen_exec.results)}"

    if strict_schema:
        return False, pairwise_error or "严格输出结构不一致"

    ref_merged = _merge_results_for_comparison(ref_exec.raw_results, sort_rows=sort_rows, decimals=decimals)
    gen_merged = _merge_results_for_comparison(gen_exec.raw_results, sort_rows=sort_rows, decimals=decimals)
    if ref_merged is not None and gen_merged is not None:
        if _df_values_match(ref_merged, gen_merged, decimals=decimals):
            return True, None

    ref_flat = _flat_values(ref_exec.raw_results, decimals=decimals)
    gen_flat = _flat_values(gen_exec.raw_results, decimals=decimals)
    if ref_flat is not None and gen_flat is not None and _flat_values_match(ref_flat, gen_flat, decimals=decimals):
        return True, None

    ref_text = json.dumps(_normalize_for_text_judge(ref_exec.raw_results, decimals=decimals), ensure_ascii=False, sort_keys=True)
    gen_text = json.dumps(_normalize_for_text_judge(gen_exec.raw_results, decimals=decimals), ensure_ascii=False, sort_keys=True)
    same, reason = _compare_text_with_ai(ref_text, gen_text, question)
    if same is True:
        return True, None
    if same is None:
        return False, "整体结果 AI 对比失败"

    if pairwise_error:
        return False, pairwise_error
    return False, "整体结果不一致"


def _result_is_text_only(exec_result: ExecuteResult) -> bool:
    return bool(exec_result.raw_results) and all(kind == "other" for kind in exec_result.result_types)


def _execution_score(exec_result: ExecuteResult, expected_count: int | None) -> tuple[int, int, int, int, int]:
    structured_count = sum(kind in {"dataframe", "image"} for kind in exec_result.result_types)
    count_match = int(expected_count is not None and expected_count > 0 and len(exec_result.raw_results) == expected_count)
    has_results = int(bool(exec_result.success and exec_result.raw_results))
    no_missing = int(not exec_result.missing_vars)
    not_text_only = int(not _result_is_text_only(exec_result))
    return (has_results, count_match, no_missing, structured_count, not_text_only)


def _needs_ai_retry(exec_result: ExecuteResult, expected_count: int | None) -> bool:
    if not exec_result.success or not exec_result.raw_results:
        return True
    if exec_result.missing_vars:
        return True
    if expected_count is not None and expected_count > 0 and len(exec_result.raw_results) != expected_count:
        return True
    if _result_is_text_only(exec_result):
        return True
    return False


def _resolve_item_execution(
    item: dict[str, Any],
    *,
    question: str,
    label: str,
    expected_count: int | None,
    sort_rows: bool,
) -> ExecuteResult:
    mode = item.get("mode", "sql")
    sql_code = item.get("sql_code", "") or ""
    python_code = item.get("python_code", "") or ""
    provided_vars = _sanitize_result_vars(item.get("result_vars") or [])
    direct_results = _get_direct_result_objects(item)

    best_result: ExecuteResult | None = None

    if direct_results:
        return _build_execute_result_from_objects(direct_results, label=label)

    if not sql_code and not python_code:
        result = ExecuteResult()
        result.error = "No direct reference result or executable code provided"
        return result

    if mode == "sql":
        return execute_code(
            mode=mode,
            sql_code=sql_code,
            python_code=python_code,
            result_vars=provided_vars or None,
            label=label,
            sort_rows=sort_rows,
        )

    if provided_vars:
        best_result = execute_code(
            mode=mode,
            sql_code=sql_code,
            python_code=python_code,
            result_vars=provided_vars,
            label=label,
            sort_rows=sort_rows,
            allow_implicit_fallback=False,
        )

    if python_code and (best_result is None or _needs_ai_retry(best_result, expected_count)):
        plan = _get_result_plan(question, python_code, expected_count=expected_count)
        plan_vars = _sanitize_result_vars(plan.get("result_vars"))
        inject_code = str(plan.get("inject_code") or "").strip()

        if inject_code or plan_vars:
            injected_python = python_code
            if inject_code:
                injected_python += "\n\n# --- evaluator injection ---\n"
                injected_python += inject_code
                injected_python += "\n"

            injected_result = execute_code(
                mode=mode,
                sql_code=sql_code,
                python_code=injected_python,
                result_vars=plan_vars or (["final_result"] if inject_code else None),
                label=f"{label}_inject",
                sort_rows=sort_rows,
                allow_implicit_fallback=False,
            )
            if best_result is None or _execution_score(injected_result, expected_count) >= _execution_score(best_result, expected_count):
                best_result = injected_result

    if best_result is None or _needs_ai_retry(best_result, expected_count):
        fallback_result = execute_code(
            mode=mode,
            sql_code=sql_code,
            python_code=python_code,
            result_vars=provided_vars or None,
            label=f"{label}_fallback",
            sort_rows=sort_rows,
            allow_implicit_fallback=True,
        )
        if best_result is None or _execution_score(fallback_result, expected_count) >= _execution_score(best_result, expected_count):
            best_result = fallback_result

    assert best_result is not None
    return best_result


class CompareResult:
    def __init__(self, qid: str | int):
        self.qid = qid
        self.passed = False
        self.score = 0.0
        self.max_score = 1.0
        self.score_details: list[dict[str, Any]] = []
        self.evaluation_kind: str | None = None
        self.error_type: str | None = None
        self.error_msg: str | None = None
        self.pit_diagnostic_flags: list[str] = []
        self.ref_sql: str | None = None
        self.ref_python: str | None = None
        self.gen_sql: str | None = None
        self.gen_python: str | None = None
        self.ref_result: Any = None
        self.gen_result: Any = None


def _infer_reference_evaluation_kind(ref_item: dict[str, Any]) -> str | None:
    explicit = str(ref_item.get("evaluation_kind") or "").strip().lower()
    if explicit in {"csv", "text_ai", "vision", "mixed"}:
        return explicit
    raw_artifacts = ref_item.get("reference_artifact_paths") or ref_item.get("reference_artifacts") or []
    if isinstance(raw_artifacts, list) and raw_artifacts:
        kinds: set[str] = set()
        for raw in raw_artifacts:
            descriptor = _normalize_artifact_descriptor(raw)
            if descriptor is not None:
                kinds.add(descriptor["kind"])
        if "image" in kinds:
            return "vision"
        if "text" in kinds and "csv" in kinds:
            return "mixed"
        if "text" in kinds:
            return "text_ai"
        if "csv" in kinds:
            return "csv"
    return None


def compare_items(ref_item: dict[str, Any], gen_item: dict[str, Any]) -> CompareResult:
    qid = ref_item.get("id", "unknown")
    question = ref_item.get("question", "")
    sort_rows = not _question_has_order(question)
    evaluation_kind = _infer_reference_evaluation_kind(ref_item)

    result = CompareResult(qid)
    result.evaluation_kind = evaluation_kind
    result.ref_sql = ref_item.get("sql_code")
    result.ref_python = ref_item.get("python_code")
    result.gen_sql = gen_item.get("sql_code")
    result.gen_python = gen_item.get("python_code")
    result.pit_diagnostic_flags = pit_diagnostic_flags(
        question=question,
        sql_code=result.gen_sql or "",
        python_code=result.gen_python or "",
    )
    strict_schema = bool(ref_item.get("strict_output_schema", False))

    if evaluation_kind == "csv":
        force_ai_judge = False
    elif evaluation_kind == "text_ai":
        force_ai_judge = True
    elif evaluation_kind in {"vision", "mixed"}:
        force_ai_judge = None
    else:
        ref_plan = _get_result_plan(question, ref_item.get("python_code", "") or "", expected_count=None) if ref_item.get("python_code") else {"needs_ai_judge": None}
        gen_plan = _get_result_plan(question, gen_item.get("python_code", "") or "", expected_count=None) if gen_item.get("python_code") else {"needs_ai_judge": None}

        ref_ai = ref_plan.get("needs_ai_judge")
        gen_ai = gen_plan.get("needs_ai_judge")
        if ref_ai is True or gen_ai is True:
            force_ai_judge = True
        elif ref_ai is False and gen_ai is False:
            force_ai_judge = False
        else:
            force_ai_judge = None

    gen_hint = len(_sanitize_result_vars(gen_item.get("result_vars") or [])) or None
    ref_hint = len(_sanitize_result_vars(ref_item.get("result_vars") or [])) or None

    ref_exec = _resolve_item_execution(
        ref_item,
        question=question,
        label=f"{qid}_reference",
        expected_count=gen_hint,
        sort_rows=sort_rows,
    )
    if not ref_exec.success:
        _record_db_cooldown_for_execution_error(ref_exec.error)
        result.error_type = "ref_error"
        result.error_msg = ref_exec.error
        return result

    gen_exec = _resolve_item_execution(
        gen_item,
        question=question,
        label=f"{qid}_generated",
        expected_count=len(ref_exec.raw_results) or ref_hint,
        sort_rows=sort_rows,
    )
    if not gen_exec.success:
        _record_db_cooldown_for_execution_error(gen_exec.error)
        err = (gen_exec.error or "").lower()
        if any(k in err for k in ("syntaxerror", "parseerror", "unexpected")):
            result.error_type = "syntax_error"
        elif any(k in err for k in ("timed out", "lost connection", "can't connect")):
            result.error_type = "timeout"
        elif any(k in err for k in ("unknown column", "doesn't exist", "no such table", "invalid object name")) or "column" in err:
            result.error_type = "schema_mismatch"
        else:
            result.error_type = "gen_error"
        result.error_msg = gen_exec.error
        result.ref_result = ref_exec.raw_results
        return result

    if ref_item.get("mode") in {"python", "sql+python"} and len(ref_exec.raw_results) != len(gen_exec.raw_results):
        retried_ref = _resolve_item_execution(
            ref_item,
            question=question,
            label=f"{qid}_reference_retry",
            expected_count=len(gen_exec.raw_results),
            sort_rows=sort_rows,
        )
        if _execution_score(retried_ref, len(gen_exec.raw_results)) > _execution_score(ref_exec, len(gen_exec.raw_results)):
            ref_exec = retried_ref

    if gen_item.get("mode") in {"python", "sql+python"} and len(ref_exec.raw_results) != len(gen_exec.raw_results):
        retried_gen = _resolve_item_execution(
            gen_item,
            question=question,
            label=f"{qid}_generated_retry",
            expected_count=len(ref_exec.raw_results),
            sort_rows=sort_rows,
        )
        if _execution_score(retried_gen, len(ref_exec.raw_results)) > _execution_score(gen_exec, len(ref_exec.raw_results)):
            gen_exec = retried_gen

    result.ref_result = ref_exec.raw_results
    result.gen_result = gen_exec.raw_results

    if evaluation_kind in {"vision", "mixed"}:
        passed, score, details, error_msg = _score_visual_outputs(
            ref_item,
            ref_exec,
            gen_exec,
            question=question,
            sort_rows=sort_rows,
            decimals=_extract_compare_decimals(question),
        )
        result.score = score
        result.score_details = details
        if passed:
            result.passed = True
            result.score = 1.0
            return result
        failed_table = [
            item for item in details
            if item.get("op") == "table_ref_equal" and float(item.get("score", 0.0)) < 0.999
        ]
        if failed_table:
            table_reason = " ".join(str(item.get("reason") or "") for item in failed_table)
            result.error_type = "format_mismatch" if any(token in table_reason for token in ("结构", "数量", "列")) else "logic_mismatch"
        else:
            result.error_type = "ai_mismatch"
        result.error_msg = error_msg or "visual checklist mismatch"
        return result

    passed, error_msg = _compare_result_lists(
        ref_exec,
        gen_exec,
        qid=str(qid),
        question=question,
        sort_rows=sort_rows,
        force_ai_judge=force_ai_judge,
        strict_schema=strict_schema,
    )
    if passed:
        result.passed = True
        result.score = 1.0
        return result

    if error_msg and ("AI裁判判定不一致" in error_msg or "AI" in error_msg):
        result.error_type = "ai_mismatch"
    elif error_msg and ("类型不一致" in error_msg or "结构不一致" in error_msg or "列名不一致" in error_msg or "数量不一致" in error_msg):
        result.error_type = "format_mismatch"
    elif error_msg and "数据不一致" in error_msg:
        result.error_type = "logic_mismatch"
    else:
        result.error_type = "mismatch"
    result.error_msg = error_msg or "执行结果不一致"
    return result


def format_result_for_report(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, list):
        parts = []
        for index, item in enumerate(result, start=1):
            parts.append(f"[Result {index}]")
            parts.append(format_result_for_report(item))
        return "\n".join(parts)
    if isinstance(result, pd.DataFrame):
        return result.to_string(index=False)
    if isinstance(result, bytes):
        return "<image bytes>"
    try:
        return json.dumps(_normalize_for_text_judge(result), ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return str(result)
