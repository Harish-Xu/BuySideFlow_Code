from __future__ import annotations

import ast
import calendar
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeDateViolation:
    location: str
    token: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TimeAnchorContext:
    """Role-aware dates extracted from one benchmark task.

    ``primary`` is retained for backward-compatible reports.  Guard checks use
    the role-specific fields instead: disclosure guards bind to
    ``knowledge_cutoffs`` and state intervals bind to ``valid_at``.
    """

    primary: str
    primary_source: str
    valid_at: tuple[str, ...] = ()
    knowledge_cutoffs: tuple[str, ...] = ()
    report_periods: tuple[str, ...] = ()
    execution_dates: tuple[str, ...] = ()
    label_end_dates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
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
    r"(?P<cn>20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?)|"
    r"(?P<short_cn>(?<!\d)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?)"
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
_YEAR_RE = re.compile(r"(?<!\d)20\d{2}(?:\s*年)?")
_FINANCIAL_PUBLISH_FIELDS: dict[str, tuple[str, ...]] = {
    "fin_derivative": ("INFOPUBLDATE",),
    "lc_balancesheetall": ("INFOPUBLDATE",),
    "lc_derivativedata": ("INFOPUBLDATE",),
    "lc_maindatanew": ("INFOPUBLDATE",),
    "lc_mainindexnew": ("INFOPUBLDATE",),
    "lc_mainoperincome": ("INFOPUBLDATE",),
    "lc_mainshlistnew": ("INFOPUBLDATE",),
    "lc_qfinancialindexnew": ("INFOPUBLDATE",),
    # nq_mainindexnew has no INFOPUBLDATE in the current schema.
    "nq_mainindexnew": ("INSERTTIME",),
    # mf_scaleanalysis exposes only the vendor timestamp INSERTTIME.  It can
    # bound the database snapshot, but cannot prove public-information time.
    "mf_scaleanalysis": ("INSERTTIME",),
}
_FINANCIAL_DEGRADED_PUBLISH_TABLES = frozenset({"nq_mainindexnew", "mf_scaleanalysis"})
_FINANCIAL_TABLE_RE = re.compile(
    rf"\b(?:{'|'.join(map(re.escape, _FINANCIAL_PUBLISH_FIELDS))})\b",
    re.IGNORECASE,
)
_CS_RISKALERT_RE = re.compile(r"\bcs_riskalert\b", re.IGNORECASE)
_INDEX_SNAPSHOT_TABLE_RE = re.compile(r"\blc_indexcomponent\b", re.IGNORECASE)
_INFOPUBL_FIELD = r"(?:\b\w+\.)?INFOPUBL(?:DATE|TIME)\b"
_INFOPUBL_DATE_EXPR = rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*{_INFOPUBL_FIELD}\s*\)|{_INFOPUBL_FIELD})"
_INFOPUBL_GUARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{_INFOPUBL_DATE_EXPR}\s*(?:<=|<)", re.IGNORECASE),
    re.compile(rf"(?:<=|<)\s*[^;\n]{{0,200}}{_INFOPUBL_DATE_EXPR}", re.IGNORECASE),
    re.compile(rf"{_INFOPUBL_DATE_EXPR}\s+BETWEEN\s+[^;\n]{{1,160}}\s+AND\s+[^;\n]{{1,160}}", re.IGNORECASE),
)
_CS_RISKALERT_INFORMATION_REQUIRED_RE = re.compile(
    r"(?:ST|\*ST|风险警示|退市风险)[^。；;\n]{0,24}(?:实施公告|撤销公告|公告时点|公告信息|已公告)|"
    r"(?:实施公告|撤销公告|公告时点|已公告(?:的)?(?:信息|状态)?)[^。；;\n]{0,24}(?:ST|\*ST|风险警示|退市风险)",
    re.IGNORECASE,
)
_REPORT_PERIOD_SHAREHOLDER_DISCLOSURE_RE = re.compile(
    r"(?:基于|按照|按)\s*20\d{2}\s*(?:[-/.年]\s*\d{1,2}){2}\s*日?\s*(?:的)?报告期"
    r"[^。；;\n]{0,120}(?:股东|持仓)[^。；;\n]{0,120}(?:披露|名单)",
    re.IGNORECASE,
)
_HISTORICAL_INFORMATION_SET_RE = re.compile(
    r"截至|观察日|观测日|事件日|当时(?:可得|已知)|可获得的数据|最新已披露|披露日前|回测|复盘|回看",
    re.IGNORECASE,
)
_FUTURE_LABEL_RE = re.compile(
    r"未来|下一月|realized|next[_\s-]?(?:month|return)|离线评估|已可验证",
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
_HISTORICAL_ANCHOR_TOKEN_RE = re.compile(
    r"\b(?:as_of_date|observation_date|anchor_date|target_date|end_date)\b|截至|观察日|观测日|复盘|回看|历史",
    re.IGNORECASE,
)
_SQL_ALIAS_STOP_WORDS = {
    "where", "join", "left", "right", "inner", "outer", "full", "cross", "on", "group", "order",
    "having", "limit", "union", "window", "qualify", "set", "select",
}
_TEMPORAL_INTERVAL_FIELDS: dict[str, tuple[str, str]] = {
    "bond_conceptnature": ("BEGINDATE", "ENDDATE"),
    "lc_exgindustry": ("INFOPUBLDATE", "CANCELDATE"),
    "lc_indexcomponent": ("INDATE", "OUTDATE"),
    "lc_stibexgindustry": ("EFFECTIVEDATE", "CANCELDATE"),
    "mf_fundtype": ("EFFECTIVEDATE", "CANCELDATE"),
    "mf_jyfundtype": ("EFFECTIVEDATE", "CANCELDATE"),
    "nq_exgindustry": ("EFFECTIVEDATE", "UNEFFECTDATE"),
}
_CLASSIFICATION_VINTAGE_TABLES = frozenset(
    {
        "lc_exgindustry",
        "lc_stibexgindustry",
        "mf_fundtype",
        "mf_jyfundtype",
        "nq_exgindustry",
    }
)
# Tables whose classification valid time is distinct from the time at which
# that classification became knowable.  lc_exgindustry already uses
# INFOPUBLDATE as its only available start boundary.
_CLASSIFICATION_INFORMATION_FIELDS: dict[str, str] = {
    "mf_jyfundtype": "INSERTTIME",
    "mf_fundtype": "INFOPUBLDATE",
    "lc_stibexgindustry": "INFOPUBLDATE",
    "nq_exgindustry": "INFOPUBLDATE",
}
_CLASSIFICATION_DEGRADED_INFORMATION_TABLES = frozenset({"mf_jyfundtype"})
_CLASSIFICATION_VINTAGE_TABLE_RE = re.compile(
    rf"\b(?:{'|'.join(map(re.escape, _CLASSIFICATION_VINTAGE_TABLES))})\b",
    re.IGNORECASE,
)
_BOND_CONCEPT_TABLE_RE = re.compile(r"\bbond_conceptnature\b", re.IGNORECASE)

# Current-state fields are meaningful only on their source tables.  IFMERGED
# is intentionally absent: it selects a financial statement scope rather than
# a current database state.
_CURRENT_STATE_FIELD_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "bond_biindustry": (("IFEXECUTED", "historical_current_classification_filter"),),
    "bond_conceptnature": (("IFEFFECTED", "historical_current_status_filter"),),
    "c_ed_indicatormain": (("INDISTATE", "historical_current_status_filter"),),
    "lc_coconcept": (("INDISTATE", "historical_current_status_filter"),),
    "lc_conceptlist": (("CONCEPTSTATE", "historical_current_status_filter"),),
    "lc_exgindustry": (("IFPERFORMED", "historical_current_classification_filter"),),
    "lc_stibexgindustry": (("IFEXECUTED", "historical_current_classification_filter"),),
    "lc_stibsuspendresume": (("IFEFFECTED", "historical_current_status_filter"),),
    "mf_benchmarkallocation": (("IFEXECUTED", "historical_current_status_filter"),),
    "mf_chargeratenew": (("IFEXECUTED", "historical_current_status_filter"),),
    "mf_fundmanagernew": (("INCUMBENT", "historical_current_incumbent_filter"),),
    "mf_fundprodname": (("IFEFFECTED", "historical_current_name_filter"),),
    "mf_fundtype": (("IFEFFECTED", "historical_current_classification_filter"),),
    "mf_investtargetcriterion": (("IFEXECUTED", "historical_current_status_filter"),),
    "mf_jyfundtype": (("IFEFFECTED", "historical_current_classification_filter"),),
}
_CURRENT_STATE_TABLE_RE = re.compile(
    rf"\b(?:{'|'.join(map(re.escape, _CURRENT_STATE_FIELD_RULES))})\b",
    re.IGNORECASE,
)
# LISTEDSTATE is a database-current entity attribute.  Historical remedies are
# table-specific: A-share securities use listing-event history, while bond and
# Hong Kong securities expose delisting dates; nq_secumain has no equivalent
# history table in the current schema and therefore remains a capability
# boundary.  The audit flag deliberately does not prescribe one universal join.
_LISTED_STATE_TABLES = frozenset({"bond_code", "hk_secumain", "nq_secumain", "secumain"})
_LISTED_STATE_TABLE_RE = re.compile(
    rf"\b(?:{'|'.join(map(re.escape, _LISTED_STATE_TABLES))})\b",
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
    "cs_riskalert_effective_guard_missing",
    "cs_riskalert_information_guard_missing",
    "index_component_guard_missing",
    "current_state_status_filter",
    "listed_state_current_filter",
    "classification_vintage_missing",
    "historical_current_classification_filter",
    "historical_current_incumbent_filter",
    "historical_current_name_filter",
    "historical_current_status_filter",
    "bond_concept_vintage_missing",
)
_PIT_REVIEW_CANDIDATE_ORDER = ("future_label_as_feature_suspected",)
_INPUT_ANCHOR_KEYS = (
    "as_of_date",
    "observation_date",
    "anchor_date",
    "target_date",
    "end_date",
    "snapshot_as_of_date",
)
_KNOWLEDGE_INPUT_KEYS = (
    "knowledge_cutoff",
    "information_cutoff",
    "visible_before",
    "as_of_date",
    "observation_date",
    "anchor_date",
    "target_date",
    "snapshot_as_of_date",
)
_VALID_AT_INPUT_KEYS = (
    "valid_at",
    "as_of_date",
    "observation_date",
    "anchor_date",
    "target_date",
    "snapshot_as_of_date",
    "event_date",
    "signal_date",
    "rebalance_date",
)
_REPORT_PERIOD_INPUT_KEYS = ("report_date", "report_period", "period_end")
_EXECUTION_DATE_INPUT_KEYS = ("execution_date", "formation_date", "trade_date", "rebalance_date")
_LABEL_END_INPUT_KEYS = ("label_end", "label_end_date", "horizon_end", "future_end_date")
_ANCHOR_BINDING_TOKEN_RE = re.compile(
    r"\b(?:as_?of|observation|observe|obs|anchor|target|trade|base|signal|rebalance|event|calc|calculation|"
    r"cutoff|ref|reference|sample_end)_(?:date|dt)\b",
    re.IGNORECASE,
)
_BOUND_PARAMETER_TOKEN_RE = re.compile(
    r"(?:"
    r"(?<!\.)\b(?:start_date|end_date|visible_before|visible_cutoff|knowledge_cutoff|information_cutoff)\b|"
    r"\b(?:p|param|params|cfg|config|input|inputs)\s*\.\s*"
    r"(?:start_date|end_date|visible_before|visible_cutoff|knowledge_cutoff|information_cutoff)\b"
    r")",
    re.IGNORECASE,
)
_VALID_TIME_BINDING_TOKEN_RE = re.compile(
    r"\b(?:"
    r"(?:as_?of|anchor|observation|observe|obs|event|signal|rebalance|screening|adjust|weight|"
    r"report|period|trade|trading|calc|calculation|base|ref|reference|snapshot|year_end|q[1-4])"
    r"_(?:date|day|dt)|"
    r"(?:latest_)?trade_day|tradingday|obs_trade_date|year_end(?:_nav_date)?|"
    r"(?:completion_)?announcement_date|publish_date|publication_date|disclosure_date|"
    r"(?:min|max)_date|end_20\d{2}|visible_before|"
    r"(?:visible|knowledge|information|data)_cutoff|"
    r"(?:as_?of|anchor|observation|trade|end)_next(?:_day)?|next_day|cutoff|reportdate"
    r")\b",
    re.IGNORECASE,
)
_GUARD_STATUS_PRESENT = "present"
_GUARD_STATUS_DEGRADED = "present_degraded"
_GUARD_STATUS_UNANCHORED = "present_unanchored"
_GUARD_STATUS_OVER_RESTRICTIVE = "present_over_restrictive"
_GUARD_STATUS_UNRELIABLE_FALLBACK = "present_unreliable_fallback"


