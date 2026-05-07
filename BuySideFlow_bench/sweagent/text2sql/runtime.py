from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path
from typing import Any

from sweagent.text2sql.schema import describe_tables, iter_tables, render_column_names, render_selected_schema, search_schema_columns, search_tables


TEXT2SQL_SCHEMA_TOOL = "request_schema"
TEXT2SQL_GET_COLUMNS_TOOL = "get_columns"
TEXT2SQL_SEARCH_COLUMNS_TOOL = "search_columns"
TEXT2SQL_SEARCH_TABLES_TOOL = "search_tables"
TEXT2SQL_DESCRIBE_TABLES_TOOL = "describe_tables"
TEXT2SQL_GET_TRADING_DAYS_TOOL = "get_trading_days"
TEXT2SQL_SUBMIT_END = "END_SUBMIT"
TEXT2SQL_RUN_CODE_TOOL = "run_code"
TEXT2SQL_RUN_CODE_END = "END_RUN_CODE"
TEXT2SQL_REVEAL_REFERENCE_RESULT_TOOL = "reveal_reference_result"
MAX_RUN_CODE_CALLS = 999
MAX_REVEAL_REFERENCE_RESULT_CALLS = 2

# per-instance call counter keyed by problem_statement id
_run_code_counts: dict[str, int] = {}
_reveal_reference_result_counts: dict[str, int] = {}
_TIMEOUT_KEYWORDS = ("timed out", "Lost connection", "Can't connect")


def reset_run_code_count(qid: str) -> None:
    _run_code_counts[qid] = 0


def get_run_code_count(qid: str) -> int:
    return _run_code_counts.get(qid, 0)


def reset_reveal_reference_result_count(qid: str) -> None:
    _reveal_reference_result_counts[qid] = 0


def get_reveal_reference_result_count(qid: str) -> int:
    return _reveal_reference_result_counts.get(qid, 0)


def parse_schema_request_action(action: str) -> list[str] | None:
    try:
        parts = shlex.split(action.strip())
    except ValueError:
        return None
    if not parts or parts[0] != TEXT2SQL_SCHEMA_TOOL:
        return None
    if len(parts) < 2:
        return []
    raw = " ".join(parts[1:])
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_submit_action(action: str) -> str | None:
    stripped = action.strip()
    prefix = "submit"
    suffix = TEXT2SQL_SUBMIT_END
    if not stripped.startswith(prefix):
        return None
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[0].strip() != prefix or lines[-1].strip() != suffix:
        return None
    payload = "\n".join(lines[1:-1]).strip()
    return payload or None


def validate_submission_payload(payload: str) -> str | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return f"submission is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return "submission must be a single JSON object"
    for key in ("mode", "sql_code", "python_code", "result_vars"):
        if key not in data:
            return f"missing required key: {key}"
    if data["mode"] not in {"sql", "python", "sql+python"}:
        return "mode must be one of: sql, python, sql+python"
    if not isinstance(data["result_vars"], list):
        return "result_vars must be a JSON array"
    return None


def parse_describe_tables_action(action: str) -> list[str] | None:
    """Return table names if action is `describe_tables <t1>[,<t2>...]`, else None."""
    try:
        parts = shlex.split(action.strip())
    except ValueError:
        return None
    if not parts or parts[0] != TEXT2SQL_DESCRIBE_TABLES_TOOL:
        return None
    if len(parts) < 2:
        return []
    raw = " ".join(parts[1:])
    return [t.strip() for t in raw.split(",") if t.strip()]


def build_describe_tables_observation(problem_statement: Any, table_names: list[str]) -> str:
    schema_path = problem_statement.extra_fields.get("schema_path")
    if not schema_path:
        return "Schema path is not configured for this task."
    return describe_tables(Path(schema_path), table_names)


def parse_get_columns_action(action: str) -> str | None:
    """Return table name if action is `get_columns <table>`, else None."""
    try:
        parts = shlex.split(action.strip())
    except ValueError:
        return None
    if len(parts) >= 2 and parts[0] == TEXT2SQL_GET_COLUMNS_TOOL:
        return " ".join(parts[1:]).strip()
    return None


