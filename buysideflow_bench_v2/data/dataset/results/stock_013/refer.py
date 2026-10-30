#!/usr/bin/env python3
"""Three-year positive earnings/cash-flow screen, ranked by two-year CAGR."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import re
from string import Template
import sys

import numpy as np
import pandas as pd

INJECTED_SQL = globals().get("sql_code", "")
if not INJECTED_SQL:
    REPO_ROOT = Path(__file__).resolve().parents[4]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

OBSERVATION_DATE = date(2025, 5, 6)
TARGET_YEARS = (2022, 2023, 2024)
TOP_N = 20
COLUMNS = ["股票代码", "股票简称", "2024年归母净利润", "2024年经营活动现金流量净额",
           "归母净利润复合年增长率"]
SQL_BLOCK_RE = re.compile(r"^-- name:\s*(\w+)\s*$\n(.*?)(?=^-- name:|\Z)", re.M | re.S)
MARK_PRIORITY = {1: 0, 8: 1, 7: 2, 6: 3, 2: 4}


def load_sql_blocks():
    text = INJECTED_SQL or Path(__file__).with_name("refer.sql").read_text()
    return dict(SQL_BLOCK_RE.findall(text))


def render_sql(name: str, **params: object) -> str:
    return Template(load_sql_blocks()[name]).substitute(
        obs_date=OBSERVATION_DATE.isoformat(),
        next_date=(OBSERVATION_DATE + timedelta(days=1)).isoformat(),
        annual_enddate_list_sql=", ".join(f"'{year}-12-31'" for year in TARGET_YEARS),
        **params,
    )


def run_sql(sql: str) -> pd.DataFrame:
    return query_to_dataframe(sql, timeout=180, max_rows=0)


def select_financial_records(records: pd.DataFrame) -> pd.DataFrame:
    records = records.copy()
    records["pub_date"] = pd.to_datetime(records["pub_date"], format="mixed")
    records = records.loc[
        records["pub_date"].lt(pd.Timestamp(OBSERVATION_DATE + timedelta(days=1)))
        & records["fiscal_year"].isin(TARGET_YEARS)
        & records["MARK"].isin(MARK_PRIORITY)
    ].copy()
    records["priority"] = records["MARK"].map(MARK_PRIORITY)
    return records.sort_values(
        ["COMPANYCODE", "fiscal_year", "priority", "pub_date", "JSID", "ID"],
        ascending=[True, True, True, False, False, False],
    ).drop_duplicates(["COMPANYCODE", "fiscal_year"])[
        ["COMPANYCODE", "fiscal_year", "parent_net_profit", "operating_cashflow"]
    ]


def build_result(stock_pool: pd.DataFrame, financials: pd.DataFrame) -> pd.DataFrame:
    if stock_pool.empty or financials.empty:
        return pd.DataFrame(columns=COLUMNS)
    panel = (stock_pool.merge(pd.DataFrame({"fiscal_year": TARGET_YEARS}), how="cross")
             .merge(financials, on=["COMPANYCODE", "fiscal_year"], how="left", validate="many_to_one"))
    rows = []
    for _, group in panel.groupby("INNERCODE"):
        group = group.set_index("fiscal_year").reindex(TARGET_YEARS)
        values = group[["parent_net_profit", "operating_cashflow"]].apply(pd.to_numeric)
        if values.isna().any().any() or not np.isfinite(values.to_numpy()).all():
            continue
        if not values.gt(0).all().all():
            continue
        profit_2022 = Decimal(str(values.loc[2022, "parent_net_profit"]))
        profit_2024 = Decimal(str(values.loc[2024, "parent_net_profit"]))
        if profit_2024 <= profit_2022 * Decimal("1.21"):
            continue
        growth = ((profit_2024 / profit_2022).sqrt() - 1) * 100
        first = group.iloc[0]
        rows.append([
            str(first["stock_code"]).zfill(6), first["stock_name"], profit_2024 / Decimal("1e8"),
            Decimal(str(values.loc[2024, "operating_cashflow"])) / Decimal("1e8"), growth,
        ])
    result = pd.DataFrame(rows, columns=COLUMNS)
    return result.sort_values([COLUMNS[4], COLUMNS[0]], ascending=[False, True]).head(TOP_N).reset_index(drop=True)


def run() -> pd.DataFrame:
    stock_pool = run_sql(render_sql("stock_pool"))
    if stock_pool.empty:
        raise ValueError("stock_013: historical stock universe is unavailable")
    companies = sorted(set(stock_pool.COMPANYCODE.astype(int)))
    frames = [run_sql(render_sql("financial_records", company_values=",".join(map(str, companies[i:i+500]))))
              for i in range(0, len(companies), 500)]
    raw = pd.concat(frames, ignore_index=True)
    if raw.empty:
        raise ValueError("stock_013: annual financial records are unavailable")
    result = build_result(stock_pool, select_financial_records(raw))
    for column in COLUMNS[2:]:
        result[column] = result[column].map(lambda value: float(f"{value:.2f}"))
    return result


def main(*, write_output=True) -> pd.DataFrame:
    result = run()
    if write_output:
        result.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    if not INJECTED_SQL:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env", override=False)
    result = main(write_output=not bool(INJECTED_SQL))
