#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2026-01-16"
MIN_TRADE_DAYS_20 = 5
TOP_PER_GROUP = 3
CHUNK_SIZE = 5000
RESULT_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "剩余期限",
    "估值收益率",
    "信用利差",
    "利差偏离",
    "近20日成交天数",
]
RATING_ORDER = {"AAA": 0}
TERM_BUCKETS = [(2.0, 3.0, "2-3年"), (3.0, 4.0, "3-4年"), (4.0, 5.0, "4-5年")]
TERM_ORDER = {label: idx for idx, (_, _, label) in enumerate(TERM_BUCKETS)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同评级同期限信用债利差偏离筛选")
    parser.add_argument("--as-of-date", default=AS_OF_DATE)
    return parser.parse_args()


def load_sql_blocks() -> dict[str, str]:
    sql_text = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    matches = list(re.finditer(r"^-- name:\s*(\w+)\s*$", sql_text, flags=re.MULTILINE))
    blocks: dict[str, str] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(sql_text)
        blocks[match.group(1)] = sql_text[start:end].strip().rstrip(";")
    return blocks


def render_sql(template: str, **params: str) -> str:
    sql = template
    for key, value in params.items():
        sql = sql.replace(f"{{{{{key}}}}}", value)
    return sql


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def sql_int_list(values: list[int]) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values: list[int], size: int = CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def fetch_current_valuation_date(blocks: dict[str, str], as_of_date: str) -> pd.Timestamp:
    sql = render_sql(blocks["current_valuation_date"], as_of_date=as_of_date)
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty or pd.isna(df.loc[0, "valuation_date"]):
        raise ValueError("No valuation date with recommended CCDC valuation and treasury curve was found.")
    return pd.Timestamp(df.loc[0, "valuation_date"]).normalize()


def fetch_candidates(blocks: dict[str, str], valuation_date: pd.Timestamp) -> pd.DataFrame:
    sql = render_sql(blocks["current_candidates"], valuation_date=valuation_date.strftime("%Y-%m-%d"))
    df = query_to_dataframe(sql, max_rows=0, timeout=900)
    if df.empty:
        return df

    df = df.copy()
    int_cols = ["maincode", "innercode"]
    num_cols = ["remaining_years", "valuation_yield_pct"]
    for col in int_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")

    df = df.dropna(
        subset=[
            "valuation_date",
            "maincode",
            "innercode",
            "bond_code",
            "bond_name",
            "issuer",
            "rating_desc",
            "remaining_years",
            "valuation_yield_pct",
        ]
    ).copy()
    if df.empty:
        return df

    df["maincode"] = df["maincode"].astype("int64")
    df["innercode"] = df["innercode"].astype("int64")
    for col in ["bond_code", "bond_name", "issuer", "rating_desc"]:
        df[col] = df[col].astype(str)
    return df.reset_index(drop=True)


def fetch_recent_trade_days(blocks: dict[str, str], valuation_date: pd.Timestamp) -> list[str]:
    sql = render_sql(blocks["recent_trade_days"], valuation_date=valuation_date.strftime("%Y-%m-%d"))
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        return []
    dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
    return sorted(dates.dt.strftime("%Y-%m-%d").tolist())


def fetch_trade_activity(
    blocks: dict[str, str],
    maincodes: list[int],
    trade_days: list[str],
    as_of_date: str = AS_OF_DATE,
) -> pd.DataFrame:
    if not maincodes or not trade_days:
        return pd.DataFrame(columns=["maincode", "trade_days_20"])

    trade_dates = ",".join(f"DATE('{day}')" for day in trade_days)
    frames: list[pd.DataFrame] = []
    for part in chunks(sorted(set(int(code) for code in maincodes))):
        sql = render_sql(
            blocks["trade_activity"],
            maincodes=sql_int_list(part),
            trade_dates=trade_dates,
            as_of_date=as_of_date,
        )
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return pd.DataFrame(columns=["maincode", "trade_days_20"])

    df["maincode"] = pd.to_numeric(df["maincode"], errors="coerce")
    df["trade_days_20"] = pd.to_numeric(df["trade_days_20"], errors="coerce")
    df = df.dropna(subset=["maincode", "trade_days_20"]).copy()
    df["maincode"] = df["maincode"].astype("int64")
    df["trade_days_20"] = df["trade_days_20"].astype("int64")
    return df.drop_duplicates("maincode", keep="first")


def fetch_treasury_curve(blocks: dict[str, str], valuation_date: pd.Timestamp) -> pd.DataFrame:
    sql = render_sql(blocks["treasury_curve"], valuation_date=valuation_date.strftime("%Y-%m-%d"))
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        raise ValueError("Treasury curve is empty on the valuation date.")

    df = df.copy()
    df["maturity_year"] = pd.to_numeric(df["maturity_year"], errors="coerce")
    df["treasury_yield_pct"] = pd.to_numeric(df["yield_value"], errors="coerce") * 100.0
    df = df.dropna(subset=["maturity_year", "treasury_yield_pct"]).copy()
    return df.sort_values("maturity_year").drop_duplicates("maturity_year", keep="last")


def assign_term_bucket(remaining_years: float) -> str | None:
    for left, right, label in TERM_BUCKETS:
        if left <= remaining_years < right or (right == 5.0 and remaining_years == right):
            return label
    return None


def add_spread_metrics(candidates: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates

    result = candidates.copy()
    tenors = curve["maturity_year"].to_numpy(dtype=float)
    yields = curve["treasury_yield_pct"].to_numpy(dtype=float)
    maturities = result["remaining_years"].to_numpy(dtype=float)

    result["treasury_benchmark_yield_pct"] = np.nan
    valid = (maturities >= tenors.min()) & (maturities <= tenors.max())
    result.loc[valid, "treasury_benchmark_yield_pct"] = np.interp(maturities[valid], tenors, yields)
    result = result.dropna(subset=["treasury_benchmark_yield_pct"]).copy()

    result["credit_spread_bp"] = (
        result["valuation_yield_pct"] - result["treasury_benchmark_yield_pct"]
    ) * 100.0
    result["term_bucket"] = result["remaining_years"].map(assign_term_bucket)
    result = result.dropna(subset=["term_bucket"]).copy()
    return result


def select_group_top(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates

    result = candidates.copy()
    result["group_median_spread_bp"] = result.groupby(
        ["rating_desc", "term_bucket"], sort=False
    )["credit_spread_bp"].transform("median")
    result["spread_deviation_bp"] = result["credit_spread_bp"] - result["group_median_spread_bp"]
    result["rating_rank"] = result["rating_desc"].map(RATING_ORDER)
    result["term_rank"] = result["term_bucket"].map(TERM_ORDER)

    result = result.dropna(subset=["rating_rank", "term_rank", "spread_deviation_bp"]).copy()
    result["rating_rank"] = result["rating_rank"].astype(int)
    result["term_rank"] = result["term_rank"].astype(int)

    ordered = result.sort_values(
        ["rating_rank", "term_rank", "spread_deviation_bp", "bond_code"],
        ascending=[True, True, False, True],
    )
    selected = (
        ordered.groupby(["rating_desc", "term_bucket"], group_keys=False, sort=False)
        .head(TOP_PER_GROUP)
        .copy()
    )
    return selected.sort_values(
        ["rating_rank", "term_rank", "spread_deviation_bp", "bond_code"],
        ascending=[True, True, False, True],
    ).reset_index(drop=True)


def format_result(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return empty_result()

    out = pd.DataFrame(
        {
            "债券代码": selected["bond_code"],
            "债券简称": selected["bond_name"],
            "发行人": selected["issuer"],
            "剩余期限": selected["remaining_years"],
            "估值收益率": selected["valuation_yield_pct"],
            "信用利差": selected["credit_spread_bp"],
            "利差偏离": selected["spread_deviation_bp"],
            "近20日成交天数": selected["trade_days_20"].astype(int),
        }
    )

    for col in [
        "剩余期限",
        "估值收益率",
        "信用利差",
        "利差偏离",
    ]:
        out[col] = out[col].map(lambda x: "" if pd.isna(x) else f"{float(x):.2f}")
    return out[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).strftime("%Y-%m-%d")
    blocks = load_sql_blocks()

    valuation_date = fetch_current_valuation_date(blocks, as_of_date)
    if valuation_date != pd.Timestamp(as_of_date):
        raise ValueError("The question requires valuation and curve data on the observation date.")
    candidates = fetch_candidates(blocks, valuation_date)
    if candidates.empty:
        result = empty_result()
    else:
        trade_days = fetch_recent_trade_days(blocks, valuation_date)
        if len(trade_days) != 20:
            raise ValueError("Expected 20 common interbank/exchange trading days")
        activity = fetch_trade_activity(blocks, candidates["maincode"].tolist(), trade_days, as_of_date)
        candidates = candidates.merge(activity, on="maincode", how="left")
        candidates["trade_days_20"] = (
            pd.to_numeric(candidates["trade_days_20"], errors="coerce").fillna(0).astype(int)
        )
        candidates = candidates[candidates["trade_days_20"] >= MIN_TRADE_DAYS_20].copy()

        curve = fetch_treasury_curve(blocks, valuation_date)
        candidates = add_spread_metrics(candidates, curve)
        selected = select_group_top(candidates)
        result = format_result(selected)

    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