def parse_search_columns_action(action: str) -> tuple[list[str], list[str]] | None:
    """Return (keywords, table_scope) if action is `search_columns`, else None.

    Syntax: search_columns <keyword1>[,<keyword2>...] [in <table1>[,<table2>...]]
    """
    stripped = action.strip()
    if not stripped.startswith(TEXT2SQL_SEARCH_COLUMNS_TOOL):
        return None
    rest = stripped[len(TEXT2SQL_SEARCH_COLUMNS_TOOL):].strip()
    if not rest:
        return None
    table_scope: list[str] = []
    if " in " in rest:
        kw_part, tbl_part = rest.split(" in ", 1)
        table_scope = [t.strip() for t in tbl_part.split(",") if t.strip()]
    else:
        kw_part = rest
    keywords = [k.strip() for k in kw_part.split(",") if k.strip()]
    if not keywords:
        return None
    return keywords, table_scope


def build_get_columns_observation(problem_statement: Any, table_name: str) -> str:
    schema_path = problem_statement.extra_fields.get("schema_path")
    if not schema_path:
        return "Schema path is not configured for this task."
    result = render_column_names(Path(schema_path), table_name)
    return result


def build_search_columns_observation(problem_statement: Any, keywords: list[str], table_scope: list[str]) -> str:
    schema_path = problem_statement.extra_fields.get("schema_path")
    if not schema_path:
        return "Schema path is not configured for this task."
    return search_schema_columns(Path(schema_path), keywords, table_scope or None)


def parse_search_tables_action(action: str) -> list[str] | None:
    """Return keywords if action is `search_tables <kw1>[,<kw2>...]`, else None."""
    stripped = action.strip()
    if not stripped.startswith(TEXT2SQL_SEARCH_TABLES_TOOL):
        return None
    rest = stripped[len(TEXT2SQL_SEARCH_TABLES_TOOL):].strip()
    if not rest:
        return None
    return [k.strip() for k in rest.split(",") if k.strip()]


def build_search_tables_observation(problem_statement: Any, keywords: list[str]) -> str:
    schema_path = problem_statement.extra_fields.get("schema_path")
    if not schema_path:
        return "Schema path is not configured for this task."
    return search_tables(Path(schema_path), keywords)


def parse_get_trading_days_action(action: str) -> tuple[str, str, str] | None:
    """Return (start_date, end_date, markets) if action is get_trading_days, else None.

    Syntax: get_trading_days <start_date> <end_date> [market=83,90]
    Example: get_trading_days 2023-01-01 2025-12-31 market=83,90
    """
    try:
        parts = action.strip().split()
    except Exception:
        return None
    if not parts or parts[0] != TEXT2SQL_GET_TRADING_DAYS_TOOL:
        return None
    if len(parts) < 3:
        return None
    start_date = parts[1].strip()
    end_date = parts[2].strip()
    markets = "83,90"
    for part in parts[3:]:
        if part.lower().startswith("market="):
            markets = part.split("=", 1)[1].strip()
        elif part.strip():
            markets = part.strip()
    return start_date, end_date, markets


def build_get_trading_days_observation(start_date: str, end_date: str, markets: str) -> str:
    from sweagent.text2sql.db_connector import query_to_dataframe

    market_list = ", ".join(f"'{m.strip()}'" for m in markets.split(",") if m.strip())
    sql = (
        f"SELECT TRADINGDATE FROM qt_tradingdaynew "
        f"WHERE IFTRADINGDAY = 1 AND SECUMARKET IN ({market_list}) "
        f"AND TRADINGDATE >= '{start_date}' AND TRADINGDATE <= '{end_date}' "
        f"ORDER BY TRADINGDATE"
    )
    try:
        df = query_to_dataframe(sql)
    except Exception as exc:
        return f"查询交易日历失败: {exc}"

    if df.empty:
        return (
            f"在 {start_date} ~ {end_date} 范围内未找到交易日（market={markets}）。\n"
            f"请检查日期范围是否正确，或尝试扩大查询区间。"
        )

    dates = df["TRADINGDATE"].tolist()
    preview = ", ".join(str(d) for d in dates[:10])
    if len(dates) > 10:
        preview += f", ... 共 {len(dates)} 个交易日"
    return (
        f"交易日历查询结果（{start_date} ~ {end_date}, market={markets}）：\n"
        f"共 {len(dates)} 个交易日\n"
        f"前 10 个：{preview}\n"
        f"完整列表：{', '.join(str(d) for d in dates)}"
    )


