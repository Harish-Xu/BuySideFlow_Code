from __future__ import annotations

import calendar
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeDateViolation:
    location: str
    token: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_SQL_RUNTIME_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CURDATE()", re.compile(r"\bCURDATE\s*\(", re.IGNORECASE)),
    ("CURRENT_DATE", re.compile(r"\bCURRENT_DATE(?:\s*\(\s*\))?\b", re.IGNORECASE)),
    ("NOW()", re.compile(r"\bNOW\s*\(", re.IGNORECASE)),
    ("SYSDATE()", re.compile(r"\bSYSDATE\s*\(", re.IGNORECASE)),
    ("CURRENT_TIMESTAMP", re.compile(r"\bCURRENT_TIMESTAMP(?:\s*\(\s*\))?\b", re.IGNORECASE)),
)
_PY_RUNTIME_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("date.today()", re.compile(r"\bdate\s*\.\s*today\s*\(", re.IGNORECASE)),
    ("datetime.now()", re.compile(r"\bdatetime\s*\.\s*now\s*\(", re.IGNORECASE)),
    ("datetime.today()", re.compile(r"\bdatetime\s*\.\s*today\s*\(", re.IGNORECASE)),
    ("pd.Timestamp.today()", re.compile(r"\b(?:pd|pandas)\s*\.\s*Timestamp\s*\.\s*today\s*\(", re.IGNORECASE)),
    ("pd.Timestamp.now()", re.compile(r"\b(?:pd|pandas)\s*\.\s*Timestamp\s*\.\s*now\s*\(", re.IGNORECASE)),
)
_PY_EMBEDDED_SQL_RUNTIME_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CURDATE()", re.compile(r"\bCURDATE\s*\(", re.IGNORECASE)),
    ("NOW()", re.compile(r"\bNOW\s*\(", re.IGNORECASE)),
    ("SYSDATE()", re.compile(r"\bSYSDATE\s*\(", re.IGNORECASE)),
    ("CURRENT_TIMESTAMP", re.compile(r"\bCURRENT_TIMESTAMP(?:\s*\(\s*\))?\b", re.IGNORECASE)),
)
_DATE_LITERAL_RE = re.compile(
    r"(?P<iso>20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})|"
    r"(?P<cn>20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?)"
)
_LAST_TRADING_DAY_PHRASE = r"最后\s*(?:一\s*)?(?:个\s*)?交易日"
_MONTH_END_WORD = r"(?:月\s*)?(?:底|末)"
_YEAR_END_WORD = r"(?:年\s*)?(?:底|末)"
_MONTH_END_ANCHOR_RE = re.compile(
    rf"(?P<year>20\d{{2}})\s*年\s*(?P<month>\d{{1,2}})\s*月\s*(?:{_MONTH_END_WORD}|{_LAST_TRADING_DAY_PHRASE})"
)
_YEAR_END_ANCHOR_RE = re.compile(
    rf"(?P<year>20\d{{2}})\s*年\s*(?:{_YEAR_END_WORD}|收盘后|{_LAST_TRADING_DAY_PHRASE})"
)
_QUARTER_ANCHOR_RE = re.compile(
    r"(?P<year>20\d{2})\s*(?:"
    r"[Qq]\s*(?P<quarter_q>[1-4])|"
    r"年\s*(?:第\s*)?(?P<quarter_cn>[1-4一二三四])\s*(?:季度|季)"
    r")"
)
_YEAR_RE = re.compile(r"\b20\d{2}\b|20\d{2}年")
_FINANCIAL_TABLE_RE = re.compile(
    r"\b(?:lc_mainindexnew|lc_maindatanew|lc_qfinancialindexnew|lc_income|lc_balance|lc_cashflow)\b",
    re.IGNORECASE,
)
_CS_RISKALERT_RE = re.compile(r"\bcs_riskalert\b", re.IGNORECASE)
_INFOPUBL_FIELD = r"(?:\b\w+\.)?INFOPUBL(?:DATE|TIME)\b"
_INFOPUBL_DATE_EXPR = rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*{_INFOPUBL_FIELD}\s*\)|{_INFOPUBL_FIELD})"
_INFOPUBL_GUARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{_INFOPUBL_DATE_EXPR}\s*(?:<=|<)", re.IGNORECASE),
    re.compile(rf"(?:<=|<)\s*[^;\n]{{0,120}}{_INFOPUBL_DATE_EXPR}", re.IGNORECASE),
    re.compile(rf"{_INFOPUBL_DATE_EXPR}\s+BETWEEN\s+[^;\n]{{1,160}}\s+AND\s+[^;\n]{{1,160}}", re.IGNORECASE),
)
_CS_RISKALERT_PUBLISH_FIELD = r"(?:\b\w+\.)?(?:IMPLANNOUCEDATE|REMOVEINFOPUBLDATE|INSERTTIME|UPDATETIME)\b"
_CS_RISKALERT_PUBLISH_DATE_EXPR = (
    rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*{_CS_RISKALERT_PUBLISH_FIELD}\s*\)|{_CS_RISKALERT_PUBLISH_FIELD})"
)
_CS_RISKALERT_PUBLISH_GUARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{_CS_RISKALERT_PUBLISH_DATE_EXPR}\s*(?:<=|<|>=|>)", re.IGNORECASE),
    re.compile(rf"(?:<=|<|>=|>)\s*[^;\n]{{0,120}}{_CS_RISKALERT_PUBLISH_DATE_EXPR}", re.IGNORECASE),
    re.compile(
        r"\b(?:COALESCE|DATE|TIMESTAMP)\s*\([^;\n]{0,240}"
        r"\b(?:IMPLANNOUCEDATE|REMOVEINFOPUBLDATE|INSERTTIME|UPDATETIME)\b[^;\n]{0,240}\)"
        r"\s*(?:<=|<|>=|>)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:<=|<|>=|>)\s*[^;\n]{0,240}\b(?:COALESCE|DATE|TIMESTAMP)\s*\([^;\n]{0,240}"
        r"\b(?:IMPLANNOUCEDATE|REMOVEINFOPUBLDATE|INSERTTIME|UPDATETIME)\b[^;\n]{0,240}\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\b\w+\s*\[\s*['\"](?:implannoucedate|removeinfopubldate|inserttime|updatetime)['\"]\s*\]|"
        r"\b(?:implannoucedate|removeinfopubldate|inserttime|updatetime)\b)\s*(?:<=|<|>=|>)",
        re.IGNORECASE,
    ),
)
_SNAPSHOT_FIELD_RE = re.compile(
    r"\b(?:EFFECTIVEDATE|CANCELDATE|EXPIREDATE|LISTEDDATE|INDATE|OUTDATE|IMPLEMENTDATE|REMOVEDATE|"
    r"effective_date|cancel_date|expire_date|expired_date|listed_date|in_date|out_date|"
    r"implement_date|remove_date|implementdate|removedate)\b",
    re.IGNORECASE,
)
_SQL_SNAPSHOT_START_FIELD = r"(?:\b\w+\.)?(?:EFFECTIVEDATE|LISTEDDATE|INDATE|IMPLEMENTDATE)\b"
_SQL_SNAPSHOT_END_FIELD = r"(?:\b\w+\.)?(?:CANCELDATE|EXPIREDATE|OUTDATE|REMOVEDATE)\b"
_SQL_DATE_START_FIELD = rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*{_SQL_SNAPSHOT_START_FIELD}\s*\)|{_SQL_SNAPSHOT_START_FIELD})"
_SQL_DATE_END_FIELD = rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*{_SQL_SNAPSHOT_END_FIELD}\s*\)|{_SQL_SNAPSHOT_END_FIELD})"
_PY_SNAPSHOT_START_FIELD = (
    r"(?:\b\w+\s*\[\s*['\"](?:EFFECTIVEDATE|LISTEDDATE|INDATE|IMPLEMENTDATE|effective_date|listed_date|"
    r"in_date|implement_date|implementdate)['\"]\s*\]|"
    r"\b(?:EFFECTIVEDATE|LISTEDDATE|INDATE|IMPLEMENTDATE|effective_date|listed_date|in_date|"
    r"implement_date|implementdate)\b)"
)
_PY_SNAPSHOT_END_FIELD = (
    r"(?:\b\w+\s*\[\s*['\"](?:CANCELDATE|EXPIREDATE|OUTDATE|REMOVEDATE|cancel_date|expire_date|"
    r"expired_date|out_date|remove_date|removedate)['\"]\s*\]|"
    r"\b(?:CANCELDATE|EXPIREDATE|OUTDATE|REMOVEDATE|cancel_date|expire_date|expired_date|out_date|"
    r"remove_date|removedate)\b)"
)
_SNAPSHOT_GUARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    # SQL: DATE(field) <= anchor, field < DATE_ADD(anchor, INTERVAL 1 DAY), etc.
    re.compile(rf"{_SQL_DATE_START_FIELD}\s*(?:<=|<)", re.IGNORECASE),
    re.compile(rf"{_SQL_DATE_END_FIELD}\s*(?:IS\s+NULL|>=|>)", re.IGNORECASE),
    re.compile(
        r"\b(?:DATE|TIMESTAMP)\s*\([^;\n]{0,200}\b(?:EFFECTIVEDATE|LISTEDDATE|INDATE|IMPLEMENTDATE)\b[^;\n]{0,200}\)"
        r"\s*(?:<=|<)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:DATE|TIMESTAMP)\s*\([^;\n]{0,200}\b(?:CANCELDATE|EXPIREDATE|OUTDATE|REMOVEDATE)\b[^;\n]{0,200}\)"
        r"\s*(?:>=|>)",
        re.IGNORECASE,
    ),
    # SQL: DATEDIFF(anchor, LISTEDDATE) >= N means listed at least N days before anchor.
    re.compile(
        r"\bDATEDIFF\s*\(\s*[^,;\n]{1,160},\s*(?:\b(?:DATE|TIMESTAMP)\s*\(\s*)?"
        r"(?:\b\w+\.)?LISTEDDATE\b[^)]*\)\s*>=\s*\d+",
        re.IGNORECASE,
    ),
    # Pandas/Python: df["listed_date"] <= obs_date, listed_date < cutoff, etc.
    re.compile(rf"{_PY_SNAPSHOT_START_FIELD}\s*(?:<=|<)", re.IGNORECASE),
    re.compile(rf"{_PY_SNAPSHOT_END_FIELD}\s*(?:>=|>)", re.IGNORECASE),
    re.compile(rf"{_PY_SNAPSHOT_END_FIELD}\s*\.\s*(?:isna|isnull)\s*\(", re.IGNORECASE),
    re.compile(rf"(?:pd|pandas)\s*\.\s*(?:isna|isnull)\s*\(\s*{_PY_SNAPSHOT_END_FIELD}\s*\)", re.IGNORECASE),
    re.compile(rf"(?:np|numpy)\s*\.\s*isnat\s*\(\s*{_PY_SNAPSHOT_END_FIELD}\s*\)", re.IGNORECASE),
)
_FUTURE_LABEL_RE = re.compile(
    r"未来|下一(?:月|日|期)|realized|next[_\s-]?(?:month|period|return)|离线评估|已可验证",
    re.IGNORECASE,
)
_CURRENT_STATE_STATUS_FILTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:SECUABBR|SECUNAME|CHINAME|CHINAMEABBR|stock_name|sec_name)\b"
        r"[^;\n]{0,160}\b(?:LIKE|NOT\s+LIKE|REGEXP|RLIKE)\b[^;\n]{0,160}(?:\*?\s*ST|退市)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\b\w+\s*\[\s*['\"](?:SECUABBR|SECUNAME|CHINAME|CHINAMEABBR|stock_name|sec_name)['\"]\s*\]|"
        r"\b(?:SECUABBR|SECUNAME|CHINAME|CHINAMEABBR|stock_name|sec_name)\b)"
        r"[^;\n]{0,160}\.\s*str\s*\.\s*contains\s*\([^)]*(?:\*?\s*ST|退市)",
        re.IGNORECASE,
    ),
)
_CLASSIFICATION_VINTAGE_TABLE_RE = re.compile(
    r"\b(?:mf_jyfundtype|mf_fundtype|lc_exgindustry|lc_stibexgindustry|nq_exgindustry|"
    r"lc_indexcomponent|lc_indexcomponentsweight)\b",
    re.IGNORECASE,
)
_FUTURE_LABEL_TOKEN = (
    r"(?:future|forward|fwd|next|label|未来|后续|"
    r"(?:future_|next_|fwd_)?(?:excess_?)?ret(?:urn)?_?(?:5|10|20|60|120|252)?(?:d|day|days)?|"
    r"(?:5|10|20|60|120|252)(?:d|day|days)?_?(?:excess_?)?ret(?:urn)?)"
)
_FUTURE_LABEL_FEATURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?:WHERE|HAVING|QUALIFY|ORDER\s+BY)\b[^;\n]{{0,260}}{_FUTURE_LABEL_TOKEN}", re.IGNORECASE),
    re.compile(
        rf"\[[^\]\n]{{0,220}}{_FUTURE_LABEL_TOKEN}[^\]\n]{{0,220}}"
        r"(?:[<>]=?|==|!=|\.isin\s*\(|\.between\s*\()",
        re.IGNORECASE,
    ),
    re.compile(rf"\.(?:query|sort_values|nlargest|nsmallest)\s*\([^)]{{0,220}}{_FUTURE_LABEL_TOKEN}", re.IGNORECASE),
)
_PIT_DIAGNOSTIC_FLAG_ORDER = (
    "runtime_date_leakage",
    "financial_publish_guard_missing",
    "current_state_status_filter",
    "classification_vintage_missing",
    "future_label_as_feature_suspected",
)
_INPUT_ANCHOR_KEYS = (
    "as_of_date",
    "observation_date",
    "anchor_date",
    "target_date",
    "end_date",
    "snapshot_as_of_date",
)


