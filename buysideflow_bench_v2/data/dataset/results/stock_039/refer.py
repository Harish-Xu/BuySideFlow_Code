#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from string import Template

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DT = "2025-04-28 23:59:59"
CHUNK_SIZE = 500
EXPECTED_COLUMNS = [
    "股票代码",
    "公司简称",
    "总资产同比增速",
    "营业收入同比增速",
    "资产负债率变化",
]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def load_sql_blocks() -> dict[str, str]:
    sql_text = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }
    if set(blocks) != {"index_components", "financial_records"}:
        raise ValueError(f"Unexpected SQL blocks: {sorted(blocks)}")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def query(name: str, timeout: int = 300, **params: str) -> pd.DataFrame:
    sql = Template(SQL_BLOCKS[name]).substitute(**params)
    result = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if result is None:
        raise RuntimeError(f"SQL block returned None: {name}")
    return result


def int_list(values: pd.Series | list[int]) -> str:
    unique_values = sorted(pd.unique(pd.Series(values).dropna()).astype(int))
    if not unique_values:
        raise ValueError("No values for SQL IN clause")
    return ", ".join(str(value) for value in unique_values)


def fetch_index_components() -> pd.DataFrame:
    return query("index_components", as_of_dt=AS_OF_DT)


def fetch_financial_records(companycodes: pd.Series) -> pd.DataFrame:
    companies = sorted(pd.unique(companycodes.dropna()).astype(int))
    frames: list[pd.DataFrame] = []
    for start in range(0, len(companies), CHUNK_SIZE):
        chunk = companies[start : start + CHUNK_SIZE]
        frame = query(
            "financial_records",
            company_values=int_list(chunk),
            as_of_dt=AS_OF_DT,
            timeout=180,
        )
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def dedupe_financial_records(records: pd.DataFrame, components: pd.DataFrame) -> pd.DataFrame:
    sector = components[["COMPANYCODE", "LISTEDSECTOR"]].drop_duplicates("COMPANYCODE").copy()
    sector["COMPANYCODE"] = pd.to_numeric(sector["COMPANYCODE"], errors="coerce").astype("Int64")

    data = records.copy()
    data["COMPANYCODE"] = pd.to_numeric(data["COMPANYCODE"], errors="coerce").astype("Int64")
    data = data.merge(sector, on="COMPANYCODE", how="inner")
    data = data[
        ((data["LISTEDSECTOR"] == 7) & (data["source_table"] == "stib"))
        | ((data["LISTEDSECTOR"] != 7) & (data["source_table"] == "main"))
    ].copy()

    data["end_date"] = pd.to_datetime(data["end_date"])
    for column in ["total_asset_growth", "revenue_growth", "debt_ratio", "JSID", "ID"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["INFOPUBLDATE"] = pd.to_datetime(data["INFOPUBLDATE"])
    data["INSERTTIME"] = pd.to_datetime(data["INSERTTIME"])
    data["IFADJUSTED"] = pd.to_numeric(data["IFADJUSTED"], errors="coerce")
    data["adjust_priority"] = 0
    stib = data["source_table"].eq("stib")
    data.loc[stib, "adjust_priority"] = data.loc[stib, "IFADJUSTED"].ne(1).astype(int)

    return (
        data.sort_values(
            [
                "COMPANYCODE",
                "end_date",
                "adjust_priority",
                "INFOPUBLDATE",
                "INSERTTIME",
                "JSID",
                "ID",
            ],
            ascending=[True, True, True, False, False, False, False],
        )
        .drop_duplicates(["COMPANYCODE", "end_date"], keep="first")
        [["COMPANYCODE", "end_date", "total_asset_growth", "revenue_growth", "debt_ratio"]]
        .copy()
    )


def build_company_metrics(records: pd.DataFrame, components: pd.DataFrame) -> pd.DataFrame:
    annual = dedupe_financial_records(records, components)
    current = annual[annual["end_date"] == pd.Timestamp("2024-12-31")].rename(
        columns={"debt_ratio": "debt_ratio_2024"}
    )
    prior = annual[annual["end_date"] == pd.Timestamp("2023-12-31")].rename(
        columns={"debt_ratio": "debt_ratio_2023"}
    )
    metrics = current[
        ["COMPANYCODE", "total_asset_growth", "revenue_growth", "debt_ratio_2024"]
    ].merge(prior[["COMPANYCODE", "debt_ratio_2023"]], on="COMPANYCODE", how="inner")
    metrics["debt_ratio_change"] = metrics["debt_ratio_2024"] - metrics["debt_ratio_2023"]
    return metrics.dropna(subset=["total_asset_growth", "revenue_growth", "debt_ratio_change"])


def build_result(components: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    base = components[["COMPANYCODE", "stock_code", "stock_name"]].drop_duplicates("COMPANYCODE")
    data = base.merge(metrics, on="COMPANYCODE", how="inner")
    data = data[
        (data["total_asset_growth"] > 30)
        & (data["revenue_growth"] < 20)
        & (data["debt_ratio_change"] > 5)
    ].copy()
    data["stock_code"] = data["stock_code"].astype(str).str.zfill(6)
    data = data.sort_values(
        ["total_asset_growth", "stock_code"], ascending=[False, True], kind="mergesort"
    )
    result = data.rename(
        columns={
            "stock_code": "股票代码",
            "stock_name": "公司简称",
            "total_asset_growth": "总资产同比增速",
            "revenue_growth": "营业收入同比增速",
            "debt_ratio_change": "资产负债率变化",
        }
    )[EXPECTED_COLUMNS].reset_index(drop=True)
    for column in EXPECTED_COLUMNS[2:]:
        result[column] = result[column].round(2)
    return result


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")
    components = fetch_index_components()
    constituent_count = components["COMPANYCODE"].nunique()
    if constituent_count != 1000:
        raise ValueError(f"Expected 1000 CSI 1000 constituents, got {constituent_count}")
    records = fetch_financial_records(components["COMPANYCODE"])
    metrics = build_company_metrics(records, components)
    result = build_result(components, metrics)
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