def build_schema_observation(problem_statement: Any, requested_tables: list[str]) -> str:
    schema_path = problem_statement.extra_fields.get("schema_path")
    if not schema_path:
        return "Schema path is not configured for this task."
    rendered, matched, missing = render_selected_schema(
        Path(schema_path),
        requested_tables,
        max_columns_per_table=int(problem_statement.extra_fields.get("schema_columns_per_table", 80)),
    )

    lines = [
        "Schema request processed.",
        f"Requested tables: {', '.join(requested_tables) if requested_tables else '(none)'}",
        f"Matched tables: {', '.join(matched) if matched else '(none)'}",
    ]
    if missing:
        lines.append(f"Unmatched tables: {', '.join(missing)}")
    lines.append("")
    lines.append("Selected schema:")
    lines.append(rendered)
    lines.append("")
    lines.append(
        "When you are ready, call submit with one JSON object containing mode, sql_code, python_code, result_vars."
    )
    return "\n".join(lines)


def parse_run_code_action(action: str) -> str | None:
    """Return payload string if action is a run_code block, else None."""
    stripped = action.strip()
    if not stripped.startswith(TEXT2SQL_RUN_CODE_TOOL):
        return None
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[0].strip() != TEXT2SQL_RUN_CODE_TOOL or lines[-1].strip() != TEXT2SQL_RUN_CODE_END:
        return None
    payload = "\n".join(lines[1:-1]).strip()
    return payload or None


def parse_reveal_reference_result_action(action: str) -> str | None:
    try:
        parts = shlex.split(action.strip())
    except ValueError:
        return None
    if len(parts) == 1 and parts[0] == TEXT2SQL_REVEAL_REFERENCE_RESULT_TOOL:
        return TEXT2SQL_REVEAL_REFERENCE_RESULT_TOOL
    return None


def _is_timeout_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(keyword.lower() in lowered for keyword in _TIMEOUT_KEYWORDS)


def _truncate_text(text: Any, *, max_chars: int = 4_000) -> str:
    rendered = str(text).strip()
    if len(rendered) <= max_chars:
        return rendered
    omitted = len(rendered) - max_chars
    return f"{rendered[:max_chars]}\n...[truncated {omitted} chars]"


def _render_result_value(value: Any, *, max_chars: int = 4_000) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return _truncate_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), max_chars=max_chars)
        except TypeError:
            pass
    return _truncate_text(value, max_chars=max_chars)


def _render_image_markdown(image_bytes: bytes, *, alt_text: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"![{alt_text}](data:image/png;base64,{encoded})"


_SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "WITH", "JOIN", "INNER", "LEFT", "RIGHT", "OUTER",
    "ON", "AND", "OR", "NOT", "NULL", "AS", "GROUP", "BY", "ORDER",
    "HAVING", "LIMIT", "UNION", "ALL", "DISTINCT", "CASE", "WHEN", "THEN",
    "ELSE", "END", "ASC", "DESC", "BETWEEN", "LIKE", "IN", "EXISTS",
    "COUNT", "SUM", "AVG", "MAX", "MIN", "ROW_NUMBER", "OVER", "PARTITION",
    "CAST", "COALESCE", "IFNULL", "DATE", "YEAR", "MONTH", "DAY",
    "TRUE", "FALSE", "INTERVAL", "IS", "SQL", "TABLE", "COLUMN",
    "DATE_SUB", "DATEDIFF", "DATE_ADD", "NOW", "CURDATE", "CONCAT",
    "SUBSTRING", "TRIM", "UPPER", "LOWER", "ROUND", "ABS", "FLOOR",
    "CEIL", "RANK", "DENSE_RANK", "LEAD", "LAG", "FIRST_VALUE", "LAST_VALUE",
    "INTERVAL", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
}


