"""Deterministic, field-preserving comparison of structured task outputs.

Rules are reviewed task data, never inferred from candidate values. In their
absence only literal field identities and the existing precision apply.

Presentation normalization (TEXT2SQL_PRESENTATION_NORMALIZATION=0 restores the
literal comparison): a strict column order is checked on the field identities
_align resolves (a header may omit or restate the declared unit of the same
dimension, or use other bracket widths), not on raw header strings; in columns
without a declared unit, a midnight timestamp is its date and a percent marker
(an "x%" cell or a "(%)" header) is presentation, so x is compared as written.
Bracket width inside a text value is presentation too: （LOF） and (LOF) are the same name.
A multiple marker ("(倍)") is presentation like the percent marker when the reference field declares no unit, and a
header that repeats the question's own wording for a field, note in brackets included (高相关基金对（A代码-B代码）,
剩余期限（年）), is that field; values are then compared as written.
Unknown, ambiguous or duplicate headers, other units, values, row counts and
row order are compared as before.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
import hashlib
import json
from itertools import product
import os
from pathlib import Path
import re
from typing import Any

import pandas as pd


class Difference(ValueError):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")


def source_fingerprint(record: dict) -> str:
    source = {key: record.get(key) for key in ("instruction", "question", "query", "inputs", "output_contract", "evaluation_contract", "strict_output_schema", "column_order_matters", "single_numeric_output")}
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_comparison_rules(root: Path, qid: str, record: dict) -> dict | None:
    """Bind reviewed rules to the source version; never apply them by id alone."""
    path = root / "comparison_rules.json"
    entry = json.loads(path.read_text(encoding="utf-8")).get(qid) if path.exists() else None
    if entry is not None:
        if entry.get("source_sha256") != source_fingerprint(record):
            raise ValueError(f"Stale comparison rules for {qid}: question or contract changed")
        rules = entry["rules"]
    else:
        rules = record.get("comparison_rules")
    if rules is not None:
        validate_rules(rules)
        rules = deepcopy(rules)
        for table in rules["tables"]:
            if not (filename := table.get("raw_artifact")):
                continue
            if Path(filename).name != filename or filename not in rules.get("raw_artifacts", []):
                raise ValueError(f"Invalid raw artifact for {qid}: {filename!r}")
            raw_path = root / "results" / qid / filename
            if not raw_path.is_file():
                raise ValueError(f"Missing raw reference for {qid}: {filename}")
            if table.get("raw_sha256") != hashlib.sha256(raw_path.read_bytes()).hexdigest():
                raise ValueError(f"Raw reference fingerprint mismatch for {qid}: {filename}")
            for source_name, digest in table.get("raw_source_sha256", {}).items():
                source_path = root / "results" / qid / source_name
                if source_name not in {"refer.py", "refer.sql"} or not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Stale raw reference source for {qid}: {source_name}")
            frame = pd.read_csv(raw_path, dtype=object)
            rules_frame = frame.where(frame.notna(), None)
            table["raw_reference"] = {"columns": list(frame.columns), "rows": rules_frame.values.tolist()}
    return rules


# Factors express a unit in its dimension's base scale. Percentage points are
# changes, not levels: a percent sign cannot silently turn one into the other.
UNITS = {
    "percent": ("rate", Decimal("0.01"), ["%", "％", "percent", "pct", "百分比"]),
    "ratio": ("rate", Decimal(1), ["ratio", "比例"]),
    "percentage_point": ("rate_change", Decimal("0.01"), ["百分点", "pp", "percentage points"]),
    "bp": ("rate_change", Decimal("0.0001"), ["bp", "bps", "基点"]),
    "CNY": ("currency", Decimal(1), ["元", "cny"]),
    "CNY_1e4": ("currency", Decimal("10000"), ["万元"]),
    "CNY_1e8": ("currency", Decimal("100000000"), ["亿元"]),
    "CNY_per_share": ("per_share", Decimal(1), ["元/股", "元每股"]),
    "year": ("duration", Decimal(12), ["年", "年期", "years", "year", "Y"]),
    "month": ("duration", Decimal(1), ["月", "months", "month"]),
    "day": ("days", Decimal(1), ["天", "日", "交易日", "days", "day"]),
    "multiple": ("multiple", Decimal(1), ["倍", "次", "multiple", "times"]),
    "dimensionless": ("dimensionless", Decimal(1), ["无量纲", "dimensionless"]),
}

_MIDNIGHT = re.compile(r"(\d{4}-\d{2}-\d{2})[ T]00:00:00(?:\.0+)?")
_PERCENT_TEXT = re.compile(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*[%％]")
_BRACKET_WIDTH = str.maketrans("（）", "()")


def _presentation_normalization() -> bool:
    return os.getenv("TEXT2SQL_PRESENTATION_NORMALIZATION", "1") != "0"


def validate_rules(rules: dict) -> None:
    if not isinstance(rules, dict) or not isinstance(rules.get("tables"), list) or not rules["tables"]:
        raise Difference("reference", "comparison_rules.tables must be a nonempty list")
    for table in rules["tables"]:
        columns = table.get("columns") if isinstance(table, dict) else None
        if not isinstance(columns, list) or not columns:
            raise Difference("reference", "each table requires fields")
        names = [c.get("name") for c in columns if isinstance(c, dict)]
        if len(names) != len(columns) or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
            raise Difference("reference", "invalid or duplicate contract fields")
        for column in columns:
            if column.get("type", "auto") not in {"auto", "number", "code", "text", "date", "date_set", "month", "quarter", "quarter_set", "state"}:
                raise Difference("reference", f"unknown field type for {column['name']!r}")
            if column.get("unit") is not None and column["unit"] not in UNITS:
                raise Difference("reference", f"unknown unit for {column['name']!r}")
            if "decimals" in column and (type(column["decimals"]) is not int or not 0 <= column["decimals"] <= 12):
                raise Difference("reference", f"invalid precision for {column['name']!r}")
            if not isinstance(column.get("aliases", []), list):
                raise Difference("reference", f"invalid aliases for {column['name']!r}")
            if any(unit not in UNITS for unit in column.get("alias_units", {}).values()):
                raise Difference("reference", f"unknown alias unit for {column['name']!r}")
            if column.get("entity_key") and column["entity_key"] not in names:
                raise Difference("reference", "unknown entity key")
        for key in table.get("order_by", []):
            if key.get("field") not in names or not isinstance(key.get("ascending", True), bool):
                raise Difference("reference", "invalid sort field or direction")
            if key.get("subtract") and key["subtract"] not in names:
                raise Difference("reference", "invalid secondary sort field")
            if key.get("transform") not in {None, "abs"}:
                raise Difference("reference", "unsupported sort transform")
        if table.get("raw_reference") or table.get("raw_artifact"):
            keys = table.get("raw_key", [])
            if not keys or any(k not in names for k in keys):
                raise Difference("reference", "raw references require explicit field keys")
    if spec := rules.get("sections"):
        if (not isinstance(spec, dict) or not isinstance(spec.get("column"), str)
                or not isinstance(spec.get("aliases", []), list)
                or not isinstance(spec.get("labels"), dict) or not spec["labels"]
                or any(not isinstance(k, str) or type(v) is not int or not 0 <= v < len(rules["tables"])
                       for k, v in spec["labels"].items())):
            raise Difference("reference", "invalid section-to-table mapping")
    fields = {c["name"]: c for table in rules["tables"] for c in table["columns"]}
    for spec in rules.get("header_dates", []):
        # A date may be stated in a reviewed value-column label, never inferred
        # from reference values or silently supplied for an undated column.
        if (not isinstance(spec, dict) or not isinstance(spec.get("column"), str)
                or fields.get(spec.get("field"), {}).get("type") != "date"
                or not isinstance(spec.get("date"), str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", spec["date"])
                or spec["date"].replace("-", "") not in re.sub(r"\D", "", spec["column"])):
            raise Difference("reference", "invalid date-bearing header")
        try:
            pd.Timestamp(spec["date"])
        except ValueError as exc:
            raise Difference("reference", "invalid header date") from exc
        matches = [c for c in fields.values() if name(spec["column"]) in
                   {name(a) for a in [c["name"], *c.get("aliases", [])]}]
        if len(matches) != 1 or matches[0].get("type") != "number":
            raise Difference("reference", "date-bearing header must identify one numeric field")
    for spec in rules.get("split_date_ranges", []):
        if (not isinstance(spec, dict) or not isinstance(spec.get("aliases"), list) or not spec["aliases"]
                or spec.get("start") == spec.get("end")
                or any(fields.get(spec.get(k), {}).get("type") != "date" for k in ["start", "end"])):
            raise Difference("reference", "invalid date-range fields")


def name(value: Any) -> str:
    return re.sub(r"[\s_]+", "", str(value).lstrip("\ufeff")).casefold()


def _unit(token: str) -> str | None:
    return next((key for key, (_, _, aliases) in UNITS.items() if name(token) in map(name, aliases)), None)


def _header(value: Any) -> tuple[str, str | None]:
    text = str(value).strip()
    match = re.fullmatch(r"(.*?)[(（\[]([^()（）\[\]]+)[)）\]]", text)
    if match and (unit := _unit(match[2])):
        return name(match[1]), unit
    for token in sorted({a for _, _, aliases in UNITS.values() for a in aliases}, key=len, reverse=True):
        if text.casefold().endswith("_" + token.casefold()):
            return name(text[:-(len(token) + 1)]), _unit(token)
    return name(text), None


def _reference_unit(header: Any) -> str | None:
    """Unit of an inferred reference column; a trailing _ratio names the indicator (as for candidates in _align)."""
    unit = _header(header)[1]
    if unit == "ratio" and str(header).casefold().endswith("_ratio"):
        return None
    return unit


def _field_index(columns: list[dict]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for i, column in enumerate(columns):
        labels = [column["name"], *column.get("aliases", [])]
        keys = {name(a) for a in labels}
        for label in labels:
            base, unit = _header(label)
            if unit and column.get("unit") in UNITS and UNITS[unit][0] == UNITS[column["unit"]][0]:
                keys.add(base)
        for alias in keys:
            index[alias].append(i)
    return index


def _question_stem(header: Any, question: str | None) -> str | None:
    """The field name of a header written as the question words it, e.g. 设立规模（万元，保留2位小数）."""
    match = re.fullmatch(r"(.+?)\s*[(（]([^()（）]+)[)）]", str(header).strip())
    if not (match and question and _presentation_normalization()):
        return None
    compact = lambda text: re.sub(r"\s+", "", str(text)).translate(_BRACKET_WIDTH)
    return match[1] if compact(match[0]) in compact(question) else None


def _lookup(index: dict[str, list[int]], header: Any, question: str | None = None) -> tuple[list[int], bool]:
    if (stem := _question_stem(header, question)) is not None:
        if matches := index.get(name(stem), index.get(_header(stem)[0], [])):
            return matches, True
    return index.get(name(header), index.get(_header(header)[0], [])), False


def _align(df: pd.DataFrame, columns: list[dict], *, role: str,
           question: str | None = None) -> tuple[pd.DataFrame, list[str | None]]:
    index = _field_index(columns)
    positions, units = {}, {}
    for pos, header in enumerate(df.columns):
        base, unit = _header(header)
        matches, from_question = _lookup(index, header, question)
        if from_question:
            unit = None  # The question's own note for this field, not a scale to convert.
        if len(matches) != 1:
            why = "ambiguous" if matches else "unknown"
            raise Difference("field", f"{role} {why} field {header!r}")
        field = matches[0]
        if field in positions:
            raise Difference("field", f"{role} duplicate field {columns[field]['name']!r}")
        alias_units = {name(k): v for k, v in columns[field].get("alias_units", {}).items()}
        if columns[field].get("type") not in {None, "number", "auto"} and name(header) in index:
            # In a literal period label such as start_month, month is part of
            # the field identity, not a numeric measurement suffix.
            unit = None
        if unit == "ratio" and str(header).casefold().endswith("_ratio") and name(header) in index:
            # A named indicator such as debt_to_assets_ratio does not declare
            # decimal-vs-percent scale. Explicit alias_units can do so.
            unit = None
        unit = unit or alias_units.get(name(header))
        if unit in {"percent", "multiple"} and not columns[field].get("unit") and _presentation_normalization():
            unit = None  # No declared scale to convert into: a dimensionless marker is presentation only.
        if unit:
            target = columns[field].get("unit")
            if not target or target not in UNITS or UNITS[target][0] != UNITS[unit][0]:
                raise Difference("unit", f"{role} field {columns[field]['name']!r}: {unit} != {target or 'unspecified'}")
        positions[field], units[field] = pos, unit
    missing = [c["name"] for i, c in enumerate(columns) if i not in positions and not c.get("optional")]
    if missing:
        raise Difference("field", f"{role} missing fields {missing!r}")
    aligned = pd.DataFrame({c["name"]: df.iloc[:, positions[i]].copy() if i in positions else pd.Series(None, index=df.index)
                            for i, c in enumerate(columns)}, index=df.index)
    aligned.attrs["absent_optional"] = [i for i in range(len(columns)) if i not in positions]
    return aligned, [units.get(i) for i in range(len(columns))]


def _missing(value: Any) -> bool:
    if isinstance(value, str) and not value.strip():
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _number(value: Any, column: dict, source_unit: str | None) -> Decimal:
    text = str(value).strip()
    target = column.get("unit")
    # A value-level unit is evidence of scale, not permission to guess one.
    for key, (_, _, aliases) in UNITS.items():
        suffix = next((a for a in sorted(aliases, key=len, reverse=True)
                       if text.casefold().endswith(a.casefold())), None)
        if suffix:
            if not target or UNITS[key][0] != UNITS.get(target, (None,))[0]:
                raise Difference("unit", f"field {column['name']!r}: {key} != {target or 'unspecified'}")
            if source_unit and key != source_unit:
                raise Difference("unit", f"field {column['name']!r}: conflicting cell and header units")
            source_unit, text = key, text[:-len(suffix)].strip()
            break
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise Difference("value", f"field {column['name']!r}: invalid number {value!r}") from exc
    if not number.is_finite():
        raise Difference("value", f"field {column['name']!r}: non-finite number")
    if source_unit:
        number *= UNITS[source_unit][1] / UNITS[target][1]
    if column.get("integer") and number != number.to_integral_value():
        raise Difference("value", f"field {column['name']!r}: expected an integer, got {value!r}")
    return number


def _cell(value: Any, column: dict, unit: str | None, decimals: int, *, raw: bool = False) -> Any:
    if _missing(value):
        return None
    kind = column.get("type", "auto")
    text = str(value).strip()
    if text in column.get("labels", {}):
        value = column["labels"][text]
        text = str(value)
    if kind == "state" and column.get("numeric_sign"):
        try:
            number = Decimal(text)
        except InvalidOperation:
            return text
        if not number.is_finite():
            raise Difference("value", f"field {column['name']!r}: invalid numeric state")
        return column["numeric_sign"]["positive" if number > 0 else "negative" if number < 0 else "zero"]
    if kind in {"quarter_set", "date_set"}:
        values = list(value) if isinstance(value, (list, tuple, set)) else re.split(r"\s*[,;，；、|]\s*", text)
        values = [token for v in values for token in re.split(r"\s*[,;，；、|]\s*", str(v))]
        periods = [_cell(v, {**column, "type": "quarter" if kind == "quarter_set" else "date"}, None, decimals) for v in values]
        if len(set(periods)) != len(periods):
            raise Difference("value", f"field {column['name']!r}: duplicate periods")
        return tuple(sorted(periods))
    if kind in {"number", "auto"}:
        try:
            number = _number(value, column, unit)
        except Difference:
            if kind != "auto" or unit:
                raise
        else:
            return number if raw else number.quantize(Decimal(1).scaleb(-column.get("decimals", decimals)), rounding=ROUND_HALF_UP)
    if kind == "auto" and not unit and not column.get("unit") and _presentation_normalization():
        # No declared unit to convert into: a percent sign is presentation only.
        if match := _PERCENT_TEXT.fullmatch(text):
            number = Decimal(match[1])
            return number if raw else number.quantize(Decimal(1).scaleb(-column.get("decimals", decimals)), rounding=ROUND_HALF_UP)
        if match := _MIDNIGHT.fullmatch(text):
            return match[1]
    if kind == "code":
        match = re.fullmatch(r"(\d+)\.([A-Za-z]+)", text)
        if match:
            code = match[1].zfill(column.get("width", 0))
            allowed_suffixes = column.get("code_suffixes", {}).get(code, column.get("suffixes", []))
            if match[2].upper() not in allowed_suffixes:
                raise Difference("value", f"field {column['name']!r}: invalid code suffix {match[2]!r}")
            text = match[1]
        if column.get("width") and text.isdigit():
            text = text.zfill(column["width"])
        return text
    if kind in {"month", "quarter", "date"}:
        if kind == "quarter" and (match := re.fullmatch(r"(\d{4})(?:\s*[- ]?[Qq]|年(?:第)?)([1-4])(?:季度)?", text)):
            return f"{match[1]}Q{match[2]}"
        if kind == "month" and (match := re.fullmatch(r"(\d{4})[-/年](\d{1,2})月?", text)):
            if 1 <= int(match[2]) <= 12:
                return f"{match[1]}-{int(match[2]):02d}"
        if kind == "month" and re.fullmatch(r"\d{6}", text) and 1 <= int(text[4:]) <= 12:
            return f"{text[:4]}-{text[4:]}"
        if not re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]00:00:00)?", text):
            raise Difference("value", f"field {column['name']!r}: invalid {kind} {text!r}")
        try:
            date = pd.Timestamp(text)
        except ValueError as exc:
            raise Difference("value", f"field {column['name']!r}: invalid date {text!r}") from exc
        if kind == "date":
            return date.strftime("%Y-%m-%d")
        if kind == "quarter" and date.is_quarter_end:
            return f"{date.year}Q{date.quarter}"
        if kind == "month" and date.is_month_end:
            return date.strftime("%Y-%m")
        raise Difference("value", f"field {column['name']!r}: {text!r} is not a {kind} end")
    text = column.get("labels", {}).get(text, text)
    return text.translate(_BRACKET_WIDTH) if isinstance(text, str) and _presentation_normalization() else text


def _rows(df: pd.DataFrame, columns: list[dict], units: list, decimals: int, *, raw=False) -> list[tuple]:
    rows = []
    locations = {c["name"]: i for i, c in enumerate(columns)}
    for row in df.itertuples(index=False, name=None):
        values = [_cell(v, c, u, decimals, raw=raw) for v, c, u in zip(row, columns, units, strict=True)]
        for i, column in enumerate(columns):
            if column.get("entity_key"):
                code = values[locations[column["entity_key"]]]
                aliases = column.get("entity_names", {}).get(str(code), [])
                if values[i] is not None and values[i] in aliases:
                    values[i] = ("entity", code)
        rows.append(tuple(values))
    return rows


def _raw_reference(rule, expected, columns, ref_units, decimals):
    if not rule.get("raw_reference"):
        return _rows(expected, columns, ref_units, decimals, raw=True)
    raw = rule["raw_reference"]
    frame, units = _align(pd.DataFrame(raw["rows"], columns=raw["columns"]), rule["columns"], role="raw reference")
    positions = [c["name"] for c in columns]
    units = [units[[c["name"] for c in rule["columns"]].index(n)] for n in positions]
    raw_rows = _rows(frame[positions], columns, units, decimals, raw=True)
    expected_rows = _rows(expected, columns, ref_units, decimals, raw=True)
    key_indices = [positions.index(key) for key in rule["raw_key"]]
    def key(row):
        return tuple(row[i] for i in key_indices)
    by_key = {key(row): row for row in raw_rows}
    if len(by_key) != len(raw_rows) or len({key(r) for r in expected_rows}) != len(expected_rows):
        raise Difference("reference", "raw reference keys must be unique")
    if set(by_key) != {key(r) for r in expected_rows}:
        raise Difference("reference", "raw and display reference keys differ")
    aligned = [by_key[key(row)] for row in expected_rows]
    display_rows = _rows(expected, columns, ref_units, decimals)
    for display, original in zip(display_rows, aligned, strict=True):
        for shown, value, column in zip(display, original, columns, strict=True):
            if isinstance(value, Decimal):
                precision = Decimal(1).scaleb(-column.get("decimals", decimals))
                allowed = {value.quantize(precision, rounding=ROUND_HALF_UP), value.quantize(precision, rounding=ROUND_HALF_EVEN)}
            else:
                allowed = {value}
            if shown not in allowed:
                raise Difference("reference", f"raw/display mismatch for {column['name']!r}")
    return aligned


def _match_rounding(ref_rows: list[tuple], raw_rows: list[tuple], gen_rows: list[tuple],
                    columns: list[dict], decimals: int) -> list[tuple] | None:
    """Allow two display roundings only when an unrounded reference proves both.

    A small bipartite matching preserves multiplicity when rounding intervals
    overlap. This maps whole rows, never individual columns by their values.
    """
    if not any(c.get("rounding") == "either" for c in columns):
        return None
    candidates = defaultdict(list)
    for i, (row, raw_row) in enumerate(zip(ref_rows, raw_rows, strict=True)):
        options = []
        for value, raw_value, column in zip(row, raw_row, columns, strict=True):
            values = {value}
            if column.get("rounding") == "either" and isinstance(raw_value, Decimal):
                values.add(raw_value.quantize(Decimal(1).scaleb(-column.get("decimals", decimals)), rounding=ROUND_HALF_UP))
                values.add(raw_value.quantize(Decimal(1).scaleb(-column.get("decimals", decimals)), rounding=ROUND_HALF_EVEN))
            options.append(values)
        if sum(len(values) > 1 for values in options) > 12:
            raise Difference("reference", "too many ambiguous display rounding fields; split the output contract")
        for option in product(*options):
            candidates[option].append(i)
    matched = {}

    def assign(j, visited):
        for i in candidates.get(gen_rows[j], []):
            if i in visited:
                continue
            visited.add(i)
            if i not in matched or assign(matched[i], visited):
                matched[i] = j
                return True
        return False

    if not all(assign(j, set()) for j in range(len(gen_rows))):
        return None
    aligned = [None] * len(gen_rows)
    for i, j in matched.items():
        aligned[j] = ref_rows[i]
    return aligned


def _record(frames: list[pd.DataFrame], rule: dict) -> list[pd.DataFrame]:
    if not rule.get("record"):
        return frames
    fields = rule["columns"]
    if len(frames) == 1:
        try:
            _align(frames[0], fields, role="record")
        except Difference:
            pass
        else:
            return frames
    if len(frames) > 1 and all(df.shape == (1, 1) and name(df.columns[0]) != "0" for df in frames):
        # Named scalars retain their field labels; duplicates are rejected by _align.
        return [pd.concat([df.reset_index(drop=True) for df in frames], axis=1)]
    if rule.get("scalar_order"):
        values = None
        if len(frames) == len(fields) and all(df.shape == (1, 1) and name(df.columns[0]) == "0" for df in frames):
            values = [df.iat[0, 0] for df in frames]
        elif len(frames) == 1 and frames[0].shape == (len(fields), 1) and name(frames[0].columns[0]) == "0":
            # The runtime may pack ordered scalar outputs into one column.
            values = frames[0].iloc[:, 0].tolist()
        if values is not None:
            order = rule["scalar_order"]
            if len(order) != len(fields) or set(order) != {c["name"] for c in fields}:
                raise Difference("reference", "invalid scalar_order contract")
            return [pd.DataFrame([dict(zip(order, values, strict=True))])]
    if len(frames) == 1 and frames[0].shape[1] == 2 and rule.get("key_value"):
        df = frames[0]
        index = _field_index(fields)
        result = {}
        for key, value in df.itertuples(index=False, name=None):
            matches = index.get(name(key), index.get(_header(key)[0], []))
            if len(matches) != 1 or matches[0] in result:
                if len(df) == 1:
                    return frames
                raise Difference("component", f"unknown, ambiguous or duplicate record key {key!r}")
            result[matches[0]] = (key, value)
        return [pd.DataFrame([{key: v for key, v in result.values()}])]
    return frames


def _repeated_summary(frames: list[pd.DataFrame], tables: list[dict]) -> list[pd.DataFrame]:
    if len(frames) != 1:
        return frames
    if len(tables) != 2:
        raise Difference("reference", "repeated_summary requires detail and summary tables")
    frame = frames[0]
    detail_fields, summary_fields = tables[0]["columns"], tables[1]["columns"]
    combined, units = _align(frame, detail_fields + summary_fields, role="combined detail/summary")
    if combined.empty:
        raise Difference("component", "empty combined table has no summary")
    summary = combined.iloc[:, len(detail_fields):]
    for i, column in enumerate(summary_fields, len(detail_fields)):
        if i in combined.attrs.get("absent_optional", []):
            continue
        normalized = [_cell(value, column, units[i], 4, raw=True) for value in summary[column["name"]]]
        if len(set(normalized)) != 1:
            raise Difference("component", f"repeated summary field {column['name']!r} is not constant")
    # Keep normalized field names, but preserve source units by reusing the
    # original column headers from their identity-based positions.
    lookup = _field_index(detail_fields + summary_fields)
    original = {}
    for header in frame.columns:
        matches = lookup.get(name(header), lookup.get(_header(header)[0], []))
        original[matches[0]] = header
    detail = frame.loc[:, [original[i] for i in range(len(detail_fields)) if i in original]].copy()
    summary = frame.loc[:, [original[i] for i in range(len(detail_fields), len(detail_fields) + len(summary_fields)) if i in original]].iloc[:1].copy()
    return [detail, summary]


def _extrema(frames: list[pd.DataFrame], rule: dict) -> list[pd.DataFrame]:
    spec = rule.get("extrema")
    if not spec or len(frames) != 1 or frames[0].shape[1] != 3:
        return frames
    fields = [{"name": "role", "aliases": spec["role_aliases"]},
              {"name": "value", "aliases": spec["value_aliases"]},
              {"name": "period", "aliases": spec["period_aliases"]}]
    frame, units = _align(frames[0], [{**c, "unit": spec.get("unit")} for c in fields], role="extrema")
    values, periods = {}, defaultdict(list)
    for label, value, period in frame.itertuples(index=False, name=None):
        matches = [role for role, aliases in spec["roles"].items() if str(label).strip() in aliases]
        if len(matches) != 1:
            raise Difference("component", f"unknown or ambiguous extreme role {label!r}")
        role = matches[0]
        if role in values and str(values[role]) != str(value):
            raise Difference("component", f"conflicting values for extreme role {role!r}")
        values[role] = value
        periods[role].append(period)
    if set(values) != set(spec["roles"]):
        raise Difference("component", "missing maximum or minimum component")
    record = {}
    for role in spec["roles"]:
        value_field, periods_field = spec["fields"][role]
        if units[1]:
            # Carry the explicit source unit through the wide representation.
            suffix = UNITS[units[1]][2][0]
            value_field = f"{value_field}({suffix})"
        record[value_field], record[periods_field] = values[role], periods[role]
    return [pd.DataFrame([record])]


def _summary_counts(ref: list[pd.DataFrame], rules: dict, decimals: int) -> list[pd.DataFrame]:
    if not rules.get("summary_counts"):
        return ref
    if len(ref) != 2 or len(rules["tables"]) != 2:
        raise Difference("reference", "summary_counts requires detail and summary")
    detail_columns = rules["tables"][0]["columns"]
    detail, units = _align(ref[0], detail_columns, role="count detail")
    rows = _rows(detail, detail_columns, units, decimals)
    summary_columns = rules["tables"][1]["columns"]
    aligned_summary, summary_units = _align(ref[1], summary_columns, role="count summary")
    summary = aligned_summary
    if len(summary) != 1:
        raise Difference("reference", "summary_counts requires a single summary row")
    summary = ref[1].copy()
    for spec in rules["summary_counts"]:
        positions = [i for i, col in enumerate(detail_columns) if col["name"] == spec["detail_field"]]
        if len(positions) != 1:
            raise Difference("reference", "unknown count detail field")
        count = sum(row[positions[0]] == spec["equals"] for row in rows)
        target = spec["field"]
        target_positions = [i for i, col in enumerate(summary_columns) if col["name"] == target]
        if len(target_positions) != 1:
            raise Difference("reference", "unknown count summary field")
        j = target_positions[0]
        if target in aligned_summary and not _missing(aligned_summary.iloc[0][target]):
            if _number(aligned_summary.iloc[0][target], summary_columns[j], summary_units[j]) != count:
                raise Difference("reference", "summary count contradicts detail")
            continue
        summary[target] = count
    return [ref[0], summary]


def _assign_tables(frames: list[pd.DataFrame], tables: list[dict], role: str) -> list[pd.DataFrame]:
    assigned = {}
    for frame in frames:
        matches = []
        adapted = {}
        for i, table in enumerate(tables):
            try:
                candidate = _record(_extrema([frame], table), table)
                if len(candidate) != 1:
                    continue
                _align(candidate[0], table["columns"], role=role)
            except Difference:
                continue
            matches.append(i)
            adapted[i] = candidate[0]
        if len(matches) != 1 or matches[0] in assigned:
            raise Difference("component", f"{role}: ambiguous, duplicate or unrecognized table role for {list(frame.columns)!r}")
        assigned[matches[0]] = adapted[matches[0]]
    return [assigned[i] for i in range(len(tables))]


def _prepare_components(frames: list[pd.DataFrame], rules: dict) -> list[pd.DataFrame]:
    """Unpack declared date ranges and sparse tagged tables without losing data.

    Only explicitly mapped labels determine a table's role. Padding is removable
    only when it belongs to another declared field and is entirely empty for that
    section; unknown fields and misplaced nonempty values are still errors.
    """
    tables = rules["tables"]
    all_fields = [c for table in tables for c in table["columns"]]
    all_index = _field_index(all_fields)
    spec = rules.get("sections")
    outputs = []
    for original in frames:
        if len(set(map(name, original.columns))) != len(original.columns):
            raise Difference("field", "duplicate fields in result")
        frame = original.copy()
        dated_fields = set()
        for dated in rules.get("header_dates", []):
            if not any(name(h) == name(dated["column"]) for h in frame.columns):
                continue
            target = dated["field"]
            if target in dated_fields:
                raise Difference("field", f"duplicate date-bearing fields for {target!r}")
            dated_fields.add(target)
            column = next(c for c in all_fields if c["name"] == target)
            aliases = {name(a) for a in [target, *column.get("aliases", [])]}
            explicit = [h for h in frame.columns if name(h) in aliases]
            if explicit:
                if len(explicit) != 1 or any(_cell(v, column, None, 4) != dated["date"]
                                             for v in frame[explicit[0]]):
                    raise Difference("value", f"header date conflicts with {target!r}")
            else:
                frame[target] = dated["date"]
        for interval in rules.get("split_date_ranges", []):
            headers = [h for h in frame.columns if name(h) in set(map(name, interval["aliases"]))]
            if not headers:
                continue
            if len(headers) != 1:
                raise Difference("field", "duplicate date-range fields")
            targets = [interval["start"], interval["end"]]
            aliases = {name(a) for c in all_fields if c["name"] in targets for a in [c["name"], *c.get("aliases", [])]}
            if any(name(h) in aliases for h in frame.columns):
                raise Difference("field", "date range conflicts with separate start/end fields")
            start, end = [], []
            for value in frame[headers[0]]:
                if _missing(value):
                    start.append(None); end.append(None)
                    continue
                match = re.fullmatch(r"\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*(?:~|～|至|到)\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*", str(value))
                if not match:
                    raise Difference("value", f"invalid date range {value!r}")
                a, b = [_cell(v, {"name": targets[i], "type": "date"}, None, 4) for i, v in enumerate(match.groups())]
                if a > b:
                    raise Difference("value", "date-range start is after end")
                start.append(a); end.append(b)
            frame = frame.drop(columns=headers)
            frame[targets[0]], frame[targets[1]] = start, end
        tags = [h for h in frame.columns if spec and name(h) in set(map(name, [spec["column"], *spec.get("aliases", [])]))]
        if not tags:
            outputs.append(frame)
            continue
        if len(tags) != 1:
            raise Difference("field", "duplicate section discriminator")
        if frame.empty:
            raise Difference("component", "empty tagged table has no sections")
        labels = frame[tags[0]].map(lambda x: str(x).strip())
        if not labels.isin(spec["labels"]).all():
            raise Difference("component", "unknown or missing section label")
        for label in labels.unique():
            part = frame.loc[labels == label].drop(columns=tags)
            fields = tables[spec["labels"][label]]["columns"]
            index = _field_index(fields)
            keep = []
            for h in part.columns:
                base, _ = _header(h)
                if index.get(name(h), index.get(base, [])):
                    keep.append(h)
                elif not all_index.get(name(h), all_index.get(base, [])):
                    raise Difference("field", f"unknown section field {h!r}")
                elif not all(_missing(v) for v in part[h]):
                    raise Difference("component", f"nonempty field {h!r} outside section {label!r}")
            part = part.loc[:, keep].reset_index(drop=True)
            _align(part, fields, role=f"section {label}")
            outputs.append(part)
    return outputs


def _scalar_row(ref: list[pd.DataFrame], gen: list[pd.DataFrame], tables: list[dict]) -> list[pd.DataFrame]:
    """result_vars may be numeric scalars in question order; N of them against a one-row, N-column reference are that row.

    A declared scalar_order is that task's explicit order and is applied later by _record instead."""
    if (_presentation_normalization() and not any(t.get("scalar_order") for t in tables)
            and len(ref) == 1 and len(ref[0]) == 1 and len(gen) > 1
            and len(gen) == ref[0].shape[1]
            and all(f.shape == (1, 1) and [str(c) for c in f.columns] == ["0"] for f in gen)):
        return [pd.DataFrame([[f.iat[0, 0] for f in gen]], columns=list(ref[0].columns))]
    return gen


