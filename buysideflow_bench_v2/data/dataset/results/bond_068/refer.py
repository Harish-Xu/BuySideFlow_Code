#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import sys
import time
from pathlib import Path

import pandas as pd


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "sweagent" / "text2sql" / "db_connector.py").exists():
            return parent
    raise RuntimeError("Cannot locate repository root")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2024-12-31"
HISTORY_DAYS = 60
LIQUIDITY_DAYS = 20

OUTPUT_COLUMNS = [
    "证券代码",
    "转债简称",
    "收盘价",
    "到期收益率",
    "剩余期限",
    "转股溢价率",
    "近60日波动率",
]

CHINESE_YEAR_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def _to_float(value: object, default: float | None = None) -> float | None:
    if value is None or pd.isna(value):
        return default
    try:
        return float(value)
    except Exception:
        return default


def _chinese_year_to_int(text: str) -> int | None:
    text = text.strip()
    if text in CHINESE_YEAR_MAP:
        return CHINESE_YEAR_MAP[text]
    if len(text) == 2 and text.startswith("十"):
        return 10 + CHINESE_YEAR_MAP.get(text[1:], 0)
    if len(text) == 2 and text.endswith("十"):
        return CHINESE_YEAR_MAP.get(text[:1], 0) * 10
    if "十" in text:
        left, right = text.split("十", 1)
        return CHINESE_YEAR_MAP.get(left, 1) * 10 + CHINESE_YEAR_MAP.get(right, 0)
    return None


def _parse_coupon_schedule(text: object) -> dict[int, float]:
    if text is None or pd.isna(text):
        return {}
    normalized = str(text).replace("％", "%").replace(" ", "")
    schedule: dict[int, float] = {}
    pattern = re.compile(r"第([一二三四五六七八九十]+)年[^0-9%]{0,30}([0-9]+(?:\.[0-9]+)?)%")
    for year_text, rate_text in pattern.findall(normalized):
        year = _chinese_year_to_int(year_text)
        if year is not None:
            schedule[year] = float(rate_text)
    return schedule


def _solve_ytm(price: float, cashflows: list[tuple[float, float]]) -> float | None:
    if price <= 0 or not cashflows:
        return None

    def npv(rate: float) -> float:
        return sum(amount / ((1.0 + rate) ** years) for years, amount in cashflows) - price

    low = -0.95
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    while high_value > 0 and high < 100:
        high *= 2
        high_value = npv(high)
    if low_value * high_value > 0:
        return None

    for _ in range(100):
        mid = (low + high) / 2
        mid_value = npv(mid)
        if abs(mid_value) < 1e-10:
            return mid
        if low_value * mid_value <= 0:
            high = mid
            high_value = mid_value
        else:
            low = mid
            low_value = mid_value
    return (low + high) / 2


def _estimate_ytm(row: pd.Series) -> float | None:
    price = _to_float(row.get("close_price"))
    par = _to_float(row.get("PARVALUE"), 100.0) or 100.0
    if price is None or price <= 0:
        return None

    obs_date = pd.Timestamp(row.get("obs_date") or AS_OF_DATE)
    start_date = pd.to_datetime(row.get("start_date"), errors="coerce")
    end_date = pd.to_datetime(row.get("end_date"), errors="coerce")
    if pd.isna(start_date) or pd.isna(end_date) or end_date <= obs_date:
        return None

    maturity = _to_float(row.get("MATURITY"))
    max_year = int(math.ceil(maturity if maturity and maturity > 0 else ((end_date - start_date).days / 365.25)))
    if max_year <= 0:
        return None

    schedule = _parse_coupon_schedule(row.get("INTERESTITEM"))
    fallback_coupon = _to_float(row.get("LATESTCOUPONRATE"))
    if fallback_coupon is None:
        fallback_coupon = _to_float(row.get("COUPONRATE"), 0.0) or 0.0

    redemption = _to_float(row.get("CALLPRICEEND"))
    if redemption is None or redemption <= 0:
        redeem_interest = _to_float(row.get("REDEEMOFINTEREST"), 0.0) or 0.0
        redemption = par + redeem_interest
    redemption = max(redemption, par)

    cashflows: list[tuple[float, float]] = []
    final_added = False
    for year in range(1, max_year + 1):
        pay_date = start_date + pd.DateOffset(years=year)
        if pay_date >= end_date or year == max_year:
            pay_date = end_date
            amount = redemption
            final_added = True
        else:
            rate = schedule.get(year, fallback_coupon)
            amount = par * rate / 100.0
        if pay_date > obs_date and amount > 0:
            years = max((pay_date - obs_date).days / 365.25, 1 / 365.25)
            cashflows.append((years, amount))

    if not final_added:
        years = max((end_date - obs_date).days / 365.25, 1 / 365.25)
        cashflows.append((years, redemption))

    return _solve_ytm(price, cashflows)