def _extract_cte_names(sql_code: str) -> set[str]:
    """Extract CTE names introduced via WITH ... AS (...)."""
    import re

    if not sql_code or not sql_code.strip():
        return set()

    return {
        match.group(1)
        for match in re.finditer(
            r"(?is)(?:\bWITH\b|,)\s*([A-Za-z_][A-Za-z0-9_$]*)\s+\bAS\b\s*\(",
            sql_code,
        )
    }


def _extract_sql_reference_metadata_fallback(
    sql_code: str,
    cte_names: set[str],
) -> tuple[set[str], set[str], set[str], set[str]]:
    import re

    tables: set[str] = set()
    columns: set[str] = set()
    aliases: set[str] = set()
    relation_aliases: set[str] = set()

    def _is_identifier(token_str: str) -> bool:
        s = token_str.strip()
        if not s or s == "*" or s.startswith("%") or s.startswith("'") or s.startswith('"'):
            return False
        if s.upper() in _SQL_KEYWORDS:
            return False
        try:
            float(s)
            return False
        except ValueError:
            pass
        return True

    relation_pattern = re.compile(
        r"""(?is)
        \b(?:FROM|JOIN|INNER\s+JOIN|LEFT(?:\s+OUTER)?\s+JOIN|RIGHT(?:\s+OUTER)?\s+JOIN|FULL(?:\s+OUTER)?\s+JOIN|CROSS\s+JOIN)\b
        \s+
        ([A-Za-z_][A-Za-z0-9_$]*)
        (?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_$]*))?
        (?=
            \s*(?:,|\)|$|
            \bON\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\bHAVING\b|\bLIMIT\b|\bUNION\b|
            \bJOIN\b|\bINNER\b|\bLEFT\b|\bRIGHT\b|\bFULL\b|\bCROSS\b)
        )
        """,
        re.VERBOSE,
    )
    generic_alias_pattern = re.compile(r"(?is)\bAS\s+([A-Za-z_][A-Za-z0-9_$]*)\b")
    qualified_identifier_pattern = re.compile(r"(?i)\b([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z_][A-Za-z0-9_$]*)\b")
    bare_identifier_pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$]*)\b")

    for match in relation_pattern.finditer(sql_code):
        name = match.group(1)
        alias = match.group(2)
        if name and _is_identifier(name) and name not in cte_names:
            tables.add(name)
        if alias and _is_identifier(alias):
            aliases.add(alias)
            relation_aliases.add(alias)

    for match in generic_alias_pattern.finditer(sql_code):
        alias = match.group(1)
        if alias and _is_identifier(alias):
            aliases.add(alias)

    for match in qualified_identifier_pattern.finditer(sql_code):
        column = match.group(2)
        if column and _is_identifier(column):
            columns.add(column)

    filtered_names = {name.lower() for name in tables | aliases | cte_names}
    for match in bare_identifier_pattern.finditer(sql_code):
        token = match.group(1)
        start, end = match.span(1)
        if start > 0 and sql_code[start - 1] == ".":
            continue
        if end < len(sql_code) and sql_code[end] == ".":
            continue
        lookahead = end
        while lookahead < len(sql_code) and sql_code[lookahead].isspace():
            lookahead += 1
        if lookahead < len(sql_code) and sql_code[lookahead] == "(":
            continue
        if not _is_identifier(token):
            continue
        if token.lower() in filtered_names:
            continue
        columns.add(token)

    tables -= cte_names
    tables -= relation_aliases
    columns -= aliases
    columns -= tables
    columns -= cte_names
    return tables, columns, aliases, cte_names


