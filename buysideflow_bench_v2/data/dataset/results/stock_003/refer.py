"""Two PIT stock screens followed by close-to-close excess-return evaluation."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from string import Template

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe

OBSERVATIONS = ("2025-04-30", "2025-08-31")
HORIZONS = (5, 20)
COLUMNS = ["观察日", "持有期", "样本数", "平均超额收益", "中位数超额收益", "胜率"]
BLOCKS = dict(re.findall(
    r"^-- name: (\w+)\n(.*?)(?=^-- name:|\Z)",
    Path(__file__).with_name("refer.sql").read_text(), re.M | re.S,
))


def query(name: str, **params) -> pd.DataFrame:
    return query_to_dataframe(Template(BLOCKS[name]).substitute(**params),
                              max_rows=0, timeout=120)


def ids(values) -> str:
    return ",".join(str(int(x)) for x in sorted(set(values)))


def dates_for_observation(calendar: pd.DatetimeIndex, observation: str) -> dict:
    # The valuation includes the observation date; entry is strictly after it.
    pos = calendar.searchsorted(pd.Timestamp(observation), side="right")
    if pos == 0 or pos + max(HORIZONS) >= len(calendar):
        raise ValueError("Insufficient market calendar")
    return {"valuation": calendar[pos - 1], "entry": calendar[pos],
            **{h: calendar[pos + h] for h in HORIZONS}}


def ttm(profits: pd.Series, end: pd.Timestamp) -> float:
    value = profits.get(end, np.nan)
    if end.month == 12:
        return float(value)
    annual = pd.Timestamp(end.year - 1, 12, 31)
    previous = end - pd.DateOffset(years=1)
    return float(value + profits.get(annual, np.nan) - profits.get(previous, np.nan))


def growth_candidates(universe: pd.DataFrame, financials: pd.DataFrame) -> pd.DataFrame:
    reports = financials.copy()
    reports["ENDDATE"] = pd.to_datetime(reports["ENDDATE"])
    reports["profit"] = pd.to_numeric(reports["profit"], errors="coerce")
    rows = []
    for company, group in reports.groupby("COMPANYCODE"):
        if group["ENDDATE"].duplicated().any():
            raise ValueError("Multiple financial rows per company/period")
        profits = group.set_index("ENDDATE")["profit"]
        latest = profits.index.max()  # Never fall back from a missing latest value.
        current = ttm(profits, latest)
        previous = ttm(profits, latest - pd.DateOffset(years=2))
        if np.isfinite(current) and np.isfinite(previous) and current > 0 and previous > 0:
            growth = (current / previous) ** 0.5 - 1
            if growth > 0.25:
                rows.append({"COMPANYCODE": company, "report_end": latest,
                             "ttm_current": current, "ttm_previous": previous,
                             "growth": growth})
    return universe.merge(pd.DataFrame(rows, columns=[
        "COMPANYCODE", "report_end", "ttm_current", "ttm_previous", "growth"
    ]), on="COMPANYCODE", how="inner", validate="many_to_one")


def pe_candidates(candidates: pd.DataFrame, starts: pd.DataFrame,
                  history: pd.DataFrame, calendar: pd.DatetimeIndex,
                  valuation: pd.Timestamp) -> pd.DataFrame:
    first = starts.set_index("INNERCODE")["first_date"].map(pd.Timestamp)
    history = history.copy()
    history["TRADINGDAY"] = pd.to_datetime(history["TRADINGDAY"])
    history["PE"] = pd.to_numeric(history["PE"], errors="coerce")
    left = valuation - pd.DateOffset(years=1)
    history = history[history["TRADINGDAY"].isin(calendar)
                      & history["TRADINGDAY"].between(left, valuation)]
    rows = []
    for code, group in history.groupby("INNERCODE"):
        if code not in first or pd.isna(first[code]) or first[code] > left:
            continue
        values = group.set_index("TRADINGDAY")["PE"]
        if values.index.duplicated().any():
            raise ValueError("Multiple PE values per security/date")
        current = values.get(valuation, np.nan)
        positive = values[np.isfinite(values) & (values > 0)]
        if not np.isfinite(current) or current <= 0 or positive.empty:
            continue
        percentile = float((positive <= current).mean())
        if percentile <= 0.10:
            rows.append({"INNERCODE": code, "pe": current,
                         "pe_percentile": percentile, "pe_days": len(positive)})
    return candidates.merge(pd.DataFrame(rows, columns=[
        "INNERCODE", "pe", "pe_percentile", "pe_days"
    ]), on="INNERCODE", how="inner", validate="one_to_one")


def evaluate(candidates: pd.DataFrame, prices: pd.DataFrame,
             benchmark: pd.DataFrame, dates: dict) -> pd.DataFrame:
    prices = prices.copy()
    benchmark = benchmark.copy()
    prices["TRADINGDAY"] = pd.to_datetime(prices["TRADINGDAY"])
    benchmark["TRADINGDAY"] = pd.to_datetime(benchmark["TRADINGDAY"])
    prices["BACKWARDPRICE"] = pd.to_numeric(prices["BACKWARDPRICE"], errors="coerce")
    bench = pd.to_numeric(benchmark.set_index("TRADINGDAY")["CLOSEPRICE"])
    endpoints = [dates["entry"], *[dates[h] for h in HORIZONS]]
    if bench.index.duplicated().any() or not np.isfinite(bench.reindex(endpoints)).all() \
            or not (bench.reindex(endpoints) > 0).all():
        raise ValueError("Missing or ambiguous benchmark endpoint")
    panel = prices.pivot(index="INNERCODE", columns="TRADINGDAY", values="BACKWARDPRICE")
    panel = panel.reindex(index=candidates["INNERCODE"], columns=endpoints)
    # The task explicitly excludes a stock if either holding period lacks an endpoint.
    panel = panel[np.isfinite(panel).all(axis=1) & (panel > 0).all(axis=1)]
    detail = candidates.set_index("INNERCODE").loc[panel.index].copy()
    detail["entry_date"] = dates["entry"]
    detail["entry_price"] = panel[dates["entry"]]
    for h in HORIZONS:
        detail[f"exit_{h}"] = dates[h]
        detail[f"exit_price_{h}"] = panel[dates[h]]
        detail[f"excess_{h}"] = (panel[dates[h]] / panel[dates["entry"]]
                                 - bench[dates[h]] / bench[dates["entry"]])
    return detail.reset_index()


def build_result() -> tuple[pd.DataFrame, pd.DataFrame]:
    index = query("index_id")
    if len(index) != 1:
        raise ValueError("CSI 300 index identity is not unique")
    index_id = int(index.iloc[0]["INNERCODE"])
    calendar = pd.DatetimeIndex(pd.to_datetime(query("calendar")["trade_date"])).sort_values()
    summaries, details = [], []
    for observation in OBSERVATIONS:
        dates = dates_for_observation(calendar, observation)
        universe = query("universe", index_id=index_id, observation=observation)
        if universe.empty or universe["INNERCODE"].duplicated().any():
            raise ValueError("Empty or duplicated historical universe")
        financials = query("financials", company_ids=ids(universe["COMPANYCODE"]),
                           observation=observation,
                           cutoff=(pd.Timestamp(observation) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        selected = growth_candidates(universe, financials)
        growth_count = len(selected)
        if not selected.empty:
            params = dict(inner_ids=ids(selected["INNERCODE"]),
                          valuation=dates["valuation"].strftime("%Y-%m-%d"))
            starts = query("pe_start", **params)
            history = query("pe_window", **params,
                            window_start=(dates["valuation"] - pd.DateOffset(years=1)).strftime("%Y-%m-%d"))
            selected = pe_candidates(selected, starts, history, calendar, dates["valuation"])
        screened_count = len(selected)
        if not selected.empty:
            price_dates = ",".join("'" + dates[k].strftime("%Y-%m-%d") + "'"
                                   for k in ("entry", *HORIZONS))
            prices = query("stock_prices", inner_ids=ids(selected["INNERCODE"]), price_dates=price_dates)
            benchmark = query("benchmark_prices", index_id=index_id, price_dates=price_dates)
            selected = evaluate(selected, prices, benchmark, dates)
        selected["observation"] = observation
        selected["valuation_date"] = dates["valuation"]
        details.append(selected)
        print(f"{observation}: universe={len(universe)}, growth={growth_count}, "
              f"screened={screened_count}, complete_prices={len(selected)}", flush=True)
        for h in HORIZONS:
            returns = selected[f"excess_{h}"] if not selected.empty else pd.Series(dtype=float)
            summaries.append([observation, h, len(returns), returns.mean() * 100,
                              returns.median() * 100,
                              (returns > 0).mean() * 100 if len(returns) else np.nan])
    return pd.DataFrame(summaries, columns=COLUMNS), pd.concat(details, ignore_index=True)


def main() -> pd.DataFrame:
    result, _ = build_result()
    result.to_csv(Path(__file__).with_name("result.csv"), index=False,
                  encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
