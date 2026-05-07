from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_schema_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tables" in payload:
        return payload
    if isinstance(payload, list):
        return {"tables": payload}
    raise ValueError(f"Unsupported schema payload format: {path}")


def _iter_tables(path: str | Path) -> list[dict[str, Any]]:
    return list(_load_schema_payload(path).get("tables", []))


iter_tables = _iter_tables  # public alias


def render_schema_catalog(path: str | Path, *, max_columns_per_table: int = 12) -> str:
    lines: list[str] = []
    for table in _iter_tables(path):
        table_name = table.get("table_name", "")
        if not table_name:
            continue
        table_comment = (table.get("table_comment") or "").strip()
        lines.append(f"- {table_name}: {table_comment}")
        columns = table.get("columns") or []
        rendered_columns: list[str] = []
        for column in columns[:max_columns_per_table]:
            column_name = column.get("column_name", "")
            column_comment = (column.get("comment") or "").strip()
            if not column_name:
                continue
            if column_comment:
                rendered_columns.append(f"{column_name} ({column_comment})")
            else:
                rendered_columns.append(column_name)
        if rendered_columns:
            lines.append("  columns: " + ", ".join(rendered_columns))
        truncated = len(columns) - max_columns_per_table
        if truncated > 0:
            lines.append(
                f"  [注意] 该表共有 {len(columns)} 列，以上仅显示前 {max_columns_per_table} 列，"
                f"另有 {truncated} 列被省略。如需完整列定义，请调用 request_schema {table_name}"
            )
    return "\n".join(lines)


def render_schema_table_index(path: str | Path) -> str:
    table_names = [str(table.get("table_name", "")).strip() for table in _iter_tables(path)]
    table_names = [name for name in table_names if name]
    return ", ".join(table_names)


def render_focused_schema_catalog(
    path: str | Path,
    tables: list[str],
    *,
    max_columns_per_table: int = 20,
) -> str:
    matched, _ = match_requested_tables(path, tables)
    if not matched:
        return ""

    selected = {name.lower() for name in matched}
    lines: list[str] = []
    for table in _iter_tables(path):
        table_name = str(table.get("table_name", "")).strip()
        if not table_name or table_name.lower() not in selected:
            continue
        table_comment = (table.get("table_comment") or "").strip()
        lines.append(f"- {table_name}: {table_comment}")
        columns = table.get("columns") or []
        rendered_columns: list[str] = []
        for column in columns[:max_columns_per_table]:
            column_name = str(column.get("column_name", "")).strip()
            column_comment = (column.get("comment") or "").strip()
            if not column_name:
                continue
            rendered_columns.append(f"{column_name} ({column_comment})" if column_comment else column_name)
        if rendered_columns:
            lines.append("  columns: " + ", ".join(rendered_columns))
        truncated = len(columns) - max_columns_per_table
        if truncated > 0:
            lines.append(
                f"  [注意] 该表共有 {len(columns)} 列，以上仅显示前 {max_columns_per_table} 列，"
                f"另有 {truncated} 列被省略。如需完整列定义，请调用 request_schema {table_name}"
            )
    return "\n".join(lines)


def render_business_background(path: str | Path | None, *, max_chars: int = 8000) -> str:
    if path is None:
        return ""
    resolved = Path(path)
    if not resolved.exists():
        return ""
    text = resolved.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    return text[:max_chars]


def render_text_asset(path: str | Path | None, *, max_chars: int = 8000) -> str:
    if path is None:
        return ""
    resolved = Path(path)
    if not resolved.exists():
        return ""
    text = resolved.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    return text[:max_chars]


def match_requested_tables(schema_path: str | Path, requested_tables: list[str]) -> tuple[list[str], list[str]]:
    tables = _iter_tables(schema_path)
    table_index = {str(table.get("table_name", "")).lower(): str(table.get("table_name", "")) for table in tables}
    matched: list[str] = []
    missing: list[str] = []
    for raw_name in requested_tables:
        key = raw_name.strip().lower()
        if not key:
            continue
        if key in table_index:
            canonical = table_index[key]
            if canonical not in matched:
                matched.append(canonical)
        else:
            missing.append(raw_name.strip())
    return matched, missing


def render_table_name_list(path: str | Path) -> str:
    """Comma-separated list of all table names (no descriptions)."""
    names = [str(t.get("table_name", "")).strip() for t in _iter_tables(path) if t.get("table_name")]
    return ", ".join(names)