def _extract_sql_reference_metadata(sql_code: str) -> tuple[set[str], set[str], set[str], set[str]]:
    """Extract SQL tables, columns, aliases, and CTE names."""
    if not sql_code or not sql_code.strip():
        return set(), set(), set(), set()

    cte_names = _extract_cte_names(sql_code)
    fallback_tables, fallback_columns, fallback_aliases, _ = _extract_sql_reference_metadata_fallback(sql_code, cte_names)

    try:
        import sqlparse
        from sqlparse.sql import Identifier, IdentifierList, Function
    except ImportError:
        return fallback_tables, fallback_columns, fallback_aliases, cte_names

    parsed = sqlparse.parse(sql_code)
    tables: set[str] = set()
    columns: set[str] = set()
    aliases: set[str] = set()
    relation_aliases: set[str] = set()

    def _is_identifier(token_str: str) -> bool:
        s = token_str.strip()
        if not s or s == "*" or s.startswith("%") or s.startswith("'") or s.startswith('"'):
            return False
        if s.upper() in _SQL_KEYWORDS:
            return False
        try:
            float(s)
            return False
        except ValueError:
            pass
        return True

    def _collect_identifiers(token: Any, into_tables: bool = False) -> None:
        if isinstance(token, Identifier):
            real = token.get_real_name()
            alias = token.get_alias()
            if real and _is_identifier(real):
                if into_tables:
                    tables.add(real)
                else:
                    columns.add(real)
            if alias and _is_identifier(alias):
                aliases.add(alias)
                if into_tables:
                    relation_aliases.add(alias)
        elif isinstance(token, IdentifierList):
            for ident in token.get_identifiers():
                _collect_identifiers(ident, into_tables=into_tables)
        elif getattr(token, "is_group", False):
            for t in token.tokens:
                _collect_identifiers(t, into_tables=into_tables)

    for stmt in parsed:
        tokens = list(stmt.tokens)
        i = 0
        while i < len(tokens):
            token = tokens[i]
            val = str(token).strip().upper()
            if val in (
                "FROM", "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN",
                "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL JOIN", "CROSS JOIN",
            ):
                i += 1
                while i < len(tokens):
                    t = tokens[i]
                    if str(t).strip():
                        _collect_identifiers(t, into_tables=True)
                        break
                    i += 1
            elif isinstance(token, Function):
                for t in token.tokens[1:]:
                    _collect_identifiers(t, into_tables=False)
            else:
                _collect_identifiers(token, into_tables=False)
            i += 1

    aliases |= relation_aliases
    # sqlparse often misses relations nested inside CTE bodies and subqueries. Keep its
    # identifier precision for columns, but backfill relation names and aliases from the
    # regex fallback so real base tables are not lost.
    tables |= fallback_tables
    aliases |= fallback_aliases
    tables -= cte_names
    tables -= relation_aliases
    columns -= aliases
    columns -= tables
    columns -= cte_names
    if not tables and not columns:
        return _extract_sql_reference_metadata_fallback(sql_code, cte_names)
    return tables, columns, aliases, cte_names


def _extract_sql_references(sql_code: str) -> tuple[set[str], set[str]]:
    """Extract table names and column names referenced in SQL code.

    Returns (tables, columns) where:
    - tables: real table names found in FROM/JOIN clauses.
    - columns: bare column names (aliases and table names are filtered out).
    """
    tables, columns, _, _ = _extract_sql_reference_metadata(sql_code)
    return tables, columns


def _validate_sql_references(sql_code: str, schema_path: str | Path) -> list[str]:
    """Validate that tables and columns referenced in SQL exist in the schema.

    Returns a list of warning messages. Empty list means no issues found.
    """
    if not sql_code or not sql_code.strip():
        return []

    try:
        tables, columns, _, _ = _extract_sql_reference_metadata(sql_code)
    except Exception:
        return []

    if not tables and not columns:
        return []

    schema_table_names: set[str] = set()
    schema_column_names: set[str] = set()
    for t in iter_tables(schema_path):
        tn = str(t.get("table_name", "")).strip()
        if tn:
            schema_table_names.add(tn.lower())
        for c in (t.get("columns") or []):
            cn = str(c.get("column_name", "")).strip()
            if cn:
                schema_column_names.add(cn.lower())

    warnings: list[str] = []
    for table in tables:
        if table.lower() not in schema_table_names:
            warnings.append(
                f"表 '{table}' 未在 schema 目录中找到。"
                f"请调用 search_tables 或 describe_tables 确认正确的表名。"
            )
    for column in columns:
        if column.lower() not in schema_column_names:
            warnings.append(
                f"列 '{column}' 未在 schema 目录中找到。"
                f"请调用 search_columns、get_columns 或 request_schema 确认正确的列名。"
            )

    return warnings