def _fetch_history(innercodes: list[int]) -> pd.DataFrame:
    if not innercodes:
        return pd.DataFrame(columns=["INNERCODE", "TRADINGDAY", "CLOSEPRICE", "TURNOVERVALUE"])
    code_sql = ",".join(str(int(x)) for x in sorted(set(innercodes)))
    sql = f"""
    WITH obs_day AS (
        SELECT MAX(TRADINGDAY) AS obs_date
        FROM bond_conbdexchangequote
        WHERE TRADINGDAY <= '{AS_OF_DATE}'
    ),
    trade_dates AS (
        SELECT trading_day
        FROM (
            SELECT
                d.trading_day,
                ROW_NUMBER() OVER (ORDER BY d.trading_day DESC) AS rn
            FROM (
                SELECT DISTINCT q.TRADINGDAY AS trading_day
                FROM bond_conbdexchangequote q
                JOIN obs_day o
                  ON q.TRADINGDAY <= o.obs_date
            ) d
        ) x
        WHERE rn <= {HISTORY_DAYS}
    )
    SELECT
        q.INNERCODE,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        q.TURNOVERVALUE
    FROM bond_conbdexchangequote q FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
    JOIN trade_dates d
      ON q.TRADINGDAY = d.trading_day
    WHERE q.INNERCODE IN ({code_sql})
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
    ORDER BY q.INNERCODE, q.TRADINGDAY;
    """
    history = query_to_dataframe(sql, max_rows=0, timeout=600)
    if history is None or history.empty:
        return pd.DataFrame(columns=["INNERCODE", "TRADINGDAY", "CLOSEPRICE", "TURNOVERVALUE"])
    history = history.copy()
    history["INNERCODE"] = pd.to_numeric(history["INNERCODE"]).astype("int64")
    history["TRADINGDAY"] = pd.to_datetime(history["TRADINGDAY"])
    history["CLOSEPRICE"] = pd.to_numeric(history["CLOSEPRICE"], errors="coerce")
    history["TURNOVERVALUE"] = pd.to_numeric(history["TURNOVERVALUE"], errors="coerce")
    return history.dropna(subset=["CLOSEPRICE"])


def _history_metrics(history: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for innercode, group in history.groupby("INNERCODE", sort=False):
        group = group.sort_values("TRADINGDAY").tail(HISTORY_DAYS)
        if len(group) < HISTORY_DAYS:
            continue
        returns = group["CLOSEPRICE"].pct_change().dropna()
        if len(returns) < HISTORY_DAYS - 1:
            continue
        turnover_20d = group.tail(LIQUIDITY_DAYS)["TURNOVERVALUE"]
        if turnover_20d.notna().sum() < LIQUIDITY_DAYS:
            continue
        rows.append(
            {
                "INNERCODE": int(innercode),
                "price_volatility_60d_pct": float(returns.std(ddof=0) * math.sqrt(252) * 100.0),
                "turnover_value_median_20d": float(turnover_20d.median()),
            }
        )
    return pd.DataFrame(rows)


def _build_result() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    base = query_to_dataframe(sql_path.read_text(encoding="utf-8"), max_rows=0, timeout=600)
    if base is None or base.empty:
        return _empty_result()

    base = base.copy()
    base["INNERCODE"] = pd.to_numeric(base["INNERCODE"]).astype("int64")
    numeric_cols = [
        "close_price",
        "convert_premium_rate",
        "remaining_maturity_years",
        "PARVALUE",
        "MATURITY",
        "COUPONRATE",
        "LATESTCOUPONRATE",
        "CALLPRICEEND",
        "REDEEMOFINTEREST",
    ]
    for col in numeric_cols:
        base[col] = pd.to_numeric(base[col], errors="coerce")

    base["ytm_to_maturity_pct"] = base.apply(_estimate_ytm, axis=1) * 100.0
    base = base.dropna(subset=["ytm_to_maturity_pct"])
    base = base[base["ytm_to_maturity_pct"] > 0].copy()
    if base.empty:
        return _empty_result()

    metrics = _history_metrics(_fetch_history(base["INNERCODE"].tolist()))
    if metrics.empty:
        return _empty_result()

    candidates = base.merge(metrics, on="INNERCODE", how="inner")
    candidates = candidates.dropna(
        subset=[
            "turnover_value_median_20d",
            "price_volatility_60d_pct",
            "convert_premium_rate",
        ]
    ).copy()
    if candidates.empty:
        return _empty_result()

    liquidity_cutoff = candidates["turnover_value_median_20d"].median()
    candidates = candidates[candidates["turnover_value_median_20d"] >= liquidity_cutoff].copy()
    if candidates.empty:
        return _empty_result()

    candidates = candidates.sort_values(
        ["convert_premium_rate", "price_volatility_60d_pct", "SECUCODE"],
        ascending=[True, True, True],
    ).head(20)

    output = pd.DataFrame(
        {
            "证券代码": candidates["SECUCODE"].astype(str),
            "转债简称": candidates["SECUABBR"],
            "收盘价": candidates["close_price"],
            "到期收益率": candidates["ytm_to_maturity_pct"],
            "剩余期限": candidates["remaining_maturity_years"],
            "转股溢价率": candidates["convert_premium_rate"],
            "近60日波动率": candidates["price_volatility_60d_pct"],
        }
    )
    return output[OUTPUT_COLUMNS].reset_index(drop=True)


def main() -> pd.DataFrame:
    started = time.time()
    result = _build_result()
    result_dir = Path(__file__).resolve().parent
    result.to_csv(result_dir / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    print(f"# elapsed_seconds={time.time() - started:.2f}", file=sys.stderr)
    return result


if __name__ == "__main__":
    print(main().to_csv(index=False, float_format="%.2f"), end="")
