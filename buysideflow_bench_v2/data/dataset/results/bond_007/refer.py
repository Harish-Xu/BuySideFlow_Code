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


AS_OF_DATE = "2025-12-31"
TOP_K = 10
MIN_HISTORY_DAYS = 60
CROSS_SECTION_CUTOFF = 0.30
CHUNK_SIZE = 500
RESULT_COLUMNS = [
    "债券代码",
    "债券简称",
    "估值日",
    "剩余期限(年)",
    "个券估值收益率(%)",
    "国债插值收益率(%)",
    "当前信用利差(bp)",
    "近一年分位(%)",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AAA城投债信用利差低分位筛选")
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


def sql_int_list(values: list[int]) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values: list[int], size: int = CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def fetch_current_valuation_date(blocks: dict[str, str], as_of_date: str) -> pd.Timestamp:
    sql = render_sql(blocks["current_valuation_date"], as_of_date=as_of_date)
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty or pd.isna(df.loc[0, "valuation_date"]):
        raise ValueError("No usable valuation date found before the as-of date.")
    return pd.Timestamp(df.loc[0, "valuation_date"]).normalize()


def fetch_current_candidates(blocks: dict[str, str], valuation_date: pd.Timestamp) -> pd.DataFrame:
    sql = render_sql(
        blocks["current_candidates"],
        valuation_date=valuation_date.strftime("%Y-%m-%d"),
    )
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        return df

    df = df.copy()
    df["MAINCODE"] = pd.to_numeric(df["MAINCODE"], errors="coerce")
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce")
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df["remaining_years"] = pd.to_numeric(df["remaining_years"], errors="coerce")
    df["bond_ytm"] = pd.to_numeric(df["bond_ytm"], errors="coerce")
    df = df.dropna(
        subset=["MAINCODE", "INNERCODE", "valuation_date", "remaining_years", "bond_ytm"]
    ).copy()
    df["MAINCODE"] = df["MAINCODE"].astype(int)
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    return df


def fetch_history_valuations(
    blocks: dict[str, str],
    innercodes: list[int],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(sorted(set(int(x) for x in innercodes))):
        sql = render_sql(
            blocks["history_valuations"],
            innercodes=sql_int_list(part),
            start_date=start_date,
            end_date=end_date,
        )
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df

    df = df.copy()
    df["MAINCODE"] = pd.to_numeric(df["MAINCODE"], errors="coerce")
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce")
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df["bond_ytm"] = pd.to_numeric(df["bond_ytm"], errors="coerce")
    df["remaining_years"] = pd.to_numeric(df["remaining_years"], errors="coerce")
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    df = df.dropna(
        subset=["MAINCODE", "INNERCODE", "valuation_date", "bond_ytm", "remaining_years", "ID"]
    ).copy()
    df["MAINCODE"] = df["MAINCODE"].astype(int)
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    df = df.sort_values(
        ["MAINCODE", "valuation_date", "bond_code", "INNERCODE", "ID"],
        ascending=[True, True, True, True, False],
    )
    return df.drop_duplicates(["MAINCODE", "valuation_date"], keep="first").reset_index(drop=True)


def fetch_treasury_curve(blocks: dict[str, str], start_date: str, end_date: str) -> pd.DataFrame:
    sql = render_sql(blocks["treasury_curve"], start_date=start_date, end_date=end_date)
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        return df

    df = df.copy()
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df["maturity_year"] = pd.to_numeric(df["maturity_year"], errors="coerce")
    df["treasury_ytm"] = pd.to_numeric(df["yield_value"], errors="coerce") * 100.0
    return df.dropna(subset=["valuation_date", "maturity_year", "treasury_ytm"]).copy()


def build_curve_dict(curve: pd.DataFrame) -> dict[pd.Timestamp, tuple[np.ndarray, np.ndarray]]:
    curves: dict[pd.Timestamp, tuple[np.ndarray, np.ndarray]] = {}
    for valuation_date, group in curve.groupby("valuation_date"):
        ordered = group.sort_values("maturity_year")
        curves[pd.Timestamp(valuation_date)] = (
            ordered["maturity_year"].to_numpy(dtype=float),
            ordered["treasury_ytm"].to_numpy(dtype=float),
        )
    return curves


def add_treasury_yield(
    df: pd.DataFrame,
    curves: dict[pd.Timestamp, tuple[np.ndarray, np.ndarray]],
) -> pd.DataFrame:
    out = df.copy()
    out["treasury_ytm"] = np.nan
    if out.empty:
        return out

    for valuation_date, idx in out.groupby("valuation_date").groups.items():
        curve = curves.get(pd.Timestamp(valuation_date))
        if curve is None:
            continue
        tenors, yields = curve
        maturities = out.loc[idx, "remaining_years"].to_numpy(dtype=float)
        valid = (maturities >= tenors.min()) & (maturities <= tenors.max())
        if not valid.any():
            continue
        interpolated = np.full(len(maturities), np.nan)
        interpolated[valid] = np.interp(maturities[valid], tenors, yields)
        out.loc[idx, "treasury_ytm"] = interpolated
    return out.dropna(subset=["treasury_ytm"]).copy()


def empirical_percentile(history_spread: pd.Series, current_spread: float) -> float:
    clean = history_spread.dropna()
    if clean.empty:
        return np.nan
    return float(clean.le(current_spread).mean() * 100.0)


def filter_current_cross_section(
    current: pd.DataFrame,
    curve: pd.DataFrame,
) -> pd.DataFrame:
    curves = build_curve_dict(curve)
    current = add_treasury_yield(current, curves)
    if current.empty:
        return current

    current["credit_spread_bp"] = (current["bond_ytm"] - current["treasury_ytm"]) * 100.0
    if len(current) == 1:
        current["cross_section_percent_rank"] = 0.0
    else:
        current["cross_section_percent_rank"] = (
            current["credit_spread_bp"].rank(method="min") - 1
        ) / (len(current) - 1)
    return current[current["cross_section_percent_rank"] <= CROSS_SECTION_CUTOFF].copy()


def build_result(
    current: pd.DataFrame,
    history: pd.DataFrame,
    curve: pd.DataFrame,
) -> pd.DataFrame:
    if current.empty or history.empty or curve.empty:
        return empty_result()

    curves = build_curve_dict(curve)
    history = add_treasury_yield(history, curves)
    if history.empty:
        return empty_result()

    history["credit_spread_bp"] = (history["bond_ytm"] - history["treasury_ytm"]) * 100.0

    valid_counts = history.groupby("MAINCODE")["valuation_date"].nunique()
    valid_maincodes = set(valid_counts[valid_counts >= MIN_HISTORY_DAYS].index.astype(int))
    current = current[current["MAINCODE"].isin(valid_maincodes)].copy()
    history = history[history["MAINCODE"].isin(valid_maincodes)].copy()
    if current.empty:
        return empty_result()

    rows: list[dict[str, object]] = []
    for _, row in current.iterrows():
        maincode = int(row["MAINCODE"])
        series = history.loc[history["MAINCODE"] == maincode, "credit_spread_bp"]
        percentile = empirical_percentile(series, float(row["credit_spread_bp"]))
        rows.append(
            {
                "债券代码": row["bond_code"],
                "债券简称": row["bond_name"],
                "估值日": row["valuation_date"].strftime("%Y-%m-%d"),
                "剩余期限(年)": float(row["remaining_years"]),
                "个券估值收益率(%)": float(row["bond_ytm"]),
                "国债插值收益率(%)": float(row["treasury_ytm"]),
                "当前信用利差(bp)": float(row["credit_spread_bp"]),
                "近一年分位(%)": percentile,
            }
        )

    result = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    if result.empty:
        return result
    result = result.sort_values(
        ["近一年分位(%)", "当前信用利差(bp)", "债券代码"],
        ascending=[True, True, True],
    ).head(TOP_K)
    return result.reset_index(drop=True)


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    formatted = result.copy()
    if formatted.empty:
        return formatted
    for col in [
        "剩余期限(年)",
        "个券估值收益率(%)",
        "国债插值收益率(%)",
        "当前信用利差(bp)",
        "近一年分位(%)",
    ]:
        formatted[col] = formatted[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
    return formatted


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).strftime("%Y-%m-%d")
    blocks = load_sql_blocks()

    valuation_date = fetch_current_valuation_date(blocks, as_of_date)
    start_date = (valuation_date - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    end_date = valuation_date.strftime("%Y-%m-%d")

    current = fetch_current_candidates(blocks, valuation_date)
    if not current.empty:
        curve = fetch_treasury_curve(blocks, start_date, end_date)
        current = filter_current_cross_section(current, curve)

    if current.empty:
        result = empty_result()
    else:
        history = fetch_history_valuations(
            blocks,
            current["INNERCODE"].astype(int).tolist(),
            start_date,
            end_date,
        )
        result = build_result(current, history, curve)

    result = format_result(result)
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