def _normalize_date_literal(value: str) -> str:
    value = re.sub(r"\s+", "", value.strip()).replace("/", "-").replace(".", "-")
    cn_match = re.fullmatch(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?", value)
    if cn_match:
        return f"{int(cn_match.group(1)):04d}-{int(cn_match.group(2)):02d}-{int(cn_match.group(3)):02d}"
    iso_match = re.fullmatch(r"(20\d{2})-(\d{1,2})-(\d{1,2})", value)
    if iso_match:
        return f"{int(iso_match.group(1)):04d}-{int(iso_match.group(2)):02d}-{int(iso_match.group(3)):02d}"
    return value


def _format_date(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _month_end_date(year: int, month: int) -> str:
    if month < 1 or month > 12:
        return ""
    return _format_date(year, month, calendar.monthrange(year, month)[1])


def _quarter_end_date(year: int, quarter: str) -> str:
    quarter_map = {"一": 1, "二": 2, "三": 3, "四": 4}
    quarter_num = quarter_map.get(quarter, int(quarter) if quarter.isdigit() else 0)
    if quarter_num < 1 or quarter_num > 4:
        return ""
    month = quarter_num * 3
    return _month_end_date(year, month)


def _derived_instruction_anchors(question: str) -> list[str]:
    anchors: list[str] = []
    for match in _MONTH_END_ANCHOR_RE.finditer(question or ""):
        anchors.append(_month_end_date(int(match.group("year")), int(match.group("month"))))
    for match in _YEAR_END_ANCHOR_RE.finditer(question or ""):
        anchors.append(_format_date(int(match.group("year")), 12, 31))
    for match in _QUARTER_ANCHOR_RE.finditer(question or ""):
        anchors.append(_quarter_end_date(int(match.group("year")), match.group("quarter_q") or match.group("quarter_cn")))
    return [anchor for anchor in anchors if anchor]


def _input_anchor(inputs: Any) -> str:
    if not isinstance(inputs, dict):
        return ""
    for key in _INPUT_ANCHOR_KEYS:
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_date_literal(value)
    return ""


def extract_time_anchor(question: str, inputs: Any = None, *, snapshot_default: str = "") -> tuple[str, str]:
    input_anchor = _input_anchor(inputs)
    if input_anchor:
        return input_anchor, "inputs"

    matches = [_normalize_date_literal(match.group(0)) for match in _DATE_LITERAL_RE.finditer(question or "")]
    if matches:
        return max(matches), "instruction"

    derived_matches = _derived_instruction_anchors(question or "")
    if derived_matches:
        return max(derived_matches), "instruction_derived"

    if _YEAR_RE.search(question or ""):
        return _YEAR_RE.search(question or "").group(0).replace("年", ""), "instruction_year"

    if snapshot_default:
        return snapshot_default, "snapshot_default"

    return "", "missing"


def lint_sql_runtime_date_functions(sql: str, *, location: str = "sql") -> list[RuntimeDateViolation]:
    violations: list[RuntimeDateViolation] = []
    for token, pattern in _SQL_RUNTIME_DATE_PATTERNS:
        if pattern.search(sql or ""):
            violations.append(
                RuntimeDateViolation(
                    location=location,
                    token=token,
                    reason="use an explicit as_of_date/observation_date instead of runtime clock time",
                )
            )
    return violations


def lint_python_runtime_date_functions(python_code: str, *, location: str = "python") -> list[RuntimeDateViolation]:
    violations: list[RuntimeDateViolation] = []
    for token, pattern in _PY_RUNTIME_DATE_PATTERNS:
        if pattern.search(python_code or ""):
            violations.append(
                RuntimeDateViolation(
                    location=location,
                    token=token,
                    reason="use an explicit as_of_date/observation_date instead of runtime clock time",
                )
            )
    return violations


def lint_runtime_date_functions(
    *,
    sql_code: str = "",
    python_code: str = "",
    location_prefix: str = "",
) -> list[RuntimeDateViolation]:
    prefix = f"{location_prefix}." if location_prefix else ""
    violations = lint_sql_runtime_date_functions(sql_code, location=f"{prefix}sql")
    violations.extend(lint_python_runtime_date_functions(python_code, location=f"{prefix}python"))
    # Catch SQL runtime functions embedded in Python string literals or dynamically built SQL templates.
    # Do not scan for bare CURRENT_DATE here: ordinary result column names like "current_date"
    # are common in Python output dictionaries and are not runtime clock usage.
    for token, pattern in _PY_EMBEDDED_SQL_RUNTIME_DATE_PATTERNS:
        if pattern.search(python_code or ""):
            violations.append(
                RuntimeDateViolation(
                    location=f"{prefix}python_sql_literal",
                    token=token,
                    reason="use an explicit as_of_date/observation_date instead of runtime clock time",
                )
            )
    deduped: list[RuntimeDateViolation] = []
    seen: set[tuple[str, str]] = set()
    for violation in violations:
        key = (violation.location, violation.token)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(violation)
    return deduped


def format_runtime_date_lint_error(violations: list[RuntimeDateViolation]) -> str:
    rendered = ", ".join(f"{item.location}:{item.token}" for item in violations)
    return (
        "Runtime date function is forbidden in generated benchmark code: "
        f"{rendered}. Use an explicit time anchor from inputs/instruction/snapshot instead."
    )


def _read_optional(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def financial_publish_guard_status(sql_code: str, python_code: str) -> str:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    if not _FINANCIAL_TABLE_RE.search(combined):
        return "not_applicable"
    return "present" if any(pattern.search(combined) for pattern in _INFOPUBL_GUARD_PATTERNS) else "missing"


def cs_riskalert_publish_guard_status(sql_code: str, python_code: str) -> str:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    if not _CS_RISKALERT_RE.search(combined):
        return "not_applicable"
    return "present" if any(pattern.search(combined) for pattern in _CS_RISKALERT_PUBLISH_GUARD_PATTERNS) else "missing"


def snapshot_guard_status(sql_code: str, python_code: str) -> str:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    if not _SNAPSHOT_FIELD_RE.search(combined):
        return "not_applicable"
    return "present" if any(pattern.search(combined) for pattern in _SNAPSHOT_GUARD_PATTERNS) else "missing"


def future_label_allowed(question: str, sql_code: str = "", python_code: str = "") -> bool:
    return bool(_FUTURE_LABEL_RE.search(f"{question or ''}\n{sql_code or ''}\n{python_code or ''}"))


def _has_current_state_status_filter(sql_code: str, python_code: str) -> bool:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    if not any(pattern.search(combined) for pattern in _CURRENT_STATE_STATUS_FILTER_PATTERNS):
        return False
    # If the generated code also uses the dedicated point-in-time risk-alert table
    # with a state-window guard, do not flag redundant name filters as leakage.
    return not (_CS_RISKALERT_RE.search(combined) and snapshot_guard_status(sql_code, python_code) == "present")


def _classification_vintage_missing(sql_code: str, python_code: str) -> bool:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    if not _CLASSIFICATION_VINTAGE_TABLE_RE.search(combined):
        return False
    return not any(pattern.search(combined) for pattern in _SNAPSHOT_GUARD_PATTERNS)


def _future_label_as_feature_suspected(question: str, sql_code: str, python_code: str) -> bool:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    if not future_label_allowed(question, sql_code, python_code):
        return False
    return any(pattern.search(combined) for pattern in _FUTURE_LABEL_FEATURE_PATTERNS)


def pit_diagnostic_flags(*, question: str = "", sql_code: str = "", python_code: str = "") -> list[str]:
    """Return static PIT diagnostic flags for generated code.

    These flags are intentionally advisory. They support error analysis without
    changing task scores because several checks can produce false positives for
    tasks whose wording intentionally calls for latest-state or ex-post labels.
    """
    flags: set[str] = set()
    if lint_runtime_date_functions(sql_code=sql_code, python_code=python_code):
        flags.add("runtime_date_leakage")
    if financial_publish_guard_status(sql_code, python_code) == "missing":
        flags.add("financial_publish_guard_missing")
    if _has_current_state_status_filter(sql_code, python_code):
        flags.add("current_state_status_filter")
    if _classification_vintage_missing(sql_code, python_code):
        flags.add("classification_vintage_missing")
    if _future_label_as_feature_suspected(question, sql_code, python_code):
        flags.add("future_label_as_feature_suspected")
    return [flag for flag in _PIT_DIAGNOSTIC_FLAG_ORDER if flag in flags]


def audit_time_anchor_record(
    *,
    qid: str,
    question: str,
    inputs: Any = None,
    sql_code: str = "",
    python_code: str = "",
    snapshot_default: str = "",
) -> dict[str, Any]:
    time_anchor, anchor_source = extract_time_anchor(question, inputs, snapshot_default=snapshot_default)
    runtime_violations = lint_runtime_date_functions(sql_code=sql_code, python_code=python_code)
    return {
        "id": qid,
        "time_anchor": time_anchor,
        "anchor_source": anchor_source,
        "runtime_date_function": "ok" if not runtime_violations else "violation",
        "runtime_date_violations": [item.to_dict() for item in runtime_violations],
        "financial_publish_guard": financial_publish_guard_status(sql_code, python_code),
        "cs_riskalert_publish_guard": cs_riskalert_publish_guard_status(sql_code, python_code),
        "snapshot_guard": snapshot_guard_status(sql_code, python_code),
        "future_label_allowed": future_label_allowed(question, sql_code, python_code),
    }


def audit_folder_record(
    *,
    qid: str,
    question: str,
    inputs: Any,
    result_dir: Path,
    snapshot_default: str = "",
) -> dict[str, Any]:
    return audit_time_anchor_record(
        qid=qid,
        question=question,
        inputs=inputs,
        sql_code=_read_optional(result_dir / "refer.sql"),
        python_code=_read_optional(result_dir / "refer.py"),
        snapshot_default=snapshot_default,
    )


def write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    Path(path).write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