_EMPTY_RESULT_DIAGNOSIS = """\
【结果为空，请按以下顺序排查】
1. 日期条件是否过严？（如 as_of_date 被错误硬编码、观察日与数据库实际数据范围不匹配）
2. 表连接字段是否匹配？（如 INNERCODE 在不同表中类型不一致导致连接失败）
3. 筛选条件是否互斥？（如数值过滤条件与表中实际分布矛盾，导致全部记录被剔除）
4. 是否用了错误的索引代码、行业代码或证券分类代码？
5. 字段名是否正确？（如混淆了 ROIC 与 ROICTTM、ENDDATE 与 INFOPUBLDATE 等）
排查后修改代码并重新 run_code，禁止直接 submit 空结果。"""


def _build_dataframe_meta(df: Any) -> list[str]:
    """Build metadata lines for a DataFrame result."""
    import pandas as pd
    lines: list[str] = []
    lines.append(f"行数：{len(df)}，列数：{len(df.columns)}")
    lines.append(f"列名：{list(df.columns)}")

    # Date range detection
    date_cols = [c for c in df.columns if "date" in str(c).lower() or "day" in str(c).lower()]
    for dc in date_cols:
        try:
            col_data = pd.to_datetime(df[dc], errors="coerce")
            valid = col_data.dropna()
            if len(valid) > 0:
                lines.append(f"列 '{dc}' 日期范围：{valid.min()} ~ {valid.max()}")
        except Exception:
            pass

    # Numeric summary for columns with reasonable range
    for c in df.columns:
        try:
            numeric = pd.to_numeric(df[c], errors="coerce").dropna()
            if len(numeric) == 0:
                continue
            # Skip ID-like columns (all integer, max < 1e8) and flag columns (0/1)
            if numeric.max() < 1e8 and (numeric == numeric.astype(int)).all():
                continue
            lines.append(
                f"列 '{c}' 数值统计：min={numeric.min():.4g}, max={numeric.max():.4g}, mean={numeric.mean():.4g}"
            )
        except Exception:
            pass

    return lines