def _normalize_date_literal(value: str) -> str:
    value = re.sub(r"\s+", "", value.strip()).replace("/", "-").replace(".", "-")
    cn_match = re.fullmatch(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?", value)
    if cn_match:
        return f"{int(cn_match.group(1)):04d}-{int(cn_match.group(2)):02d}-{int(cn_match.group(3)):02d}"
    short_cn_match = re.fullmatch(r"(\d{2})年(\d{1,2})月(\d{1,2})日?", value)
    if short_cn_match:
        return f"{2000 + int(short_cn_match.group(1)):04d}-{int(short_cn_match.group(2)):02d}-{int(short_cn_match.group(3)):02d}"
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


def _legacy_extract_time_anchor(
    question: str,
    inputs: Any = None,
    *,
    snapshot_default: str = "",
) -> tuple[str, str]:
    """Original single-anchor heuristic, kept only for in-flight reproducibility."""
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
        return _YEAR_RE.search(question or "").group(0).replace("年", "").strip(), "instruction_year"

    if snapshot_default:
        return snapshot_default, "snapshot_default"

    return "", "missing"


def _input_date_values(inputs: Any, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(inputs, dict):
        return []
    values: list[str] = []
    for key in keys:
        raw = inputs.get(key)
        candidates = raw if isinstance(raw, (list, tuple)) else (raw,)
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            normalized = _normalize_date_literal(candidate)
            if _parse_full_date(normalized) is not None:
                values.append(normalized)
    return values


def _dedupe_dates(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _date_role_context(question: str, start: int, end: int) -> tuple[str, str, str]:
    """Return short left, right, and combined contexts around a date literal."""
    left = question[max(0, start - 24):start]
    right = question[end:min(len(question), end + 36)]
    return left, right, f"{left}{right}"


def _has_knowledge_cutoff_cue(left: str, right: str) -> bool:
    return bool(
        re.search(r"(?:截至|信息截止(?:日)?(?:为)?|披露截止(?:日)?(?:为)?|公告截止(?:日)?(?:为)?)\s*$", left)
        or re.match(r"^\s*(?:为)?(?:信息截止日|披露截止日|公告截止日)", right)
    )


def _has_valid_at_cue(left: str, right: str) -> bool:
    return bool(
        re.search(r"(?:截至|观察日(?:为)?|观测日(?:为)?|统计日(?:为)?|基准日(?:为)?|事件日(?:为)?|计算日(?:为)?)\s*$", left)
        or re.match(r"^[，,、\s]*(?:为)?(?:观察日|观测日|统计日|基准日|事件日|计算日|收盘|盘后|晨会|月会)", right)
    )


def _has_report_period_cue(left: str, right: str) -> bool:
    return bool(
        re.search(r"(?:报告期|会计期|统计期)(?:为)?\s*$", left)
        or re.match(r"^[，,、\s]*(?:报告期|年报|半年报|季报|一季报|三季报|财报)", right)
    )


def _has_execution_date_cue(left: str, right: str) -> bool:
    return bool(
        re.search(r"(?:调仓日|建仓日|买入日|卖出日|执行日|信号日)(?:为)?\s*$", left)
        or re.match(r"^[，,、\s]*(?:为)?(?:调仓日|建仓日|买入日|卖出日|执行日|信号日)", right)
    )


def _has_label_end_cue(left: str, right: str) -> bool:
    return bool(
        re.search(r"(?:持有至|观察至|收益截至|标签截止|horizon(?:_end)?(?:=|至)?)\s*$", left, re.IGNORECASE)
        or re.match(r"^[，,、\s]*(?:为)?(?:持有终点|观察终点|收益终点|标签截止日)", right)
    )


def extract_time_anchors(
    question: str,
    inputs: Any = None,
    *,
    snapshot_default: str = "",
) -> TimeAnchorContext:
    """Extract role-aware task dates without trying to parse the whole question.

    The extractor deliberately uses only high-precision lexical cues.  Dates
    with no reliable role remain available for the legacy ``primary`` field,
    but are not silently treated as both valid time and knowledge time.
    """
    question = question or ""
    valid_at = _input_date_values(inputs, _VALID_AT_INPUT_KEYS)
    knowledge_cutoffs = _input_date_values(inputs, _KNOWLEDGE_INPUT_KEYS)
    report_periods = _input_date_values(inputs, _REPORT_PERIOD_INPUT_KEYS)
    execution_dates = _input_date_values(inputs, _EXECUTION_DATE_INPUT_KEYS)
    label_end_dates = _input_date_values(inputs, _LABEL_END_INPUT_KEYS)
    unclassified: list[str] = []

    # Handle compact enumerations such as "以 T1、T2 和 T3 为观察日".
    # Individual local windows only see the role cue for T3.
    for cue in re.finditer(r"为\s*(?:观察日|观测日|统计日|基准日)", question):
        prefix = question[max(0, cue.start() - 120):cue.start()]
        start = max(prefix.rfind("以"), prefix.rfind("在"))
        if start < 0:
            continue
        valid_at.extend(
            _normalize_date_literal(match.group(0))
            for match in _DATE_LITERAL_RE.finditer(prefix[start + 1:])
        )

    for match in _DATE_LITERAL_RE.finditer(question):
        value = _normalize_date_literal(match.group(0))
        left, right, _ = _date_role_context(question, match.start(), match.end())
        roles_found = False

        # A date in "截至 T 已披露" is both the state observation time and the
        # information cutoff.  A report-period date is not promoted to a
        # knowledge cutoff unless an explicit availability cue is present.
        if _has_knowledge_cutoff_cue(left, right):
            knowledge_cutoffs.append(value)
            roles_found = True
        if _has_valid_at_cue(left, right):
            valid_at.append(value)
            roles_found = True
        if _has_report_period_cue(left, right):
            report_periods.append(value)
            roles_found = True
        if _has_execution_date_cue(left, right):
            execution_dates.append(value)
            valid_at.append(value)
            roles_found = True
        if _has_label_end_cue(left, right):
            label_end_dates.append(value)
            roles_found = True
        if not roles_found:
            unclassified.append(value)

    # Month/year-end expressions usually denote a state snapshot; quarter
    # expressions usually denote a report period.  Context cues can promote a
    # derived date to another role without inventing a calendar dependency.
    for pattern, default_role in (
        (_MONTH_END_ANCHOR_RE, "valid"),
        (_YEAR_END_ANCHOR_RE, "valid"),
        (_QUARTER_ANCHOR_RE, "report"),
    ):
        for match in pattern.finditer(question):
            if pattern is _MONTH_END_ANCHOR_RE:
                value = _month_end_date(int(match.group("year")), int(match.group("month")))
            elif pattern is _YEAR_END_ANCHOR_RE:
                value = _format_date(int(match.group("year")), 12, 31)
            else:
                value = _quarter_end_date(
                    int(match.group("year")),
                    match.group("quarter_q") or match.group("quarter_cn"),
                )
            if not value:
                continue
            left, right, _ = _date_role_context(question, match.start(), match.end())
            if default_role == "valid":
                valid_at.append(value)
            else:
                report_periods.append(value)
            if _has_knowledge_cutoff_cue(left, right):
                knowledge_cutoffs.append(value)
            if _has_valid_at_cue(left, right):
                valid_at.append(value)
            if _has_execution_date_cue(left, right):
                execution_dates.append(value)
                valid_at.append(value)
            if _has_label_end_cue(left, right):
                label_end_dates.append(value)

    valid_tuple = _dedupe_dates(valid_at)
    knowledge_tuple = _dedupe_dates(knowledge_cutoffs)
    report_tuple = _dedupe_dates(report_periods)
    execution_tuple = _dedupe_dates(execution_dates)
    label_tuple = _dedupe_dates(label_end_dates)
    unclassified_tuple = _dedupe_dates(unclassified)

    # An explicit observation/snapshot date is also the default knowledge
    # cutoff unless the task states a separate information cutoff.
    if not knowledge_tuple and valid_tuple:
        knowledge_tuple = valid_tuple

    if knowledge_tuple:
        primary, source = max(knowledge_tuple), "inputs" if _input_date_values(inputs, _KNOWLEDGE_INPUT_KEYS) else "instruction_knowledge"
    elif valid_tuple:
        primary, source = max(valid_tuple), "inputs" if _input_date_values(inputs, _VALID_AT_INPUT_KEYS) else "instruction_valid_at"
    elif unclassified_tuple:
        primary, source = max(unclassified_tuple), "instruction"
    elif report_tuple:
        primary, source = max(report_tuple), "instruction_report_period"
    elif execution_tuple:
        primary, source = max(execution_tuple), "instruction_execution"
    elif label_tuple:
        primary, source = max(label_tuple), "instruction_label_end"
    elif _YEAR_RE.search(question):
        primary = _YEAR_RE.search(question).group(0).replace("年", "").strip()
        source = "instruction_year"
    elif snapshot_default:
        primary, source = snapshot_default, "snapshot_default"
    else:
        primary, source = "", "missing"

    return TimeAnchorContext(
        primary=primary,
        primary_source=source,
        valid_at=valid_tuple,
        knowledge_cutoffs=knowledge_tuple,
        report_periods=report_tuple,
        execution_dates=execution_tuple,
        label_end_dates=label_tuple,
    )


def extract_time_anchor(question: str, inputs: Any = None, *, snapshot_default: str = "") -> tuple[str, str]:
    if os.getenv("TEXT2SQL_TIME_ANCHOR_MODE", "").lower() == "legacy":
        return _legacy_extract_time_anchor(question, inputs, snapshot_default=snapshot_default)
    context = extract_time_anchors(question, inputs, snapshot_default=snapshot_default)
    legacy_anchor, legacy_source = _legacy_extract_time_anchor(
        question,
        inputs,
        snapshot_default=snapshot_default,
    )
    # Preserve the public source label when the selected date did not change;
    # role detail is available through ``extract_time_anchors``.
    source = legacy_source if context.primary == legacy_anchor else context.primary_source
    return context.primary, source


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


def _ast_string_value(node: ast.AST, string_vars: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                rendered = _ast_string_value(value.value, string_vars)
                if not rendered:
                    try:
                        rendered = ast.unparse(value.value)
                    except (AttributeError, ValueError):
                        rendered = "expr"
                parts.append(f"{{{rendered}}}")
        return "".join(parts)
    if isinstance(node, ast.Name):
        return string_vars.get(node.id, "")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _ast_string_value(node.left, string_vars) + _ast_string_value(node.right, string_vars)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _ast_string_value(node.left, string_vars)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _ast_string_value(node.func.value, string_vars)
    return ""


def _python_sql_fragments(
    python_code: str,
    *,
    table_pattern: re.Pattern[str],
) -> list[tuple[str, str]]:
    """Return relevant SQL strings and their assigned DataFrame names."""
    if not python_code.strip():
        return []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return [(python_code, "")] if table_pattern.search(python_code) else []

    string_vars: dict[str, str] = {}
    string_values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = _ast_string_value(node.value, string_vars)
        if not value:
            continue
        string_values.append(value)
        for target in targets:
            if isinstance(target, ast.Name):
                string_vars[target.id] = value

    def is_relevant_sql(value: str) -> bool:
        return bool(
            value
            and table_pattern.search(value)
            and re.search(r"\b(?:SELECT|WITH)\b", value, re.IGNORECASE)
        )

    fragments: list[tuple[str, str]] = []
    assigned_call_ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value_node = node.value
        if not isinstance(value_node, ast.Call):
            continue
        if not value_node.args:
            continue
        sql_text = next(
            (
                rendered
                for argument in value_node.args
                if is_relevant_sql(rendered := _ast_string_value(argument, string_vars))
            ),
            "",
        )
        if not sql_text:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        dataframe_name = next((target.id for target in targets if isinstance(target, ast.Name)), "")
        fragments.append((sql_text, dataframe_name))
        assigned_call_ids.add(id(value_node))

    # Include SQL executed inside wrappers, including ``return query(sql)``.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in assigned_call_ids or not node.args:
            continue
        sql_text = next(
            (
                rendered
                for argument in node.args
                if is_relevant_sql(rendered := _ast_string_value(argument, string_vars))
            ),
            "",
        )
        if sql_text:
            fragments.append((sql_text, ""))

    # Keep complete SQL literals even when execution is delegated to a helper
    # whose call cannot be associated with a result DataFrame statically.
    for sql_text in string_values:
        if is_relevant_sql(sql_text):
            fragments.append((sql_text, ""))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        sql_text = _ast_string_value(node.value, string_vars)
        if is_relevant_sql(sql_text):
            fragments.append((sql_text, ""))

    deduped: dict[str, str] = {}
    for sql_text, dataframe_name in fragments:
        if sql_text not in deduped or (dataframe_name and not deduped[sql_text]):
            deduped[sql_text] = dataframe_name
    return list(deduped.items())


def _table_alias_occurrences(sql_fragment: str, table_name: str) -> list[tuple[str, int]]:
    pattern = re.compile(
        rf"\b(?:FROM|JOIN)\s+`?{re.escape(table_name)}`?"
        r"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_]\w*))?",
        re.IGNORECASE,
    )
    occurrences: list[tuple[str, int]] = []
    for match in pattern.finditer(sql_fragment):
        alias = str(match.group("alias") or "")
        if alias.lower() in _SQL_ALIAS_STOP_WORDS:
            alias = ""
        occurrences.append((alias, match.start()))
    return occurrences


def _sql_select_branch_from(sql: str, position: int) -> str:
    """Return the SELECT/UNION branch containing one table occurrence."""
    masked, depths, parents, closes = _sql_structure(sql)
    if position >= len(masked):
        return sql
    depth = depths[position]
    parent = parents[position]
    scope_start = parent + 1 if parent >= 0 else 0
    start = scope_start
    for match in re.finditer(r"\bSELECT\b", masked[scope_start:position], re.IGNORECASE):
        boundary = scope_start + match.start()
        if depths[boundary] == depth:
            start = boundary
    end = closes.get(parent, len(sql)) if parent >= 0 else len(sql)
    for match in re.finditer(r"\bUNION(?:\s+ALL)?\b", masked[position:end], re.IGNORECASE):
        boundary = position + match.start()
        if depths[boundary] == depth:
            end = boundary
            break
    return sql[start:end]


def _strip_sql_noise(sql: str) -> str:
    """Remove SQL comments while preserving offsets used by match spans."""
    sql = re.sub(r"--[^\n]*", lambda match: " " * len(match.group(0)), sql or "")
    return re.sub(r"/\*.*?\*/", lambda match: " " * len(match.group(0)), sql, flags=re.DOTALL)


def _audit_sql_fragments(
    sql_code: str,
    python_code: str,
    *,
    table_pattern: re.Pattern[str],
) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    if sql_code.strip():
        fragments.append((_strip_sql_noise(sql_code), ""))
    fragments.extend(
        (_strip_sql_noise(sql), dataframe)
        for sql, dataframe in _python_sql_fragments(
            python_code,
            table_pattern=table_pattern,
        )
    )

    # Prefer the fragment that retains the query result's DataFrame name; this
    # permits a same-DataFrame Python filter without counting the SQL twice.
    deduped: dict[str, str] = {}
    for sql, dataframe in fragments:
        if sql not in deduped or (dataframe and not deduped[sql]):
            deduped[sql] = dataframe
    return list(deduped.items())


def _sql_structure(sql: str) -> tuple[str, list[int], list[int], dict[int, int]]:
    masked = re.sub(
        r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"",
        lambda match: " " * len(match.group(0)),
        sql,
        flags=re.DOTALL,
    )
    depths = [0] * len(masked)
    parents = [-1] * len(masked)
    stack: list[int] = []
    closes: dict[int, int] = {}
    for index, char in enumerate(masked):
        depths[index] = len(stack)
        parents[index] = stack[-1] if stack else -1
        if char == "(":
            stack.append(index)
        elif char == ")" and stack:
            closes[stack.pop()] = index
    return masked, depths, parents, closes


def _canonical_open_boundary_expression(
    body: str,
    *,
    field: str,
    comparison_pattern: str,
) -> bool:
    masked, depths, _, _ = _sql_structure(body)
    split_points = [
        match.span()
        for match in re.finditer(r"\bOR\b", masked, re.IGNORECASE)
        if depths[match.start()] == 0
    ]
    if not split_points:
        return False
    branches: list[str] = []
    start = 0
    for left, right in split_points:
        branches.append(body[start:left])
        start = right
    branches.append(body[start:])

    allowed_fields = {field.upper()}
    if field.upper() == "REMOVEDATE":
        allowed_fields.add("REMOVEINFOPUBLDATE")

    def constrains_end(branch: str) -> bool:
        return any(
            re.search(rf"\b{re.escape(name)}\b", branch, re.IGNORECASE)
            for name in allowed_fields
        )

    return bool(
        all(constrains_end(branch) for branch in branches)
        and any(
            re.search(
                rf"\b{re.escape(field)}\b[^;\n]{{0,80}}\bIS\s+NULL\b",
                branch,
                re.IGNORECASE,
            )
            for branch in branches
        )
        and any(re.search(comparison_pattern, branch) for branch in branches)
    )


def _canonical_open_boundary_group(
    sql: str,
    *,
    or_position: int,
    parents: list[int],
    closes: dict[int, int],
    field: str,
    comparison_pattern: str,
) -> bool:
    parent = parents[or_position]
    if parent < 0 or parent not in closes:
        return False
    return _canonical_open_boundary_expression(
        sql[parent + 1:closes[parent]],
        field=field,
        comparison_pattern=comparison_pattern,
    )


def _guard_in_unsafe_or(
    sql: str,
    span: tuple[int, int],
    *,
    open_start_field: str = "",
    open_end_field: str = "",
) -> bool:
    """Detect a guard placed in a disjunct that does not constrain the full row set."""
    masked, depths, parents, closes = _sql_structure(sql)
    if not masked or span[0] >= len(masked):
        return False
    guard_depth = depths[span[0]]
    guard_parent = parents[span[0]]

    scope_start = 0
    for match in re.finditer(r"\b(?:WHERE|ON|HAVING)\b", masked[:span[0]], re.IGNORECASE):
        if depths[match.start()] <= guard_depth:
            scope_start = match.end()

    scope_parent = parents[scope_start] if scope_start < len(parents) else -1
    scope_end = closes.get(scope_parent, len(masked)) if scope_parent >= 0 else len(masked)
    for match in re.finditer(
        r"\b(?:JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|QUALIFY)\b|;",
        masked[span[1]:scope_end],
        re.IGNORECASE,
    ):
        position = span[1] + match.start()
        scope_depth = depths[scope_start] if scope_start < len(depths) else 0
        if depths[position] <= scope_depth:
            scope_end = position
            break

    for match in re.finditer(r"\bOR\b", masked[scope_start:scope_end], re.IGNORECASE):
        position = scope_start + match.start()
        same_group = depths[position] == guard_depth and parents[position] == guard_parent
        ancestor_group = depths[position] < guard_depth
        if not (same_group or ancestor_group):
            continue
        open_boundary_field = open_start_field or open_end_field
        comparison_pattern = r"(?:<=|<)" if open_start_field else r"(?:>=|>)"
        if open_boundary_field and _canonical_open_boundary_group(
            sql,
            or_position=position,
            parents=parents,
            closes=closes,
            field=open_boundary_field,
            comparison_pattern=comparison_pattern,
        ):
            continue
        if open_boundary_field and _canonical_open_boundary_expression(
            sql[scope_start:scope_end],
            field=open_boundary_field,
            comparison_pattern=comparison_pattern,
        ):
            continue
        return True
    return False


def _qualified_sql_field(table_name: str, alias: str, field: str) -> str:
    if alias:
        qualifier = rf"`?{re.escape(alias)}`?\s*\.\s*"
    else:
        qualifier = rf"(?:`?{re.escape(table_name)}`?\s*\.\s*)?"
    base = rf"{qualifier}`?{re.escape(field)}`?"
    return rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*{base}\s*\)|{base})"


def _safe_sql_guard_matches(
    sql: str,
    pattern: re.Pattern[str],
    *,
    open_start_field: str = "",
    open_end_field: str = "",
) -> list[re.Match[str]]:
    return [
        match
        for match in pattern.finditer(sql)
        if not _guard_in_unsafe_or(
            sql,
            match.span(),
            open_start_field=open_start_field,
            open_end_field=open_end_field,
        )
    ]


def _parse_full_date(value: str) -> date | None:
    normalized = _normalize_date_literal(value)
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", normalized):
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _guard_bound_status(
    bound_expression: str,
    *,
    time_anchor: str,
    strict_upper: bool = False,
) -> str:
    """Classify whether an upper information-time bound is tied to the task anchor."""
    if (
        _ANCHOR_BINDING_TOKEN_RE.search(bound_expression or "")
        or _VALID_TIME_BINDING_TOKEN_RE.search(bound_expression or "")
        or _BOUND_PARAMETER_TOKEN_RE.search(bound_expression or "")
    ):
        return _GUARD_STATUS_PRESENT

    anchor_date = _parse_full_date(time_anchor)
    literal_dates = [
        parsed
        for match in _DATE_LITERAL_RE.finditer(bound_expression or "")
        if (parsed := _parse_full_date(match.group(0))) is not None
    ]
    if anchor_date is None or not literal_dates:
        return _GUARD_STATUS_UNANCHORED

    bound_date = literal_dates[-1]
    delta_days = (bound_date - anchor_date).days
    if delta_days == 0 or (strict_upper and delta_days == 1):
        return _GUARD_STATUS_PRESENT
    if delta_days < 0:
        return _GUARD_STATUS_OVER_RESTRICTIVE
    return _GUARD_STATUS_UNANCHORED


def _best_guard_status(statuses: list[str]) -> str:
    if _GUARD_STATUS_PRESENT in statuses:
        return _GUARD_STATUS_PRESENT
    if _GUARD_STATUS_DEGRADED in statuses:
        return _GUARD_STATUS_DEGRADED
    if _GUARD_STATUS_UNRELIABLE_FALLBACK in statuses:
        return _GUARD_STATUS_UNRELIABLE_FALLBACK
    if _GUARD_STATUS_UNANCHORED in statuses:
        return _GUARD_STATUS_UNANCHORED
    if _GUARD_STATUS_OVER_RESTRICTIVE in statuses:
        return _GUARD_STATUS_OVER_RESTRICTIVE
    return "missing"


def _required_guard_status(statuses: list[str]) -> str:
    """Combine guards that must all be present, preserving the worst outcome."""
    if "missing" in statuses:
        return "missing"
    if _GUARD_STATUS_UNANCHORED in statuses:
        return _GUARD_STATUS_UNANCHORED
    if _GUARD_STATUS_OVER_RESTRICTIVE in statuses:
        return _GUARD_STATUS_OVER_RESTRICTIVE
    if _GUARD_STATUS_UNRELIABLE_FALLBACK in statuses:
        return _GUARD_STATUS_UNRELIABLE_FALLBACK
    if _GUARD_STATUS_DEGRADED in statuses:
        return _GUARD_STATUS_DEGRADED
    return _GUARD_STATUS_PRESENT


def _guard_fallback_quality(status: str, expression: str) -> str:
    """Downgrade COALESCE fallbacks that are not reliable first-availability time."""
    if status != _GUARD_STATUS_PRESENT or not re.search(r"\bCOALESCE\s*\(", expression, re.IGNORECASE):
        return status
    if re.search(r"\b(?:UPDATETIME|XGRQ)\b", expression, re.IGNORECASE):
        return _GUARD_STATUS_UNRELIABLE_FALLBACK
    if re.search(r"\bINSERTTIME\b", expression, re.IGNORECASE):
        return _GUARD_STATUS_DEGRADED
    return status


def _sql_field_guard_status(
    sql_fragment: str,
    *,
    alias: str,
    field: str,
    allow_unqualified: bool,
    time_anchor: str,
) -> str:
    qualified = rf"(?:`?{re.escape(alias)}`?\s*\.\s*)?" if not alias else rf"`?{re.escape(alias)}`?\s*\.\s*"
    direct_field_expr = (
        rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*{qualified}`?{re.escape(field)}`?\s*\)|"
        rf"{qualified}`?{re.escape(field)}`?)"
    )
    coalesced_field_expr = (
        rf"(?:\b(?:DATE|TIMESTAMP)\s*\(\s*)?\bCOALESCE\s*\("
        rf"[^;\n]{{0,160}}{qualified}`?{re.escape(field)}`?[^;\n]{{0,160}}\)\s*\)?"
    )
    field_expr = rf"(?:{direct_field_expr}|{coalesced_field_expr})"
    statuses: list[str] = []

    def append_with_fallback_quality(status: str, matched_expression: str) -> None:
        statuses.append(_guard_fallback_quality(status, matched_expression))

    forward_pattern = re.compile(rf"{field_expr}\s*(?P<operator><=|<)", re.IGNORECASE)
    for match in forward_pattern.finditer(sql_fragment):
        tail = sql_fragment[match.end(): match.end() + 240]
        bound = re.split(
            r"(?:[;\n]|\)\s*,|\s+\b(?:AND|OR|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|QUALIFY)\b)",
            tail,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        append_with_fallback_quality(
            _guard_bound_status(
                bound,
                time_anchor=time_anchor,
                strict_upper=match.group("operator") == "<",
            ),
            match.group(0),
        )

    reverse_pattern = re.compile(
        rf"(?P<bound>(?:\b(?:DATE|TIMESTAMP|DATE_ADD|DATE_SUB|COALESCE|LEAST|GREATEST)\s*\([^;\n]{{1,200}}\)|"
        rf"['\"]20\d{{2}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}['\"]|"
        rf"(?:[:@]?\w+(?:\.\w+)?)))\s*(?P<operator>>=|>)\s*{field_expr}",
        re.IGNORECASE,
    )
    for match in reverse_pattern.finditer(sql_fragment):
        append_with_fallback_quality(
            _guard_bound_status(
                match.group("bound"),
                time_anchor=time_anchor,
                strict_upper=match.group("operator") == ">",
            ),
            match.group(0),
        )

    between_pattern = re.compile(
        rf"{field_expr}\s+BETWEEN\s+[^;\n]{{1,160}}?\s+AND\s+(?P<bound>[^;\n]{{1,200}})",
        re.IGNORECASE,
    )
    for match in between_pattern.finditer(sql_fragment):
        bound = re.split(
            r"\s+\b(?:AND|OR|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|QUALIFY)\b",
            match.group("bound"),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        append_with_fallback_quality(
            _guard_bound_status(bound, time_anchor=time_anchor),
            match.group(0),
        )

    if statuses:
        return _best_guard_status(statuses)
    if alias or not allow_unqualified:
        return "missing"
    if any(pattern.search(sql_fragment) for pattern in _INFOPUBL_GUARD_PATTERNS):
        return _GUARD_STATUS_UNANCHORED
    return "missing"


def _python_dataframe_guard_status(
    python_code: str,
    *,
    dataframe_name: str,
    fields: tuple[str, ...],
    time_anchor: str,
) -> str:
    if not dataframe_name:
        return "missing"
    statuses: list[str] = []
    for field in fields:
        field_ref = rf"\b{re.escape(dataframe_name)}\s*\[\s*['\"]{re.escape(field)}['\"]\s*\]"
        forward_pattern = re.compile(rf"{field_ref}\s*(?P<operator><=|<)", re.IGNORECASE)
        for match in forward_pattern.finditer(python_code):
            bound = re.split(r"[\n&|]", python_code[match.end(): match.end() + 200], maxsplit=1)[0]
            statuses.append(
                _guard_bound_status(
                    bound,
                    time_anchor=time_anchor,
                    strict_upper=match.group("operator") == "<",
                )
            )
        reverse_pattern = re.compile(
            rf"(?P<bound>[^\n&|]{{1,160}}?)\s*(?P<operator>>=|>)\s*{field_ref}",
            re.IGNORECASE,
        )
        statuses.extend(
            _guard_bound_status(
                match.group("bound"),
                time_anchor=time_anchor,
                strict_upper=match.group("operator") == ">",
            )
            for match in reverse_pattern.finditer(python_code)
        )
    return _best_guard_status(statuses)


def _sql_alias_is_exact_key_rejoin(
    sql: str,
    *,
    table_name: str,
    alias: str,
) -> bool:
    qualifier = rf"`?{re.escape(alias)}`?" if alias else rf"`?{re.escape(table_name)}`?"
    own_key = rf"{qualifier}\s*\.\s*`?(?:JSID|ID)`?"
    other_key = r"`?[A-Za-z_]\w*`?\s*\.\s*`?(?:MAX_)?(?:JSID|ID)`?"
    return bool(
        re.search(rf"{own_key}\s*=\s*{other_key}", sql, re.IGNORECASE)
        or re.search(rf"{other_key}\s*=\s*{own_key}", sql, re.IGNORECASE)
    )


def _report_period_shareholder_disclosure_uses_locked_snapshot(question: str, table_name: str) -> bool:
    """Whether a shareholder query describes a report period rather than a historical information set."""
    if table_name != "lc_mainshlistnew":
        return False
    return bool(
        _REPORT_PERIOD_SHAREHOLDER_DISCLOSURE_RE.search(question or "")
        and not _HISTORICAL_INFORMATION_SET_RE.search(question or "")
    )


def financial_publish_guard_status(
    sql_code: str,
    python_code: str,
    *,
    question: str = "",
    time_anchor: str = "",
) -> str:
    if time_anchor:
        resolved_anchor = time_anchor
    elif os.getenv("TEXT2SQL_TIME_ANCHOR_MODE", "").lower() == "legacy":
        resolved_anchor = _legacy_extract_time_anchor(question or "")[0]
    else:
        anchor_context = extract_time_anchors(question or "")
        resolved_anchor = max(anchor_context.knowledge_cutoffs) if anchor_context.knowledge_cutoffs else ""
    matched_table = False
    applicable = False
    required_statuses: list[str] = []
    for sql_fragment, dataframe_name in _audit_sql_fragments(
        sql_code,
        python_code,
        table_pattern=_FINANCIAL_TABLE_RE,
    ):
        for table_name, fields in _FINANCIAL_PUBLISH_FIELDS.items():
            if not re.search(rf"\b{re.escape(table_name)}\b", sql_fragment, re.IGNORECASE):
                continue
            matched_table = True
            if _report_period_shareholder_disclosure_uses_locked_snapshot(question, table_name):
                continue
            applicable = True
            occurrences = _table_alias_occurrences(sql_fragment, table_name)
            if not occurrences:
                required_statuses.append("missing")
                continue
            python_guard = _python_dataframe_guard_status(
                python_code,
                dataframe_name=dataframe_name,
                fields=fields,
                time_anchor=resolved_anchor,
            )
            occurrence_statuses: list[tuple[str, str]] = []
            for alias, position in occurrences:
                local_fragment = _sql_select_branch_from(sql_fragment, position)
                guard_status = _best_guard_status(
                    [python_guard]
                    + [
                        _sql_field_guard_status(
                            local_fragment,
                            alias=alias,
                            field=field,
                            allow_unqualified=not alias,
                            time_anchor=resolved_anchor,
                        )
                        for field in fields
                    ]
                )
                occurrence_statuses.append((local_fragment, guard_status))

            inherited_status = _best_guard_status(
                [status for _, status in occurrence_statuses if status != "missing"]
            )
            for (alias, _), (local_fragment, guard_status) in zip(
                occurrences,
                occurrence_statuses,
            ):
                if (
                    guard_status == "missing"
                    and inherited_status != "missing"
                    and _sql_alias_is_exact_key_rejoin(
                        local_fragment,
                        table_name=table_name,
                        alias=alias,
                    )
                ):
                    guard_status = inherited_status
                if (
                    table_name in _FINANCIAL_DEGRADED_PUBLISH_TABLES
                    and guard_status == _GUARD_STATUS_PRESENT
                ):
                    guard_status = _GUARD_STATUS_DEGRADED
                required_statuses.append(guard_status)
    if not applicable:
        return "not_required" if matched_table else "not_applicable"
    return _required_guard_status(required_statuses)


def _guard_bound_status_for_anchors(
    bound_expression: str,
    *,
    time_anchors: tuple[str, ...],
    strict_upper: bool = False,
) -> str:
    if _VALID_TIME_BINDING_TOKEN_RE.search(bound_expression or ""):
        return _GUARD_STATUS_PRESENT
    if not time_anchors:
        return _GUARD_STATUS_PRESENT
    return _best_guard_status(
        [
            _guard_bound_status(
                bound_expression,
                time_anchor=anchor,
                strict_upper=strict_upper,
            )
            for anchor in time_anchors
        ]
    )


def _python_dataframe_interval_status(
    python_code: str,
    *,
    dataframe_name: str,
    start_field: str,
    end_field: str,
    time_anchors: tuple[str, ...],
) -> str:
    if not dataframe_name:
        return "missing"
    start_ref = rf"\b{re.escape(dataframe_name)}\s*\[\s*['\"]{re.escape(start_field)}['\"]\s*\]"
    end_ref = rf"\b{re.escape(dataframe_name)}\s*\[\s*['\"]{re.escape(end_field)}['\"]\s*\]"
    start_statuses: list[str] = []
    end_statuses: list[str] = []
    for field_ref, operators, destination, boundary_kind in (
        (start_ref, r"<=|<", start_statuses, "start"),
        (end_ref, r">=|>", end_statuses, "end"),
    ):
        pattern = re.compile(rf"{field_ref}\s*(?P<operator>{operators})", re.IGNORECASE)
        for match in pattern.finditer(python_code):
            bound = re.split(r"[\n&|]", python_code[match.end(): match.end() + 200], maxsplit=1)[0]
            destination.append(
                _guard_bound_status_for_anchors(
                    bound,
                    time_anchors=time_anchors,
                    strict_upper=(
                        boundary_kind == "start" and match.group("operator") == "<"
                    ) or (
                        boundary_kind == "end" and match.group("operator") == ">="
                    ),
                )
            )
    if not start_statuses or not end_statuses:
        return "missing"
    return _required_guard_status([_best_guard_status(start_statuses), _best_guard_status(end_statuses)])


def _python_interval_status(
    python_code: str,
    *,
    dataframe_name: str,
    sql_fragment: str,
    start_field: str,
    end_field: str,
    time_anchors: tuple[str, ...],
) -> str:
    status = _python_dataframe_interval_status(
        python_code,
        dataframe_name=dataframe_name,
        start_field=start_field,
        end_field=end_field,
        time_anchors=time_anchors,
    )
    if status != "missing":
        return status
    if not re.search(
        rf"\b{re.escape(start_field)}\b.*\b{re.escape(end_field)}\b",
        sql_fragment,
        re.IGNORECASE | re.DOTALL,
    ):
        return "missing"
    dataframe_candidates = set(
        re.findall(
            rf"\b([A-Za-z_]\w*)\s*\[\s*['\"](?:{re.escape(start_field)}|{re.escape(end_field)})['\"]\s*\]",
            python_code,
            re.IGNORECASE,
        )
    )
    return _best_guard_status(
        [
            _python_dataframe_interval_status(
                python_code,
                dataframe_name=candidate,
                start_field=start_field,
                end_field=end_field,
                time_anchors=time_anchors,
            )
            for candidate in dataframe_candidates
        ]
    )


def _sql_alias_interval_status(
    sql: str,
    *,
    table_name: str,
    alias: str,
    start_field: str,
    end_field: str,
    time_anchors: tuple[str, ...],
) -> str:
    start_expr = _qualified_sql_field(table_name, alias, start_field)
    end_expr = _qualified_sql_field(table_name, alias, end_field)
    start_pattern = re.compile(rf"{start_expr}\s*(?P<operator><=|<)", re.IGNORECASE)
    end_pattern = re.compile(rf"{end_expr}\s*(?P<operator>>=|>)", re.IGNORECASE)
    start_statuses: list[str] = []
    end_statuses: list[str] = []
    for pattern, destination, boundary_kind in (
        (start_pattern, start_statuses, "start"),
        (end_pattern, end_statuses, "end"),
    ):
        for match in _safe_sql_guard_matches(
            sql,
            pattern,
            open_start_field=start_field if pattern is start_pattern else "",
            open_end_field=end_field if pattern is end_pattern else "",
        ):
            tail = sql[match.end(): match.end() + 240]
            bound = re.split(
                r"(?:[;\n]|\)\s*,|\s+\b(?:AND|OR|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|QUALIFY)\b)",
                tail,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            destination.append(
                _guard_bound_status_for_anchors(
                    bound,
                    time_anchors=time_anchors,
                    strict_upper=(
                        boundary_kind == "start" and match.group("operator") == "<"
                    ) or (
                        boundary_kind == "end" and match.group("operator") == ">="
                    ),
                )
            )
    if not start_statuses or not end_statuses:
        return "missing"
    return _required_guard_status([_best_guard_status(start_statuses), _best_guard_status(end_statuses)])


def _registered_interval_guard_status(
    sql_code: str,
    python_code: str,
    *,
    table_names: tuple[str, ...],
    table_pattern: re.Pattern[str],
    valid_at: tuple[str, ...] = (),
) -> str:
    matched = False
    statuses: list[str] = []
    for fragment, dataframe_name in _audit_sql_fragments(
        sql_code,
        python_code,
        table_pattern=table_pattern,
    ):
        for table_name in table_names:
            if not re.search(rf"\b{re.escape(table_name)}\b", fragment, re.IGNORECASE):
                continue
            matched = True
            occurrences = _table_alias_occurrences(fragment, table_name)
            if not occurrences:
                statuses.append("missing")
                continue
            start_field, end_field = _TEMPORAL_INTERVAL_FIELDS[table_name]
            for alias, position in occurrences:
                local_fragment = _sql_select_branch_from(fragment, position)
                statuses.append(
                    _best_guard_status(
                        [
                            _sql_alias_interval_status(
                                local_fragment,
                                table_name=table_name,
                                alias=alias,
                                start_field=start_field,
                                end_field=end_field,
                                time_anchors=valid_at,
                            ),
                            _python_interval_status(
                                python_code,
                                dataframe_name=dataframe_name,
                                sql_fragment=local_fragment,
                                start_field=start_field,
                                end_field=end_field,
                                time_anchors=valid_at,
                            ),
                        ]
                    )
                )
    if not matched:
        return "not_applicable"
    return _required_guard_status(statuses) if statuses else "missing"


def _information_quality_status(status: str, field_status: str) -> str:
    if status != _GUARD_STATUS_PRESENT:
        return status
    return field_status


def _sql_alias_information_guard_status(
    sql: str,
    *,
    table_name: str,
    alias: str,
    time_anchor: str = "",
) -> str:
    statuses: list[str] = []
    for field, field_status in (
        ("IMPLANNOUCEDATE", _GUARD_STATUS_PRESENT),
        ("REMOVEINFOPUBLDATE", _GUARD_STATUS_PRESENT),
        ("INSERTTIME", _GUARD_STATUS_DEGRADED),
    ):
        field_expr = _qualified_sql_field(table_name, alias, field)
        open_end_field = field if field == "REMOVEINFOPUBLDATE" else ""
        forward = re.compile(rf"{field_expr}\s*(?P<operator><=|<)", re.IGNORECASE)
        for match in _safe_sql_guard_matches(sql, forward, open_end_field=open_end_field):
            tail = sql[match.end(): match.end() + 240]
            bound = re.split(
                r"(?:[;\n]|\)\s*,|\s+\b(?:AND|OR|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|QUALIFY)\b)",
                tail,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            status = (
                _guard_bound_status(bound, time_anchor=time_anchor, strict_upper=match.group("operator") == "<")
                if time_anchor
                else _GUARD_STATUS_PRESENT
            )
            statuses.append(
                _guard_fallback_quality(
                    _information_quality_status(status, field_status),
                    match.group(0),
                )
            )

        reverse = re.compile(
            rf"(?P<bound>(?:\b(?:DATE|TIMESTAMP|DATE_ADD|DATE_SUB|COALESCE|LEAST|GREATEST)\s*\([^;\n]{{1,200}}\)|"
            rf"['\"]20\d{{2}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}['\"]|(?:[:@]?\w+(?:\.\w+)?)))"
            rf"\s*(?P<operator>>=|>)\s*{field_expr}",
            re.IGNORECASE,
        )
        for match in _safe_sql_guard_matches(sql, reverse, open_end_field=open_end_field):
            status = (
                _guard_bound_status(
                    match.group("bound"),
                    time_anchor=time_anchor,
                    strict_upper=match.group("operator") == ">",
                )
                if time_anchor
                else _GUARD_STATUS_PRESENT
            )
            statuses.append(
                _guard_fallback_quality(
                    _information_quality_status(status, field_status),
                    match.group(0),
                )
            )

        qualifier = rf"`?{re.escape(alias)}`?\s*\.\s*" if alias else rf"(?:`?{re.escape(table_name)}`?\s*\.\s*)?"
        coalesce = re.compile(
            rf"\bCOALESCE\s*\([^;\n]{{0,240}}{qualifier}`?{re.escape(field)}`?[^;\n]{{0,240}}\)"
            r"\s*(?P<operator><=|<)",
            re.IGNORECASE,
        )
        for match in _safe_sql_guard_matches(sql, coalesce, open_end_field=open_end_field):
            tail = sql[match.end(): match.end() + 240]
            bound = re.split(r"(?:[;\n]|\s+\b(?:AND|OR|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b)", tail, maxsplit=1, flags=re.IGNORECASE)[0]
            status = (
                _guard_bound_status(bound, time_anchor=time_anchor, strict_upper=match.group("operator") == "<")
                if time_anchor
                else _GUARD_STATUS_PRESENT
            )
            statuses.append(
                _guard_fallback_quality(
                    _information_quality_status(status, field_status),
                    match.group(0),
                )
            )
    return _best_guard_status(statuses)


def _python_dataframe_information_guard_status(
    python_code: str,
    *,
    dataframe_name: str,
    time_anchor: str = "",
) -> str:
    if not dataframe_name:
        return "missing"
    statuses: list[str] = []
    for field, field_status in (
        ("IMPLANNOUCEDATE", _GUARD_STATUS_PRESENT),
        ("REMOVEINFOPUBLDATE", _GUARD_STATUS_PRESENT),
        ("INSERTTIME", _GUARD_STATUS_DEGRADED),
    ):
        field_ref = rf"\b{re.escape(dataframe_name)}\s*\[\s*['\"]{field}['\"]\s*\]"
        pattern = re.compile(rf"{field_ref}\s*(?P<operator><=|<)", re.IGNORECASE)
        for match in pattern.finditer(python_code):
            bound = re.split(r"[\n&|]", python_code[match.end(): match.end() + 200], maxsplit=1)[0]
            status = (
                _guard_bound_status(bound, time_anchor=time_anchor, strict_upper=match.group("operator") == "<")
                if time_anchor
                else _GUARD_STATUS_PRESENT
            )
            statuses.append(_information_quality_status(status, field_status))
    return _best_guard_status(statuses)


def _sql_alias_is_delisting_removal_evidence(sql: str, *, alias: str) -> bool:
    """Recognize a risk-alert alias used only as dated delisting-removal evidence."""
    qualifier = rf"`?{re.escape(alias)}`?\s*\.\s*" if alias else r"(?:`?cs_riskalert`?\s*\.\s*)?"
    risk_type_9 = re.compile(
        rf"{qualifier}`?RISKALERTTYPE`?\s*(?:=\s*9\b|IN\s*\(\s*9\s*\))",
        re.IGNORECASE,
    )
    remove_date = _qualified_sql_field("cs_riskalert", alias, "REMOVEDATE")
    remove_date_bound = re.compile(rf"{remove_date}\s*(?:<=|<)", re.IGNORECASE)
    remove_publish_date = _qualified_sql_field("cs_riskalert", alias, "REMOVEINFOPUBLDATE")
    remove_publish_bound = re.compile(rf"{remove_publish_date}\s*(?:<=|<)", re.IGNORECASE)
    coalesced_publish_bound = re.compile(
        rf"\bCOALESCE\s*\([^;\n]{{0,240}}{qualifier}`?REMOVEINFOPUBLDATE`?[^;\n]{{0,240}}\)"
        r"\s*(?:<=|<)",
        re.IGNORECASE,
    )
    return bool(
        risk_type_9.search(sql)
        and remove_date_bound.search(sql)
        and (remove_publish_bound.search(sql) or coalesced_publish_bound.search(sql))
    )


def cs_riskalert_effective_guard_status(
    sql_code: str,
    python_code: str,
    *,
    valid_at: tuple[str, ...] = (),
) -> str:
    matched = False
    statuses: list[str] = []
    for fragment, dataframe_name in _audit_sql_fragments(
        sql_code,
        python_code,
        table_pattern=_CS_RISKALERT_RE,
    ):
        if not _CS_RISKALERT_RE.search(fragment):
            continue
        matched = True
        occurrences = _table_alias_occurrences(fragment, "cs_riskalert")
        if not occurrences:
            statuses.append("missing")
            continue
        for alias, position in occurrences:
            local_fragment = _sql_select_branch_from(fragment, position)
            # RISKALERTTYPE=9 rows bounded by both removal date and its
            # publication date are evidence that a delisting state had ended;
            # they are not an as-of ST-state lookup and need no IMPLEMENTDATE
            # interval of their own.
            if _sql_alias_is_delisting_removal_evidence(local_fragment, alias=alias):
                continue
            statuses.append(
                _best_guard_status(
                    [
                        _sql_alias_interval_status(
                            local_fragment,
                            table_name="cs_riskalert",
                            alias=alias,
                            start_field="IMPLEMENTDATE",
                            end_field="REMOVEDATE",
                            time_anchors=valid_at,
                        ),
                        _python_interval_status(
                            python_code,
                            dataframe_name=dataframe_name,
                            sql_fragment=local_fragment,
                            start_field="IMPLEMENTDATE",
                            end_field="REMOVEDATE",
                            time_anchors=valid_at,
                        ),
                    ]
                )
            )
    if not matched:
        return "missing" if _CS_RISKALERT_RE.search(_strip_sql_noise(python_code)) else "not_applicable"
    if not statuses:
        return "not_applicable"
    return _required_guard_status(statuses)


def cs_riskalert_information_guard_status(
    question: str,
    sql_code: str,
    python_code: str,
    *,
    time_anchor: str = "",
) -> str:
    if not _CS_RISKALERT_INFORMATION_REQUIRED_RE.search(question or ""):
        combined = f"{_strip_sql_noise(sql_code)}\n{_strip_sql_noise(python_code)}"
        return "not_required" if _CS_RISKALERT_RE.search(combined) else "not_applicable"
    matched = False
    statuses: list[str] = []
    for fragment, dataframe_name in _audit_sql_fragments(
        sql_code,
        python_code,
        table_pattern=_CS_RISKALERT_RE,
    ):
        if not _CS_RISKALERT_RE.search(fragment):
            continue
        matched = True
        occurrences = _table_alias_occurrences(fragment, "cs_riskalert")
        if not occurrences:
            statuses.append("missing")
            continue
        for alias, position in occurrences:
            local_fragment = _sql_select_branch_from(fragment, position)
            statuses.append(
                _best_guard_status(
                    [
                        _sql_alias_information_guard_status(
                            local_fragment,
                            table_name="cs_riskalert",
                            alias=alias,
                            time_anchor=time_anchor,
                        ),
                        _python_dataframe_information_guard_status(
                            python_code,
                            dataframe_name=dataframe_name,
                            time_anchor=time_anchor,
                        ),
                    ]
                )
            )
    if not matched:
        return "missing" if _CS_RISKALERT_RE.search(_strip_sql_noise(python_code)) else "not_applicable"
    return _required_guard_status(statuses)


def _index_snapshot_guard_status(
    sql_code: str,
    python_code: str,
    *,
    valid_at: tuple[str, ...] = (),
) -> str:
    return _registered_interval_guard_status(
        sql_code,
        python_code,
        table_names=("lc_indexcomponent",),
        table_pattern=_INDEX_SNAPSHOT_TABLE_RE,
        valid_at=valid_at,
    )


def snapshot_guard_status(
    sql_code: str,
    python_code: str,
    *,
    valid_at: tuple[str, ...] = (),
) -> str:
    """Compatibility aggregate; consumers should use the split guard fields."""
    riskalert_status = cs_riskalert_effective_guard_status(sql_code, python_code, valid_at=valid_at)
    index_status = _index_snapshot_guard_status(sql_code, python_code, valid_at=valid_at)
    return _compat_snapshot_guard_status(riskalert_status, index_status)


def _compat_snapshot_guard_status(riskalert_status: str, index_status: str) -> str:
    applicable = [status for status in (riskalert_status, index_status) if status != "not_applicable"]
    if not applicable:
        return "not_applicable"
    return _required_guard_status(applicable)


def future_label_allowed(question: str, sql_code: str = "", python_code: str = "") -> bool:
    return bool(_FUTURE_LABEL_RE.search(f"{question or ''}\n{sql_code or ''}\n{python_code or ''}"))


def _has_current_state_status_filter(sql_code: str, python_code: str) -> bool:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    return any(pattern.search(combined) for pattern in _CURRENT_STATE_STATUS_FILTER_PATTERNS)


def _sql_alias_current_state_filter_present(
    sql: str,
    *,
    table_name: str,
    alias: str,
    field: str,
    listed_state: bool = False,
) -> bool:
    if alias:
        qualified = rf"`?{re.escape(alias)}`?\s*\.\s*`?{re.escape(field)}`?"
        unqualified = rf"(?<![\w.`])`?{re.escape(field)}`?\b"
        field_expr = rf"(?:{qualified}|{unqualified})"
    else:
        field_expr = (
            rf"(?:(?:`?{re.escape(table_name)}`?\s*\.\s*)?`?{re.escape(field)}`?)"
        )
    if listed_state:
        return bool(re.search(rf"{field_expr}\s*(?:=|IN\s*\()", sql, re.IGNORECASE))
    return bool(
        re.search(
            rf"{field_expr}\s*(?:=|==)\s*['\"]?1(?:\.0)?\b",
            sql,
            re.IGNORECASE,
        )
    )


def _python_dataframe_current_state_filter_present(
    python_code: str,
    *,
    dataframe_name: str,
    field: str,
    listed_state: bool = False,
) -> bool:
    if not dataframe_name:
        return False
    field_ref = rf"\b{re.escape(dataframe_name)}\s*\[\s*['\"]{re.escape(field)}['\"]\s*\]"
    if listed_state:
        pattern = re.compile(rf"{field_ref}\s*(?:==|\.\s*isin\s*\()", re.IGNORECASE)
    else:
        pattern = re.compile(
            rf"{field_ref}\s*(?:==|\.\s*eq\s*\()\s*['\"]?1(?:\.0)?\b",
            re.IGNORECASE,
        )
    return bool(pattern.search(python_code))


def _has_listed_state_current_filter(sql_code: str, python_code: str) -> bool:
    for fragment, dataframe_name in _audit_sql_fragments(
        sql_code,
        python_code,
        table_pattern=_LISTED_STATE_TABLE_RE,
    ):
        for table_name in _LISTED_STATE_TABLES:
            occurrences = _table_alias_occurrences(fragment, table_name)
            for alias, position in occurrences:
                local_fragment = _sql_select_branch_from(fragment, position)
                if _sql_alias_current_state_filter_present(
                    local_fragment,
                    table_name=table_name,
                    alias=alias,
                    field="LISTEDSTATE",
                    listed_state=True,
                ) or _python_dataframe_current_state_filter_present(
                    python_code,
                    dataframe_name=dataframe_name,
                    field="LISTEDSTATE",
                    listed_state=True,
                ):
                    return True
    return False


def _historical_current_state_flags(
    question: str,
    sql_code: str,
    python_code: str,
) -> set[str]:
    if not _has_historical_anchor(question):
        return set()
    flags: set[str] = set()
    for fragment, dataframe_name in _audit_sql_fragments(
        sql_code,
        python_code,
        table_pattern=_CURRENT_STATE_TABLE_RE,
    ):
        for table_name, rules in _CURRENT_STATE_FIELD_RULES.items():
            occurrences = _table_alias_occurrences(fragment, table_name)
            for alias, position in occurrences:
                local_fragment = _sql_select_branch_from(fragment, position)
                for field, flag in rules:
                    if _sql_alias_current_state_filter_present(
                        local_fragment,
                        table_name=table_name,
                        alias=alias,
                        field=field,
                    ) or _python_dataframe_current_state_filter_present(
                        python_code,
                        dataframe_name=dataframe_name,
                        field=field,
                    ):
                        flags.add(flag)
    return flags


def classification_vintage_guard_status(
    sql_code: str,
    python_code: str,
    *,
    valid_at: tuple[str, ...] = (),
    knowledge_cutoffs: tuple[str, ...] = (),
) -> str:
    matched = False
    statuses: list[str] = []
    for fragment, dataframe_name in _audit_sql_fragments(
        sql_code,
        python_code,
        table_pattern=_CLASSIFICATION_VINTAGE_TABLE_RE,
    ):
        for table_name in _CLASSIFICATION_VINTAGE_TABLES:
            if not re.search(rf"\b{re.escape(table_name)}\b", fragment, re.IGNORECASE):
                continue
            start_field, end_field = _TEMPORAL_INTERVAL_FIELDS[table_name]
            matched = True
            occurrences = _table_alias_occurrences(fragment, table_name)
            if not occurrences:
                statuses.append("missing")
                continue
            for alias, position in occurrences:
                local_fragment = _sql_select_branch_from(fragment, position)
                primary_status = _best_guard_status(
                    [
                        _sql_alias_interval_status(
                            local_fragment,
                            table_name=table_name,
                            alias=alias,
                            start_field=start_field,
                            end_field=end_field,
                            time_anchors=valid_at,
                        ),
                        _python_interval_status(
                            python_code,
                            dataframe_name=dataframe_name,
                            sql_fragment=local_fragment,
                            start_field=start_field,
                            end_field=end_field,
                            time_anchors=valid_at,
                        ),
                    ]
                )
                if primary_status == "missing":
                    statuses.append("missing")
                    continue

                information_field = _CLASSIFICATION_INFORMATION_FIELDS.get(table_name)
                if not information_field:
                    statuses.append(primary_status)
                    continue

                information_anchors = knowledge_cutoffs or valid_at
                anchors_to_check = information_anchors or ("",)
                information_status = _best_guard_status(
                    [
                        _best_guard_status(
                            [
                                _sql_field_guard_status(
                                    local_fragment,
                                    alias=alias,
                                    field=information_field,
                                    allow_unqualified=not alias,
                                    time_anchor=anchor,
                                ),
                                _python_dataframe_guard_status(
                                    python_code,
                                    dataframe_name=dataframe_name,
                                    fields=(information_field,),
                                    time_anchor=anchor,
                                ),
                            ]
                        )
                        for anchor in anchors_to_check
                    ]
                )
                if (
                    table_name in _CLASSIFICATION_DEGRADED_INFORMATION_TABLES
                    and information_status == _GUARD_STATUS_PRESENT
                ):
                    information_status = _GUARD_STATUS_DEGRADED
                statuses.append(_required_guard_status([primary_status, information_status]))
    if not matched:
        return "not_applicable"
    return _required_guard_status(statuses)


def _has_historical_anchor(question: str) -> bool:
    anchor, source = extract_time_anchor(question or "")
    return bool(anchor) or source != "missing" or bool(_HISTORICAL_ANCHOR_TOKEN_RE.search(question or ""))


def _bond_concept_vintage_missing(question: str, sql_code: str, python_code: str) -> bool:
    if not _has_historical_anchor(question):
        return False
    return _registered_interval_guard_status(
        sql_code,
        python_code,
        table_names=("bond_conceptnature",),
        table_pattern=_BOND_CONCEPT_TABLE_RE,
    ) == "missing"


def _future_label_as_feature_suspected(question: str, sql_code: str, python_code: str) -> bool:
    combined = f"{sql_code or ''}\n{python_code or ''}"
    if not future_label_allowed(question, sql_code, python_code):
        return False
    return any(pattern.search(combined) for pattern in _FUTURE_LABEL_FEATURE_PATTERNS)


def pit_diagnostic_flags(*, question: str = "", sql_code: str = "", python_code: str = "") -> list[str]:
    """Return high-precision task-level PIT flags; these do not change task scores."""
    legacy_mode = os.getenv("TEXT2SQL_TIME_ANCHOR_MODE", "").lower() == "legacy"
    anchor_context = extract_time_anchors(question or "") if not legacy_mode else None
    valid_at = anchor_context.valid_at if anchor_context else ()
    knowledge_cutoffs = anchor_context.knowledge_cutoffs if anchor_context else ()
    knowledge_cutoff = (
        max(knowledge_cutoffs)
        if knowledge_cutoffs
        else ""
    )
    flags = _historical_current_state_flags(question, sql_code, python_code)
    if lint_runtime_date_functions(sql_code=sql_code, python_code=python_code):
        flags.add("runtime_date_leakage")
    if financial_publish_guard_status(sql_code, python_code, question=question) == "missing":
        flags.add("financial_publish_guard_missing")
    if cs_riskalert_effective_guard_status(sql_code, python_code, valid_at=valid_at) == "missing":
        flags.add("cs_riskalert_effective_guard_missing")
    if cs_riskalert_information_guard_status(
        question,
        sql_code,
        python_code,
        time_anchor=knowledge_cutoff,
    ) == "missing":
        flags.add("cs_riskalert_information_guard_missing")
    if _index_snapshot_guard_status(sql_code, python_code, valid_at=valid_at) == "missing":
        flags.add("index_component_guard_missing")
    if _has_current_state_status_filter(sql_code, python_code):
        flags.add("current_state_status_filter")
    if _has_historical_anchor(question) and _has_listed_state_current_filter(sql_code, python_code):
        flags.add("listed_state_current_filter")
    if classification_vintage_guard_status(
        sql_code,
        python_code,
        valid_at=valid_at,
        knowledge_cutoffs=knowledge_cutoffs,
    ) == "missing":
        flags.add("classification_vintage_missing")
    if _bond_concept_vintage_missing(question, sql_code, python_code):
        flags.add("bond_concept_vintage_missing")
    return [flag for flag in _PIT_DIAGNOSTIC_FLAG_ORDER if flag in flags]


def pit_review_candidates(*, question: str = "", sql_code: str = "", python_code: str = "") -> list[str]:
    candidates: set[str] = set()
    if _future_label_as_feature_suspected(question, sql_code, python_code):
        candidates.add("future_label_as_feature_suspected")
    return [candidate for candidate in _PIT_REVIEW_CANDIDATE_ORDER if candidate in candidates]


def audit_time_anchor_record(
    *,
    qid: str,
    question: str,
    inputs: Any = None,
    sql_code: str = "",
    python_code: str = "",
    snapshot_default: str = "",
) -> dict[str, Any]:
    anchor_context = extract_time_anchors(question, inputs, snapshot_default=snapshot_default)
    if os.getenv("TEXT2SQL_TIME_ANCHOR_MODE", "").lower() == "legacy":
        time_anchor, anchor_source = _legacy_extract_time_anchor(
            question,
            inputs,
            snapshot_default=snapshot_default,
        )
        valid_at: tuple[str, ...] = ()
        knowledge_cutoff = time_anchor
        knowledge_cutoffs = (time_anchor,) if time_anchor else ()
    else:
        time_anchor, anchor_source = anchor_context.primary, anchor_context.primary_source
        valid_at = anchor_context.valid_at
        knowledge_cutoffs = anchor_context.knowledge_cutoffs
        knowledge_cutoff = max(knowledge_cutoffs) if knowledge_cutoffs else ""
    runtime_violations = lint_runtime_date_functions(sql_code=sql_code, python_code=python_code)
    financial_status = financial_publish_guard_status(
        sql_code,
        python_code,
        question=question,
        time_anchor=knowledge_cutoff,
    )
    riskalert_effective_status = cs_riskalert_effective_guard_status(
        sql_code,
        python_code,
        valid_at=valid_at,
    )
    riskalert_information_status = cs_riskalert_information_guard_status(
        question,
        sql_code,
        python_code,
        time_anchor=knowledge_cutoff,
    )
    index_component_status = _index_snapshot_guard_status(
        sql_code,
        python_code,
        valid_at=valid_at,
    )
    classification_status = classification_vintage_guard_status(
        sql_code,
        python_code,
        valid_at=valid_at,
        knowledge_cutoffs=knowledge_cutoffs,
    )
    return {
        "id": qid,
        "time_anchor": time_anchor,
        "anchor_source": anchor_source,
        "time_anchors": anchor_context.to_dict(),
        "runtime_date_function": "ok" if not runtime_violations else "violation",
        "runtime_date_violations": [item.to_dict() for item in runtime_violations],
        "financial_publish_guard": financial_status,
        "cs_riskalert_effective_guard": riskalert_effective_status,
        "cs_riskalert_information_guard": riskalert_information_status,
        "index_component_guard": index_component_status,
        "classification_vintage_guard": classification_status,
        # Backward-compatible aggregate; do not treat as an independent rule.
        "snapshot_guard": _compat_snapshot_guard_status(
            riskalert_effective_status,
            index_component_status,
        ),
        "future_label_allowed": future_label_allowed(question, sql_code, python_code),
        "pit_review_candidates": pit_review_candidates(
            question=question,
            sql_code=sql_code,
            python_code=python_code,
        ),
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