def describe_tables(path: str | Path, table_names: list[str]) -> str:
    """Return name + description for requested tables."""
    target = {n.strip().lower() for n in table_names if n.strip()}
    results: list[str] = []
    for t in _iter_tables(path):
        tn = str(t.get("table_name", "")).strip()
        if tn.lower() in target:
            desc = (t.get("table_comment") or "").strip()
            results.append(f"{tn}: {desc}" if desc else tn)
    missing = target - {r.split(":")[0].strip().lower() for r in results}
    if missing:
        results.append("未找到：" + ", ".join(missing))
    return "\n".join(results) if results else "未找到任何匹配表。"


def render_column_names(path: str | Path, table_name: str) -> str:
    """Return all column names with comments for a table."""
    target = table_name.strip().lower()
    for table in _iter_tables(path):
        if str(table.get("table_name", "")).lower() == target:
            rendered_cols = []
            for c in (table.get("columns") or []):
                col_name = str(c.get("column_name", ""))
                if not col_name:
                    continue
                col_comment = (c.get("comment") or "").strip()
                if col_comment:
                    rendered_cols.append(f"{col_name} ({col_comment})")
                else:
                    rendered_cols.append(col_name)
            return f"表 {table.get('table_name')} 共 {len(rendered_cols)} 列：\n" + ", ".join(rendered_cols)
    return f"未找到表：{table_name}"


def search_schema_columns(path: str | Path, keywords: list[str], table_scope: list[str] | None = None) -> str:
    """Search columns whose name or comment contains any keyword."""
    kws = [k.lower() for k in keywords if k.strip()]
    if not kws:
        return "请提供至少一个关键词。"
    scope = {t.lower() for t in table_scope} if table_scope else None
    results: list[str] = []
    for table in _iter_tables(path):
        tn = str(table.get("table_name", "")).strip()
        if not tn or (scope and tn.lower() not in scope):
            continue
        matched = [
            f"{c.get('column_name')}({(c.get('comment') or '').strip()})" if c.get("comment") else str(c.get("column_name"))
            for c in (table.get("columns") or [])
            if any(kw in str(c.get("column_name", "")).lower() or kw in (c.get("comment") or "").lower() for kw in kws)
        ]
        if matched:
            results.append(f"  {tn}: " + ", ".join(matched))
    return ("搜索结果：\n" + "\n".join(results)) if results else "未找到含关键词的列：" + ", ".join(keywords)


def search_tables(path: str | Path, keywords: list[str]) -> str:
    """Search tables whose name or description contains any keyword. Returns table_name: description."""
    kws = [k.lower() for k in keywords if k.strip()]
    if not kws:
        return "请提供至少一个关键词。"
    results: list[str] = []
    for table in _iter_tables(path):
        tn = str(table.get("table_name", "")).strip()
        desc = (table.get("table_comment") or "").strip()
        if any(kw in tn.lower() or kw in desc.lower() for kw in kws):
            results.append(f"{tn}: {desc}" if desc else tn)
    if not results:
        return "未找到匹配表：" + ", ".join(keywords)
    return f"找到 {len(results)} 张匹配表：\n" + "\n".join(results)


def render_selected_schema(
    schema_path: str | Path,
    requested_tables: list[str],
    *,
    max_columns_per_table: int = 80,
) -> tuple[str, list[str], list[str]]:
    matched, missing = match_requested_tables(schema_path, requested_tables)
    if not matched:
        available = sorted(
            str(table.get("table_name", "")) for table in _iter_tables(schema_path) if table.get("table_name")
        )
        preview = ", ".join(available[:40])
        if len(available) > 40:
            preview += ", ..."
        message = "No requested tables matched the schema catalog.\nAvailable tables:\n" + preview
        return message, matched, missing

    selected_payload: list[dict[str, Any]] = []
    selected_set = {name.lower() for name in matched}
    truncation_notes: list[str] = []
    for table in _iter_tables(schema_path):
        table_name = str(table.get("table_name", ""))
        if table_name.lower() not in selected_set:
            continue
        trimmed = dict(table)
        columns = list(trimmed.get("columns") or [])
        trimmed["columns"] = columns[:max_columns_per_table]
        if len(columns) > max_columns_per_table:
            trimmed["truncated_columns"] = len(columns) - max_columns_per_table
            truncation_notes.append(
                f"[注意] 表 {table_name} 共有 {len(columns)} 列，以下 JSON 中仅包含前 {max_columns_per_table} 列，"
                f"另有 {len(columns) - max_columns_per_table} 列被省略。如需完整定义，请单独调用 request_schema {table_name}"
            )
        selected_payload.append(trimmed)

    rendered = json.dumps({"tables": selected_payload}, ensure_ascii=False, indent=2)
    if truncation_notes:
        rendered = "\n".join(truncation_notes) + "\n\n" + rendered
    return rendered, matched, missing
