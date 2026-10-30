#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-08-29"
NEXT_DAY_TS = "2025-08-30 00:00:00"
LOOKBACK_DAYS = 20
TOP_N = 10
RATE_SHOCK_BP = 15.0
SPREAD_SHOCK_BP = 25.0
CHUNK_SIZE = 900
VALUATION_LOOKBACK_NATURAL_DAYS = 30

RESULT_COLUMNS = [
    "入选债券数",
    "组合平均修正久期",
    "组合利率冲击损益(%)",
    "组合利差冲击损益(%)",
    "组合合计损益(%)",
]


def sql_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values, size: int = CHUNK_SIZE):
    values = sorted({int(v) for v in values if pd.notna(v)})
    for i in range(0, len(values), size):
        yield values[i : i + size]


def fetch_recent_trade_dates() -> list[str]:
    sql = f"""
    SELECT DATE(TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET = 83
      AND TRADINGDATE <= DATE('{AS_OF_DATE}')
    ORDER BY TRADINGDATE DESC
    LIMIT {LOOKBACK_DAYS}
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=60)
    if df is None or df.empty:
        raise ValueError("无法取得近 20 个交易日")
    return sorted(pd.to_datetime(df["trade_date"], errors="coerce").dropna().dt.strftime("%Y-%m-%d").tolist())


def load_candidate_pool() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=900)
    if df is None or df.empty:
        raise ValueError("候选券池为空")

    df = df.copy()
    for col in ["maincode", "innercode", "companycode", "secumarket"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    required = [
        "valuation_date",
        "maincode",
        "innercode",
        "bond_code",
        "bond_name",
        "secumarket",
    ]
    df = df.dropna(subset=required).copy()
    if df.empty:
        raise ValueError("候选券池缺少必要字段")

    for col in ["maincode", "innercode", "companycode", "secumarket"]:
        df[col] = df[col].astype("int64")
    df["bond_code"] = df["bond_code"].astype(str)
    df["bond_name"] = df["bond_name"].astype(str)
    return df.reset_index(drop=True)


def fetch_latest_valuation(innercodes: list[int]) -> pd.DataFrame:
    remaining = {int(v) for v in innercodes if pd.notna(v)}
    frames: list[pd.DataFrame] = []
    valuation_dates = pd.date_range(
        end=pd.Timestamp(AS_OF_DATE),
        periods=VALUATION_LOOKBACK_NATURAL_DAYS + 1,
        freq="D",
    )[::-1]
    for valuation_date in valuation_dates:
        if not remaining:
            break
        date_str = valuation_date.strftime("%Y-%m-%d")
        for part in list(chunks(remaining)):
            sql = f"""
            SELECT
                CAST(INNERCODE AS UNSIGNED) AS innercode,
                DATE(ENDDATE) AS valuation_enddate,
                ID AS valuation_id,
                VPYIELD AS valuation_yield_pct,
                COALESCE(VPADURATION, TRUEADURATION) AS modified_duration
            FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL)
            WHERE INNERCODE IN ({sql_list(part)})
              AND ENDDATE = DATE('{date_str}')
              AND CREDIBILITYCODE = 1
              AND YIELDCODE = 2
              AND VPYIELD IS NOT NULL
              AND COALESCE(VPADURATION, TRUEADURATION) IS NOT NULL
              AND COALESCE(VPADURATION, TRUEADURATION) > 0
            """
            day = query_to_dataframe(sql, max_rows=0, timeout=90)
            if day is None or day.empty:
                continue

            day = day.copy()
            for col in ["innercode", "valuation_id", "valuation_yield_pct", "modified_duration"]:
                day[col] = pd.to_numeric(day[col], errors="coerce")
            day["valuation_enddate"] = pd.to_datetime(day["valuation_enddate"], errors="coerce")
            day = day.dropna(
                subset=["innercode", "valuation_enddate", "valuation_yield_pct", "modified_duration"]
            ).copy()
            day = day[(day["valuation_yield_pct"] > 0) & (day["modified_duration"] > 0)].copy()
            if day.empty:
                continue
            day["innercode"] = day["innercode"].astype("int64")
            day = day[day["innercode"].isin(remaining)].copy()
            if day.empty:
                continue
            day["valuation_id"] = day["valuation_id"].fillna(0).astype("int64")
            day = day.sort_values(["innercode", "valuation_id"], ascending=[True, False])
            day = day.drop_duplicates("innercode", keep="first")
            frames.append(day)
            remaining -= set(day["innercode"].tolist())

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return pd.DataFrame(
            columns=["innercode", "valuation_enddate", "valuation_id", "valuation_yield_pct", "modified_duration"]
        )

    for col in ["innercode", "valuation_id", "valuation_yield_pct", "modified_duration"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["valuation_enddate"] = pd.to_datetime(df["valuation_enddate"], errors="coerce")
    df = df.dropna(
        subset=["innercode", "valuation_enddate", "valuation_yield_pct", "modified_duration"]
    ).copy()
    df = df[(df["valuation_yield_pct"] > 0) & (df["modified_duration"] > 0)].copy()
    if df.empty:
        return pd.DataFrame(
            columns=["innercode", "valuation_enddate", "valuation_id", "valuation_yield_pct", "modified_duration"]
        )
    df["innercode"] = df["innercode"].astype("int64")
    df["valuation_id"] = df["valuation_id"].fillna(0).astype("int64")
    return df.reset_index(drop=True)


def fetch_turnover(code_map: pd.DataFrame, trade_dates: list[str]) -> pd.DataFrame:
    code_map = code_map[["maincode", "innercode", "secumarket"]].drop_duplicates().copy()
    if code_map.empty:
        return pd.DataFrame(columns=["maincode", "trade_days_20", "turnover_value_20d"])

    date_sql = ",".join(f"DATE('{d}')" for d in trade_dates)
    frames: list[pd.DataFrame] = []
    for market, market_map in code_map.groupby("secumarket"):
        for part in chunks(market_map["innercode"].tolist(), size=300):
            sql = f"""
            SELECT
                CAST(q.INNERCODE AS UNSIGNED) AS innercode,
                DATE(q.TRADINGDAY) AS trading_day,
                SUM(q.TURNOVERVALUE) AS turnover_value
            FROM bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
            WHERE q.INNERCODE IN ({sql_list(part)})
              AND q.SECUMARKET = {int(market)}
              AND q.TRADINGDAY IN ({date_sql})
              AND COALESCE(q.TURNOVERVALUE, 0) > 0
            GROUP BY q.INNERCODE, DATE(q.TRADINGDAY)
            """
            frames.append(query_to_dataframe(sql, max_rows=0, timeout=180))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return pd.DataFrame(columns=["maincode", "trade_days_20", "turnover_value_20d"])

    df["innercode"] = pd.to_numeric(df["innercode"], errors="coerce")
    df["turnover_value"] = pd.to_numeric(df["turnover_value"], errors="coerce")
    df["trading_day"] = pd.to_datetime(df["trading_day"], errors="coerce")
    df = df.dropna(subset=["innercode", "trading_day", "turnover_value"]).copy()
    df["innercode"] = df["innercode"].astype("int64")

    mapped = df.merge(code_map, on="innercode", how="inner")
    if mapped.empty:
        return pd.DataFrame(columns=["maincode", "trade_days_20", "turnover_value_20d"])

    by_day = (
        mapped.groupby(["maincode", "trading_day"], as_index=False)["turnover_value"]
        .sum()
    )
    out = (
        by_day.groupby("maincode", as_index=False)
        .agg(
            trade_days_20=("trading_day", "nunique"),
            turnover_value_20d=("turnover_value", "sum"),
        )
    )

    for col in ["maincode", "trade_days_20"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["turnover_value_20d"] = pd.to_numeric(out["turnover_value_20d"], errors="coerce")
    out = out.dropna(subset=["maincode", "trade_days_20", "turnover_value_20d"]).copy()
    out["maincode"] = out["maincode"].astype("int64")
    out["trade_days_20"] = out["trade_days_20"].astype("int64")
    return out


def build_selected_sample() -> pd.DataFrame:
    raw_pool = load_candidate_pool()
    valuation = fetch_latest_valuation(raw_pool["innercode"].tolist())
    valued_pool = raw_pool.merge(valuation, on="innercode", how="inner")
    if valued_pool.empty:
        raise ValueError("候选券池无有效估值收益率或修正久期")

    valued_pool["market_rank"] = (valued_pool["secumarket"] != 89).astype("int64")
    pool = valued_pool.sort_values(
        ["maincode", "valuation_enddate", "market_rank", "bond_code", "innercode", "valuation_id"],
        ascending=[True, False, True, True, True, False],
    ).drop_duplicates("maincode", keep="first")

    trade_dates = fetch_recent_trade_dates()
    turnover_map = raw_pool[raw_pool["maincode"].isin(pool["maincode"])][["maincode", "innercode", "secumarket"]]
    turnover = fetch_turnover(turnover_map, trade_dates)
    sample = pool.merge(turnover, on="maincode", how="inner")
    sample = sample[sample["turnover_value_20d"] > 0].copy()
    if sample.empty:
        raise ValueError("近 20 日有成交的候选券为空")

    sample = sample.sort_values(
        ["turnover_value_20d", "bond_code"],
        ascending=[False, True],
    ).head(TOP_N).copy()
    return sample.reset_index(drop=True)


def main() -> pd.DataFrame:
    sample = build_selected_sample()
    sample["rate_pnl_pct"] = -sample["modified_duration"] * RATE_SHOCK_BP / 100.0
    sample["spread_pnl_pct"] = -sample["modified_duration"] * SPREAD_SHOCK_BP / 100.0
    sample["total_pnl_pct"] = sample["rate_pnl_pct"] + sample["spread_pnl_pct"]

    result = pd.DataFrame(
        [
            {
                "入选债券数": int(len(sample)),
                "组合平均修正久期": round(float(sample["modified_duration"].mean()), 4),
                "组合利率冲击损益(%)": round(float(sample["rate_pnl_pct"].mean()), 2),
                "组合利差冲击损益(%)": round(float(sample["spread_pnl_pct"].mean()), 2),
                "组合合计损益(%)": round(float(sample["total_pnl_pct"].mean()), 2),
            }
        ],
        columns=RESULT_COLUMNS,
    )
    return result


if __name__ == "__main__":
    result = main()
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"wrote {out_path} rows={len(result)}")