def build_reveal_reference_result_observation(problem_statement: Any) -> str:
    import pandas as pd
    from sweagent.text2sql.evaluator import _question_has_order, _resolve_item_execution

    qid = str(getattr(problem_statement, "id", "unknown"))
    count = _reveal_reference_result_counts.get(qid, 0)

    if count >= MAX_REVEAL_REFERENCE_RESULT_CALLS:
        return (
            f"reveal_reference_result call limit reached ({MAX_REVEAL_REFERENCE_RESULT_CALLS}). "
            "Use the reference result you already saw to continue."
        )

    question = str(getattr(problem_statement, "question", "") or "")
    ref_item = {
        "id": qid,
        "mode": getattr(problem_statement, "mode", "sql"),
        "sql_code": getattr(problem_statement, "reference_sql", "") or "",
        "python_code": getattr(problem_statement, "reference_python", "") or "",
        "result_vars": list(getattr(problem_statement, "result_vars", []) or []),
        "reference_results": list(getattr(problem_statement, "reference_results", []) or []),
        "reference_artifact_paths": list(problem_statement.extra_fields.get("reference_artifact_paths", []) or []),
        "evaluation_kind": problem_statement.extra_fields.get("evaluation_kind", ""),
    }

    try:
        exec_result = _resolve_item_execution(
            ref_item,
            question=question,
            label=f"{qid}_reference_reveal_{count + 1}",
            expected_count=None,
            sort_rows=not _question_has_order(question),
        )
    except Exception as exc:
        error_text = str(exc)
        if _is_timeout_error(error_text):
            return "[reveal_reference_result timed out; quota not consumed]\n" f"Execution error: {error_text}"
        _reveal_reference_result_counts[qid] = count + 1
        remaining = MAX_REVEAL_REFERENCE_RESULT_CALLS - _reveal_reference_result_counts[qid]
        return (
            f"[reveal_reference_result {_reveal_reference_result_counts[qid]}/"
            f"{MAX_REVEAL_REFERENCE_RESULT_CALLS}, remaining {remaining}]\n"
            f"Reference execution failed: {error_text}"
        )

    if _is_timeout_error(exec_result.error):
        return "[reveal_reference_result timed out; quota not consumed]\n" f"Execution error: {exec_result.error}"

    _reveal_reference_result_counts[qid] = count + 1
    call_no = _reveal_reference_result_counts[qid]
    remaining = MAX_REVEAL_REFERENCE_RESULT_CALLS - call_no

    out = [f"[reveal_reference_result {call_no}/{MAX_REVEAL_REFERENCE_RESULT_CALLS}, remaining {remaining}]"]
    if not exec_result.success:
        out.append(f"Reference execution failed: {exec_result.error}")
        if exec_result.stdout_text:
            out.append("Captured stdout:")
            out.append(_truncate_text(exec_result.stdout_text))
        return "\n".join(out)

    if not exec_result.raw_results:
        if exec_result.stdout_text:
            out.append("Reference execution succeeded but produced no extracted result objects.")
            out.append("Captured stdout:")
            out.append(_truncate_text(exec_result.stdout_text))
        else:
            out.append("Reference execution succeeded but returned no extracted result objects.")
        return "\n".join(out)

    out.append("Reference-side evaluator result:")
    for index, (raw, result_type) in enumerate(zip(exec_result.raw_results, exec_result.result_types, strict=False), start=1):
        if result_type == "dataframe" and isinstance(raw, pd.DataFrame):
            shown_rows = min(len(raw), 20)
            out.append(
                f"Result {index} (DataFrame, {len(raw)} rows x {len(raw.columns)} cols, showing {shown_rows} rows):"
            )
            out.append(raw.head(20).to_string(index=False))
            continue
        if result_type == "image" or isinstance(raw, bytes):
            out.append(f"Result {index} (image):")
            out.append(_render_image_markdown(raw, alt_text=f"reference_result_{index}"))
            continue
        out.append(f"Result {index}:")
        out.append(_render_result_value(raw))

    if exec_result.stdout_text:
        out.append("Captured stdout:")
        out.append(_truncate_text(exec_result.stdout_text))
    out.append(
        "\n【注意】以上为参考标准答案的真实执行结果。"
        "请仔细对照你的代码逻辑（字段选择、过滤条件、计算公式、行数、数值范围等），"
        "若发现不一致，必须修正后再调用 run_code 验证，最后才能 submit。"
        "在消融实验模式下，未调用 reveal_reference_result 就直接 submit 会被系统拒绝。"
    )
    out[-1] = (
        "\n[Note] The results above are the actual execution output of the reference solution.\n"
        "Compare them against your latest run_code result carefully, including selected columns, "
        "filters, join logic, formulas, row count, ordering, and key values.\n"
        "If you find any mismatch, revise your SQL/Python, rerun run_code, and re-check before submit.\n"
        "In ablation mode, submit will still be rejected if you never called reveal_reference_result first."
    )
    return "\n".join(out)