def compare_tables(ref: list[pd.DataFrame], gen: list[pd.DataFrame], *, rules: dict | None,
                   decimals: int, sort_rows: bool, column_order_matters: bool,
                   question: str | None = None) -> tuple[bool, str | None]:
    try:
        if rules is not None:
            validate_rules(rules)
        tables = rules.get("tables", []) if rules is not None else []
        if rules is not None and (not isinstance(tables, list) or not tables):
            raise Difference("reference", "comparison_rules.tables must be a nonempty list")
        if not ref:
            raise Difference("reference", "no reference components")
        gen = _scalar_row(ref, gen, tables)
        if rules and (rules.get("sections") or rules.get("split_date_ranges") or rules.get("header_dates")):
            try:
                ref = _prepare_components(ref, rules)
            except Difference as exc:
                raise Difference("reference", str(exc)) from exc
            gen = _prepare_components(gen, rules)
        if rules and rules.get("auxiliary_columns"):
            auxiliaries = set(map(name, rules["auxiliary_columns"]))
            def without_auxiliary(frames):
                outputs = []
                for frame in frames:
                    if len(set(map(name, frame.columns))) != len(frame.columns):
                        raise Difference("field", "duplicate fields in result")
                    outputs.append(frame.loc[:, [c for c in frame.columns if name(c) not in auxiliaries]].copy())
                return outputs
            ref, gen = without_auxiliary(ref), without_auxiliary(gen)
        if rules and rules.get("repeated_summary"):
            try:
                ref = _repeated_summary(ref, tables)
            except Difference as exc:
                raise Difference("reference", str(exc)) from exc
            gen = _repeated_summary(gen, tables)
        if len(tables) == 1:
            ref, gen = _extrema(ref, tables[0]), _extrema(gen, tables[0])
            ref, gen = _record(ref, tables[0]), _record(gen, tables[0])
        if len(ref) != len(gen) or (tables and len(ref) != len(tables)):
            raise Difference("component", f"result count: reference={len(ref)}, candidate={len(gen)}, contract={len(tables) or len(ref)}")
        if len(tables) > 1 and not rules.get("component_order_matters", False):
            try:
                ref = _assign_tables(ref, tables, "reference")
            except Difference as exc:
                raise Difference("reference", str(exc)) from exc
            gen = _assign_tables(gen, tables, "candidate")
        if rules:
            ref = _summary_counts(ref, rules, decimals)
        for i, (expected, actual) in enumerate(zip(ref, gen, strict=True)):
            rule = tables[i] if tables else {}
            columns = rule.get("columns") or [{"name": str(c), "type": "auto", **({"unit": u} if (u := _reference_unit(c)) else {})} for c in expected.columns]
            if len({c["name"] for c in columns}) != len(columns):
                raise Difference("reference", f"table {i + 1}: duplicate contract fields")
            try:
                expected, ref_units = _align(expected, columns, role="reference")
            except Difference as exc:
                raise Difference("reference", f"table {i + 1}: {exc}") from exc
            original_columns = list(actual.columns)
            identity_order = not tables and column_order_matters
            mismatch = Difference("field", f"table {i + 1}: column identities/order differ; expected {list(expected.columns)!r}, got {original_columns!r}")
            if identity_order and not _presentation_normalization() and list(map(name, original_columns)) != list(map(name, expected.columns)):
                raise mismatch
            try:
                actual, gen_units = _align(actual, columns, role="candidate", question=question)
            except Difference as exc:
                if identity_order and str(exc).startswith("field:"):
                    raise mismatch from exc
                raise
            if identity_order:
                # Same identities as _align (name, unit-free base name, or the question's wording), in order.
                index = _field_index(columns)
                if [_lookup(index, c, question)[0][0] for c in original_columns] != list(range(len(columns))):
                    raise mismatch
            absent = actual.attrs.get("absent_optional", [])
            if absent:
                keep = [j for j in range(len(columns)) if j not in absent]
                columns = [columns[j] for j in keep]
                expected, actual = expected.iloc[:, keep], actual.iloc[:, keep]
                ref_units, gen_units = [ref_units[j] for j in keep], [gen_units[j] for j in keep]
            if rule.get("column_order_matters", False):
                index = _field_index(columns)
                actual_order = [index.get(name(c), index.get(_header(c)[0], []))[0] for c in original_columns]
                if actual_order != list(range(len(columns))):
                    raise Difference("field", f"table {i + 1}: required field order differs")
            if len(expected) != len(actual):
                raise Difference("component", f"table {i + 1}: row count reference={len(expected)}, candidate={len(actual)}")
            try:
                ref_rows = _rows(expected, columns, ref_units, decimals)
                raw_rows = _raw_reference(rule, expected, columns, ref_units, decimals)
            except Difference as exc:
                raise Difference("reference", str(exc)) from exc
            gen_rows = _rows(actual, columns, gen_units, decimals)
            if Counter(ref_rows) != Counter(gen_rows):
                rounded = _match_rounding(ref_rows, raw_rows, gen_rows, columns, decimals)
                if rounded is not None:
                    gen_rows = rounded
            if Counter(ref_rows) != Counter(gen_rows):
                # Column diagnostics are supplementary; matching always uses complete rows.
                bad_fields = [c["name"] for j, c in enumerate(columns)
                              if Counter(row[j] for row in ref_rows) != Counter(row[j] for row in gen_rows)]
                missing = list((Counter(ref_rows) - Counter(gen_rows)).elements())[:2]
                extra = list((Counter(gen_rows) - Counter(ref_rows)).elements())[:2]
                raise Difference("value", f"table {i + 1}, fields {bad_fields or [c['name'] for c in columns]!r}: missing rows {missing!r}; unexpected rows {extra!r}")
            order = rule.get("order_by", [])
            if order:
                keys = []
                reference_positions = []
                locations = {c["name"]: j for j, c in enumerate(columns)}
                by_row = defaultdict(deque)
                for position, (row, raw_row) in enumerate(zip(ref_rows, raw_rows, strict=True)):
                    values = []
                    for key in order:
                        value = raw_row[locations[key["field"]]]
                        if key.get("subtract"):
                            value -= raw_row[locations[key["subtract"]]]
                        if key.get("transform") == "abs":
                            value = abs(value)
                        values.append(value)
                    by_row[row].append((tuple(values), position))
                for row in gen_rows:
                    values, position = by_row[row].popleft()
                    keys.append(values)
                    reference_positions.append(position)
                rounded_sort = rule.get("sort_reference_rounded", False) and not rule.get("raw_reference")
                uncertain_ties = rounded_sort and any(columns[locations[key["field"]]].get("type") == "number"
                                                     and not columns[locations[key["field"]]].get("integer", False) for key in order)
                if uncertain_ties:
                    for j in range(1, len(keys)):
                        if keys[j - 1] == keys[j] and reference_positions[j - 1] > reference_positions[j]:
                            raise Difference("reference", f"table {i + 1}: reordered display ties need unrounded sort evidence")
                for previous, current in zip(keys, keys[1:]):
                    for j, key in enumerate(order):
                        if previous[j] == current[j]:
                            continue
                        if previous[j] is None or current[j] is None:
                            raise Difference("reference", f"sort field {key['field']!r} requires an explicit null order")
                        if (previous[j] > current[j]) == key.get("ascending", True):
                            raise Difference("order", f"table {i + 1}, field {key['field']!r}: {previous!r} precedes {current!r}")
                        break
            elif not rule.get("unordered", sort_rows) and ref_rows != gen_rows:
                raise Difference("order", f"table {i + 1}: required row order differs")
        return True, None
    except Difference as exc:
        return False, str(exc)
