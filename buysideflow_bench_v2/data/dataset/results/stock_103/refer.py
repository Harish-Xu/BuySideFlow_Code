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

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


AS_OF_DATE = pd.Timestamp("2025-10-30")
REPORT_DATE = pd.Timestamp("2025-09-30")
WINDOW_DAYS = 60
MA_DAYS = 55
BREAKOUT_DAYS = 3
LOW_DAYS = 10
MARKET_VALUE_FLOOR = 5_000_000_000
OUTPUT_COLUMNS = ["股票代码", "股票简称", "ROE", "MA55偏离", "低点偏离", "流通市值(亿元)"]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def load_sql_blocks() -> dict[str, str]:
    text = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    return {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(text)
    }


def query(sql: str, **params: object) -> pd.DataFrame:
    rendered = Template(sql).substitute({key: str(value) for key, value in params.items()})
    result = query_to_dataframe(rendered, max_rows=0, timeout=300)
    if result is None:
        raise RuntimeError("SQL execution returned None")
    return result


def comma_separated_ids(values: pd.Series) -> str:
    ids = sorted({int(value) for value in values.dropna()})
    if not ids:
        raise ValueError("Cannot build an empty SQL identifier list")
    return ",".join(map(str, ids))


def build_result(
    candidates: pd.DataFrame,
    quotes: pd.DataFrame,
    trade_days: pd.DatetimeIndex,
) -> pd.DataFrame:
    days = pd.DatetimeIndex(pd.to_datetime(trade_days)).drop_duplicates().sort_values()
    if len(days) < WINDOW_DAYS:
        raise ValueError(f"Fewer than {WINDOW_DAYS} market trading days")
    window_days = days[-WINDOW_DAYS:]

    quotes = quotes.copy()
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    for column in ["adjusted_close", "adjusted_low", "TURNOVERVOLUME"]:
        quotes[column] = pd.to_numeric(quotes[column], errors="coerce")
    quote_groups = {
        innercode: group
        for innercode, group in quotes.groupby("INNERCODE", sort=False)
    }

    rows: list[dict[str, object]] = []
    for innercode, candidate in candidates.set_index("INNERCODE").iterrows():
        group = quote_groups.get(innercode)
        if group is None:
            continue
        window = (
            group.sort_values("trade_date")
            .drop_duplicates("trade_date", keep="last")
            .set_index("trade_date")
            .reindex(window_days)
        )
        required = window[["adjusted_close", "adjusted_low"]]
        if required.isna().any().any() or (required <= 0).any().any():
            continue

        close = window["adjusted_close"]
        low = window["adjusted_low"]
        ma55 = close.rolling(MA_DAYS, min_periods=MA_DAYS).mean()
        crossed_above = (close > ma55) & (close.shift(1) <= ma55.shift(1))
        recent_cross = crossed_above.iloc[-BREAKOUT_DAYS:].any()

        current_close = close.iloc[-1]
        current_ma = ma55.iloc[-1]
        current_turnover = window["TURNOVERVOLUME"].iloc[-1]
        low_10 = low.iloc[-LOW_DAYS:].min()
        low_60 = low.min()
        ma_deviation = current_close / current_ma - 1
        low_deviation = low_10 / low_60 - 1

        if (
            pd.isna(current_turnover)
            or current_turnover <= 0
            or pd.isna(current_ma)
            or current_close <= current_ma
            or not recent_cross
            or low_10 > low_60 * 1.02
        ):
            continue

        rows.append(
            {
                "股票代码": str(candidate["stock_code"]).zfill(6),
                "股票简称": candidate["stock_name"],
                "ROE_raw": float(candidate["ROE"]),
                "MA55偏离_raw": ma_deviation * 100,
                "低点偏离_raw": low_deviation * 100,
                "流通市值_raw": float(candidate["NEGOTIABLEMV"]) / 100_000_000,
            }
        )

    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    result = (
        pd.DataFrame(rows)
        .sort_values(["ROE_raw", "股票代码"], ascending=[False, True])
        .head(10)
        .reset_index(drop=True)
    )
    return pd.DataFrame(
        {
            "股票代码": result["股票代码"],
            "股票简称": result["股票简称"],
            "ROE": result["ROE_raw"].round(2),
            "MA55偏离": result["MA55偏离_raw"].round(2),
            "低点偏离": result["低点偏离_raw"].round(2),
            "流通市值(亿元)": result["流通市值_raw"].round(2),
        },
        columns=OUTPUT_COLUMNS,
    )


def main() -> pd.DataFrame:
    blocks = load_sql_blocks()
    common = {
        "as_of_date": AS_OF_DATE.strftime("%Y-%m-%d"),
        "report_date": REPORT_DATE.strftime("%Y-%m-%d"),
    }

    with shared_connection_session():
        days = query(blocks["trade_days"], **common)
        trade_days = pd.DatetimeIndex(pd.to_datetime(days["trade_date"])).sort_values()
        if len(trade_days) != WINDOW_DAYS or trade_days[-1] != AS_OF_DATE:
            raise ValueError("The fixed 60-day market window does not end on 2025-10-30")

        pool = query(blocks["security_pool"], **common)
        if pool.empty:
            raise ValueError("No listed securities in the requested market scope")

        innercodes = comma_separated_ids(pool["INNERCODE"])
        active_risk = query(
            blocks["active_risk_alerts"],
            innercodes=innercodes,
            **common,
        )
        if not active_risk.empty:
            pool = pool[~pool["INNERCODE"].isin(active_risk["INNERCODE"])]
        if pool.empty:
            raise ValueError("No securities remain after the ST/*ST filter")

        innercodes = comma_separated_ids(pool["INNERCODE"])
        valuation = query(
            blocks["valuation_data"],
            innercodes=innercodes,
            **common,
        )
        pool = pool.merge(valuation, on="INNERCODE", how="inner")
        pool["NEGOTIABLEMV"] = pd.to_numeric(pool["NEGOTIABLEMV"], errors="coerce")
        pool = pool[pool["NEGOTIABLEMV"] >= MARKET_VALUE_FLOOR]
        if pool.empty:
            raise ValueError("No securities pass the float-market-value filter")

        financial = query(
            blocks["financial_data"],
            companycodes=comma_separated_ids(pool["COMPANYCODE"]),
            **common,
        )
        financial["ROE"] = pd.to_numeric(financial["ROE"], errors="coerce")
        financial = financial[financial["ROE"] > 0]
        candidates = pool.merge(financial, on="COMPANYCODE", how="inner")
        if candidates.empty:
            raise ValueError("No candidates have positive 2025Q3 ROE")

        quote_parts: list[pd.DataFrame] = []
        candidate_codes = sorted({int(value) for value in candidates["INNERCODE"]})
        for offset in range(0, len(candidate_codes), 200):
            batch = ",".join(map(str, candidate_codes[offset : offset + 200]))
            quote_parts.append(
                query(
                    blocks["quote_data"],
                    innercodes=batch,
                    start_date=trade_days[0].strftime("%Y-%m-%d"),
                    end_date=trade_days[-1].strftime("%Y-%m-%d"),
                    **common,
                )
            )
        quotes = pd.concat(quote_parts, ignore_index=True)

    result = build_result(candidates, quotes, trade_days)
    output = Path(__file__).with_name("result.csv")
    result.to_csv(output, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