def _load_run_code_payload(payload: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return None, f"run_code payload 不是合法 JSON: {exc}"

    if isinstance(data, str):
        sql_code = data.strip()
        if not sql_code:
            return None, "run_code payload 是空字符串，请提供 SQL 或 JSON 对象。"
        return {"mode": "sql", "sql_code": sql_code, "python_code": "", "result_vars": []}, None

    if not isinstance(data, dict):
        return None, f"run_code payload 必须是 JSON 对象或 SQL 字符串，实际类型为 {type(data).__name__}。"

    return data, None


def build_run_code_observation(problem_statement: Any, payload: str) -> str:
    import pandas as pd
    from sweagent.text2sql.evaluator import execute_code

    qid = str(getattr(problem_statement, "id", "unknown"))
    count = _run_code_counts.get(qid, 0)

    if count >= MAX_RUN_CODE_CALLS:
        return f"已达最大调用次数（{MAX_RUN_CODE_CALLS} 次），请直接 submit。请确保 submit 的代码已修正最后一次 run_code 中发现的所有问题。"

    data, payload_error = _load_run_code_payload(payload)
    if payload_error:
        return payload_error

    mode = data.get("mode", "sql")
    sql_code = data.get("sql_code") or ""
    python_code = data.get("python_code") or ""
    result_vars = data.get("result_vars") or []

    # 静态校验：表名/列名是否在 schema 中存在
    schema_path = problem_statement.extra_fields.get("schema_path")
    validation_warnings = []
    if schema_path and sql_code:
        try:
            validation_warnings = _validate_sql_references(sql_code, schema_path)
        except Exception:
            pass

    exec_result = execute_code(
        mode=mode,
        sql_code=sql_code,
        python_code=python_code,
        result_vars=result_vars,
        label=f"{qid}_probe_{count + 1}",
        force_subprocess=True,
    )

    # 超时不消耗次数
    is_timeout = not exec_result.success and _is_timeout_error(exec_result.error)

    if is_timeout:
        out = ["[run_code 超时，本次不计入调用次数，请简化查询后重试]"]
        out.append(f"执行出错: {exec_result.error}")
        return "\n".join(out)

    _run_code_counts[qid] = count + 1
    call_no = _run_code_counts[qid]
    remaining = MAX_RUN_CODE_CALLS - call_no

    header = f"[run_code 第 {call_no}/{MAX_RUN_CODE_CALLS} 次，剩余 {remaining} 次]"
    out = [header]
    if validation_warnings:
        out.append("【代码静态检查警告】")
        for w in validation_warnings:
            out.append(f"  - {w}")
        out.append(
            "请务必确认以上表名/列名正确，否则执行可能出错。"
            "如不确定，请回到 describe_tables / get_columns / search_columns 进行核实。"
        )
        out.append("")
    if not exec_result.success:
        out.append(f"执行出错: {exec_result.error}")
        return "\n".join(out)

    if not exec_result.raw_results:
        if exec_result.stdout_text:
            out.append("执行成功，捕获到打印输出：")
            out.append(exec_result.stdout_text)
        else:
            out.append("执行成功，无结果输出。")
        return "\n".join(out)

    for i, (raw, rtype) in enumerate(zip(exec_result.raw_results, exec_result.result_types)):
        if rtype == "dataframe" and isinstance(raw, pd.DataFrame):
            if len(raw) == 0:
                out.append(f"结果 {i + 1}（DataFrame，共 0 行 × {len(raw.columns)} 列）:")
                out.append(_EMPTY_RESULT_DIAGNOSIS)
            else:
                out.append(f"结果 {i + 1}（DataFrame，显示前 20 行）:")
                out.extend(_build_dataframe_meta(raw))
                out.append("")
                out.append(raw.head(20).to_string())
        elif rtype == "image":
            out.append(f"结果 {i + 1}: 图片输出（不显示）")
        else:
            out.append(f"结果 {i + 1}: {raw}")

    if exec_result.stdout_text:
        out.append("")
        out.append("执行成功，捕获到打印输出：")
        out.append(_truncate_text(exec_result.stdout_text))

    result = "\n".join(out)
    # 限制单次 observation 总长度，防止历史膨胀导致 context window 超限
    MAX_OBS_LEN = 15000
    if len(result) > MAX_OBS_LEN:
        result = result[:MAX_OBS_LEN] + (
            f"\n\n[观测结果已截断，原长度 {len(result)} 字符，仅保留前 {MAX_OBS_LEN} 字符。"
            "如需要查看完整输出，请缩小查询范围或减少返回列数后重新 run_code。]"
        )
    return result
