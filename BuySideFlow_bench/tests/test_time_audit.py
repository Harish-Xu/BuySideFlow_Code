from __future__ import annotations

from pathlib import Path

from run_time_audit import _build_report
from sweagent.text2sql.time_audit import audit_time_anchor_record, extract_time_anchor, pit_diagnostic_flags


def test_extract_time_anchor_recognizes_quarter_ranges_without_matching_months() -> None:
    assert extract_time_anchor("查询 2020Q1 至 2025Q4 中国实际 GDP 季度同比增速") == (
        "2025-12-31",
        "instruction_derived",
    )
    assert extract_time_anchor("统计2025年第四季度数据") == ("2025-12-31", "instruction_derived")
    assert extract_time_anchor("统计2025年1月到6月数据") == ("2025", "instruction_year")


def test_cs_riskalert_implement_remove_dates_count_as_snapshot_guard() -> None:
    sql = """
    SELECT 1
    FROM cs_riskalert ra
    WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
      AND DATE(ra.IMPLEMENTDATE) <= DATE('2025-12-31')
      AND (ra.REMOVEDATE IS NULL OR DATE(ra.REMOVEDATE) > DATE('2025-12-31'))
    """

    record = audit_time_anchor_record(
        qid="demo",
        question="截至2025-12-31剔除仍处于ST风险警示状态的股票",
        sql_code=sql,
    )

    assert record["snapshot_guard"] == "present"
    assert record["cs_riskalert_publish_guard"] == "missing"


def test_cs_riskalert_publish_guard_present_with_announcement_or_update_fields() -> None:
    sql = """
    SELECT 1
    FROM cs_riskalert ra
    WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
      AND ra.IMPLEMENTDATE <= p.as_of_date
      AND (ra.REMOVEDATE IS NULL OR ra.REMOVEDATE > p.as_of_date)
      AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_date
      AND (ra.REMOVEINFOPUBLDATE IS NULL OR ra.REMOVEINFOPUBLDATE > p.as_of_date OR ra.UPDATETIME <= p.as_of_date)
    """

    record = audit_time_anchor_record(
        qid="demo",
        question="以 as_of_date 为观察日剔除ST风险警示股票",
        inputs={"as_of_date": "2025-12-31"},
        sql_code=sql,
    )

    assert record["snapshot_guard"] == "present"
    assert record["cs_riskalert_publish_guard"] == "present"


def test_report_includes_cs_riskalert_publish_guard_summary_and_column() -> None:
    records = [
        {
            "id": "stock_x",
            "time_anchor": "2025-12-31",
            "anchor_source": "instruction",
            "runtime_date_function": "ok",
            "runtime_date_violations": [],
            "financial_publish_guard": "not_applicable",
            "cs_riskalert_publish_guard": "missing",
            "snapshot_guard": "present",
            "future_label_allowed": False,
        }
    ]

    report = _build_report(records, data_path=Path("data/dataset"), snapshot_default="")

    assert "| cs_riskalert_publish_guard_missing | 1 |" in report
    assert "cs_riskalert_publish_guard" in report


def test_pit_diagnostic_flags_detect_core_generated_leakage_patterns() -> None:
    sql = """
    SELECT sm.SECUABBR, f.ROE, CURDATE() AS run_date
    FROM secumain sm
    JOIN lc_mainindexnew f ON f.COMPANYCODE = sm.COMPANYCODE
    JOIN mf_jyfundtype t ON t.INNERCODE = sm.INNERCODE
    WHERE sm.SECUABBR NOT LIKE '%ST%'
      AND f.ENDDATE = (SELECT MAX(ENDDATE) FROM lc_mainindexnew)
    """

    flags = pit_diagnostic_flags(
        question="以2025-12-31为观察日，剔除ST并使用已披露财务指标筛选股票",
        sql_code=sql,
    )

    assert "runtime_date_leakage" in flags
    assert "financial_publish_guard_missing" in flags
    assert "current_state_status_filter" in flags
    assert "classification_vintage_missing" in flags


def test_pit_diagnostic_flags_detect_future_label_filter_as_suspected_only() -> None:
    sql = """
    SELECT stock_code, future_20d_excess_return
    FROM factor_panel
    WHERE future_20d_excess_return > 0
    """

    flags = pit_diagnostic_flags(
        question="按观察日构建因子，并输出未来20个交易日超额收益作为离线评估标签",
        sql_code=sql,
    )

    assert flags == ["future_label_as_feature_suspected"]


def test_pit_diagnostic_flags_do_not_flag_valid_cs_riskalert_window() -> None:
    sql = """
    SELECT sm.SECUABBR
    FROM secumain sm
    WHERE NOT EXISTS (
      SELECT 1 FROM cs_riskalert ra
      WHERE ra.INNERCODE = sm.INNERCODE
        AND ra.IMPLEMENTDATE <= DATE('2025-12-31')
        AND (ra.REMOVEDATE IS NULL OR ra.REMOVEDATE > DATE('2025-12-31'))
    )
    """

    flags = pit_diagnostic_flags(
        question="截至2025-12-31剔除ST股票",
        sql_code=sql,
    )

    assert "current_state_status_filter" not in flags
