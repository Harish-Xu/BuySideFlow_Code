#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from string import Template

import pandas as pd

INJECTED_SQL = globals().get("sql_code", "")
if not INJECTED_SQL:
    REPO_ROOT = Path(__file__).resolve().parents[4]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-07-25"
EXPECTED_COLUMNS = [
    "股票代码",
    "股票简称",
    "总市值",
    "EV/EBITDA",
    "历史分位",
    "自由现金流收益率",
]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def load_sql_blocks() -> dict[str, str]:
    text = INJECTED_SQL or Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    return {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(text)
    }


def run_block(blocks: dict[str, str], name: str, **params: str) -> pd.DataFrame:
    sql = Template(blocks[name]).substitute(**params)
    result = query_to_dataframe(sql, max_rows=0, timeout=300)
    if result is None:
        raise ValueError(f"{name} returned None")
    return result


def int_list(values: pd.Series) -> str:
    items = sorted(pd.to_numeric(values, errors="coerce").dropna().astype(int).unique())
    if not items:
        raise ValueError("Empty SQL identifier list")
    return ", ".join(map(str, items))


def query_in_chunks(
    blocks: dict[str, str], name: str, values: pd.Series, parameter: str
) -> pd.DataFrame:
    items = sorted(pd.to_numeric(values, errors="coerce").dropna().astype(int).unique())
    frames: list[pd.DataFrame] = []
    for start in range(0, len(items), 1000):
        params = {
            "as_of_date": AS_OF_DATE,
            parameter: ", ".join(map(str, items[start : start + 1000])),
        }
        part = run_block(blocks, name, **params)
        if not part.empty:
            frames.append(part)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_percentiles(blocks: dict[str, str], current: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    records = current[["INNERCODE", "EVTOEBITDA"]].dropna().drop_duplicates("INNERCODE")
    for start in range(0, len(records), 100):
        chunk = records.iloc[start : start + 100]
        selects = [
            f"SELECT {int(row.INNERCODE)} AS INNERCODE, {float(row.EVTOEBITDA):.12g} AS current_ev"
            for row in chunk.itertuples(index=False)
        ]
        part = run_block(
            blocks,
            "valuation_percentiles",
            as_of_date=AS_OF_DATE,
            candidate_values="\nUNION ALL\n".join(selects),
        )
        if not part.empty:
            frames.append(part)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def calculate() -> pd.DataFrame:
    blocks = load_sql_blocks()
    universe = run_block(blocks, "stock_universe", as_of_date=AS_OF_DATE)
    valuation = query_in_chunks(
        blocks, "valuation_today", universe["INNERCODE"], "innercode_values"
    )
    current = universe.merge(valuation, on="INNERCODE", how="inner")
    if current.empty:
        result = pd.DataFrame(columns=EXPECTED_COLUMNS)
    else:
        for column in ["INNERCODE", "COMPANYCODE", "TOTALMV", "EVTOEBITDA"]:
            current[column] = pd.to_numeric(current[column], errors="coerce")

        listed = run_block(
            blocks,
            "list_status_asof",
            as_of_date=AS_OF_DATE,
            innercode_values=int_list(current["INNERCODE"]),
        )
        industry = run_block(
            blocks,
            "industry_asof",
            as_of_date=AS_OF_DATE,
            company_values=int_list(current["COMPANYCODE"]),
        )
        listed["INNERCODE"] = pd.to_numeric(listed["INNERCODE"], errors="coerce")
        industry["COMPANYCODE"] = pd.to_numeric(industry["COMPANYCODE"], errors="coerce")
        current = current.merge(listed, on="INNERCODE", how="inner").merge(
            industry, on="COMPANYCODE", how="inner"
        )

        if current.empty:
            return pd.DataFrame(columns=EXPECTED_COLUMNS)
        percentiles = fetch_percentiles(blocks, current)
        percentiles["INNERCODE"] = pd.to_numeric(
            percentiles["INNERCODE"], errors="coerce"
        )
        percentiles["ev_percentile"] = pd.to_numeric(
            percentiles["ev_percentile"], errors="coerce"
        )
        candidates = current.merge(percentiles, on="INNERCODE", how="inner")
        candidates = candidates[candidates["ev_percentile"] <= 0.20].copy()

        if candidates.empty:
            result = pd.DataFrame(columns=EXPECTED_COLUMNS)
        else:
            fcf = run_block(
                blocks,
                "latest_fcf",
                as_of_date=AS_OF_DATE,
                company_values=int_list(candidates["COMPANYCODE"]),
            )
            fcf["COMPANYCODE"] = pd.to_numeric(fcf["COMPANYCODE"], errors="coerce")
            fcf["FREECASHFLOW"] = pd.to_numeric(fcf["FREECASHFLOW"], errors="coerce")
            selected = candidates.merge(fcf, on="COMPANYCODE", how="inner")
            selected = selected[selected["FREECASHFLOW"].notna()].copy()
            selected["fcf_yield"] = selected["FREECASHFLOW"] / selected["TOTALMV"]
            selected = selected[selected["fcf_yield"] > 0.07].copy()
            selected["stock_code"] = selected["stock_code"].astype(str).str.zfill(6)
            selected = selected.sort_values(["ev_percentile", "stock_code"])
            result = pd.DataFrame(
                {
                    "股票代码": selected["stock_code"],
                    "股票简称": selected["stock_name"],
                    "总市值": (selected["TOTALMV"] / 100_000_000).round(2),
                    "EV/EBITDA": selected["EVTOEBITDA"].round(2),
                    "历史分位": selected["ev_percentile"].round(4),
                    "自由现金流收益率": (selected["fcf_yield"] * 100).round(2),
                }
            )

    return result


def main(*, write_output: bool = True) -> pd.DataFrame:
    result = calculate()
    if write_output:
        output_path = Path(__file__).with_name("result.csv")
        result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    result = main(write_output=not bool(INJECTED_SQL))
