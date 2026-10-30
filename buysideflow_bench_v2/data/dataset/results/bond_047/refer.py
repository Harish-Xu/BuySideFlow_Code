#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


AS_OF_DATE = pd.Timestamp("2024-12-31")
AS_OF_NEXT_DAY = pd.Timestamp("2025-01-01")
WINDOW_START = pd.Timestamp("2025-01-01")
WINDOW_END_EXCL = pd.Timestamp("2025-07-01")
MIN_OUTSTANDING_MILLION = 3000.0
MIN_PRESSURE_RATIO = 0.30
RESULT_COLUMNS = ["发行人", "所属区域", "存续债只数", "存续债余额", "到期/回售规模", "压力占比"]
TERM_COLUMNS = ["main_code", "option_type", "option_maturity", "value_date", "issue_date"]
OPTION_TERM_BATCH_SIZE = 1000


def load_base_sql() -> str:
    return Path(__file__).resolve().with_name("refer.sql").read_text(encoding="utf-8")


def parse_first_put_year(option_type: object, option_maturity: object) -> float | None:
    option_text = "" if pd.isna(option_type) else str(option_type)
    maturity_text = "" if pd.isna(option_maturity) else str(option_maturity)
    if "持有人回售" not in option_text and "投资者回售" not in option_text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*\+", maturity_text)
    return float(match.group(1)) if match else None


def add_years(start_date: pd.Timestamp | pd.NaT, years: float | None) -> pd.Timestamp | pd.NaT:
    if pd.isna(start_date) or years is None or pd.isna(years):
        return pd.NaT
    return start_date + pd.DateOffset(months=int(round(years * 12)))


def fmt_amount(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.2f}"


def fmt_ratio(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value) * 100:.2f}"


def load_live_city_bonds() -> pd.DataFrame:
    data = query_to_dataframe(load_base_sql(), max_rows=0, timeout=300)
    if data.empty:
        return data
    result = data.copy()
    result["main_code"] = pd.to_numeric(result["main_code"], errors="coerce").astype("Int64")
    result["company_code"] = pd.to_numeric(result["company_code"], errors="coerce").astype("Int64")
    result["maturity_date"] = pd.to_datetime(result["maturity_date"], errors="coerce")
    result["current_size_million"] = pd.to_numeric(result["current_size_million"], errors="coerce")
    return result.dropna(subset=["main_code", "company_code", "issuer_area", "current_size_million"])


def load_option_terms(main_codes: pd.Series) -> pd.DataFrame:
    code_list = sorted({int(code) for code in main_codes.dropna().astype(int).tolist()})
    if not code_list:
        return pd.DataFrame(columns=TERM_COLUMNS)

    frames: list[pd.DataFrame] = []
    for start in range(0, len(code_list), OPTION_TERM_BATCH_SIZE):
        batch = code_list[start : start + OPTION_TERM_BATCH_SIZE]
        code_sql = ",".join(str(code) for code in batch)
        sql = f"""
        SELECT
            bi.MAINCODE AS main_code,
            bi.OPTIONTYPE AS option_type,
            bi.OPMATURITY AS option_maturity,
            bi.VALUEDATE AS value_date,
            bi.ISSUEDATE AS issue_date
        FROM bond_basicinfo bi
        WHERE bi.MAINCODE IN ({code_sql})
          AND (bi.OPTIONTYPE LIKE '%持有人回售%' OR bi.OPTIONTYPE LIKE '%投资者回售%')
          AND bi.OPMATURITY LIKE '%+%'
          AND (bi.INITIALINFOPUBLDATE IS NULL OR bi.INITIALINFOPUBLDATE < '2025-01-01')
          AND (bi.ISSUEDATE IS NULL OR bi.ISSUEDATE < '2025-01-01')
          AND (bi.VALUEDATE IS NULL OR bi.VALUEDATE < '2025-01-01')
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=120)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=TERM_COLUMNS)

    result = pd.concat(frames, ignore_index=True)
    if result.empty:
        return pd.DataFrame(columns=TERM_COLUMNS)
    result["main_code"] = pd.to_numeric(result["main_code"], errors="coerce").astype("Int64")
    result["value_date"] = pd.to_datetime(result["value_date"], errors="coerce")
    result["issue_date"] = pd.to_datetime(result["issue_date"], errors="coerce")
    result["put_year"] = result.apply(
        lambda row: parse_first_put_year(row.get("option_type"), row.get("option_maturity")), axis=1
    )
    result["put_date"] = result.apply(lambda row: add_years(row["value_date"], row["put_year"]), axis=1)
    result = result.dropna(subset=["main_code"]).sort_values(["main_code", "put_date"], na_position="last")
    return result.drop_duplicates("main_code", keep="first")[TERM_COLUMNS]


def attach_put_dates(bonds: pd.DataFrame, terms: pd.DataFrame) -> pd.DataFrame:
    data = bonds.merge(terms, on="main_code", how="left")
    if data.empty:
        data["put_date"] = pd.NaT
        return data
    data["put_year"] = data.apply(lambda row: parse_first_put_year(row.get("option_type"), row.get("option_maturity")), axis=1)
    data["put_date"] = data.apply(lambda row: add_years(row["value_date"], row["put_year"]), axis=1)
    return data


def build_result() -> tuple[pd.DataFrame, pd.DataFrame]:
    with shared_connection_session():
        bonds = load_live_city_bonds()
        terms = load_option_terms(bonds["main_code"] if not bonds.empty else pd.Series(dtype="Int64"))
    if bonds.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS), bonds

    data = attach_put_dates(bonds, terms)
    data["has_maturity_pressure"] = (data["maturity_date"] >= WINDOW_START) & (data["maturity_date"] < WINDOW_END_EXCL)
    data["has_put_pressure"] = (data["put_date"] >= WINDOW_START) & (data["put_date"] < WINDOW_END_EXCL)
    data["maturity_pressure_date"] = data["maturity_date"].where(data["has_maturity_pressure"])
    data["put_pressure_date"] = data["put_date"].where(data["has_put_pressure"])
    data["pressure_date"] = data[["maturity_pressure_date", "put_pressure_date"]].min(axis=1)
    data["has_pressure"] = data["pressure_date"].notna()
    data["pressure_size_million"] = data["current_size_million"].where(data["has_pressure"], 0.0)

    summary = (
        data.groupby("company_code", as_index=False)
        .agg(
            issuer=("issuer", "min"),
            issuer_area=("issuer_area", "min"),
            live_bond_count=("main_code", "nunique"),
            outstanding_size_million=("current_size_million", "sum"),
            pressure_size_million=("pressure_size_million", "sum"),
        )
        .assign(pressure_ratio=lambda x: x["pressure_size_million"] / x["outstanding_size_million"])
    )
    selected = (
        summary.loc[
            (summary["outstanding_size_million"] >= MIN_OUTSTANDING_MILLION)
            & (summary["pressure_ratio"] > MIN_PRESSURE_RATIO)
        ]
        .sort_values(["pressure_ratio", "issuer"], ascending=[False, True])
        .head(20)
    )
    result = pd.DataFrame(
        {
            "发行人": selected["issuer"].astype(str),
            "所属区域": selected["issuer_area"].astype(str),
            "存续债只数": selected["live_bond_count"].astype("Int64"),
            "存续债余额": (selected["outstanding_size_million"] / 100.0).map(fmt_amount),
            "到期/回售规模": (selected["pressure_size_million"] / 100.0).map(fmt_amount),
            "压力占比": selected["pressure_ratio"].map(fmt_ratio),
        }
    )
    return result[RESULT_COLUMNS], data


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    result, _ = build_result()
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
