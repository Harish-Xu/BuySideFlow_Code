#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

AS_OF_DATE = "2026-02-27"
START_DATE = "2026-01-28"
MONEY_FUND_CODE = 1402
TOP_SCALE_N = 20
TOP_RETURN_N = 5
OUTPUT_DIR = Path(__file__).resolve().parent


def sql_in(values: list[int]) -> str:
    return "(" + ", ".join(str(int(value)) for value in values) + ")"


def fmt2(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.2f}"


def fetch_top_scale_funds() -> pd.DataFrame:
    sql = f"""
    WITH type_ranked AS (
        SELECT
            t.INNERCODE,
            t.SECASSETCATCODE,
            ROW_NUMBER() OVER (
                PARTITION BY t.INNERCODE
                ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
            ) AS rn
        FROM mf_jyfundtype t
        WHERE t.STANDARD = 75
          AND t.EFFECTIVEDATE <= DATE('{AS_OF_DATE}')
          AND (t.CANCELDATE IS NULL OR t.CANCELDATE > DATE('{AS_OF_DATE}'))
          AND t.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
    ),
    fund_pool AS (
        SELECT
            fa.INNERCODE,
            sm.SECUCODE,
            COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS fund_name
        FROM mf_fundarchives fa
        JOIN secumain sm
          ON sm.INNERCODE = fa.INNERCODE
        JOIN type_ranked tr
          ON tr.INNERCODE = fa.INNERCODE
         AND tr.rn = 1
        WHERE sm.SECUCATEGORY = 8
          AND tr.SECASSETCATCODE = {MONEY_FUND_CODE}
          AND fa.TYPE = 2
          AND fa.STARTDATE <= DATE('{AS_OF_DATE}')
          AND fa.MAINCODE = fa.SECURITYCODE
          AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > DATE('{AS_OF_DATE}'))
          AND COALESCE(fa.IFFOF, 2) <> 1
    ),
    scale_ranked AS (
        SELECT
            fp.INNERCODE,
            fp.SECUCODE,
            fp.fund_name,
            DATE(sa.ENDDATE) AS scale_enddate,
            sa.COMBNVI,
            ROW_NUMBER() OVER (
                PARTITION BY fp.INNERCODE
                ORDER BY sa.ENDDATE DESC, sa.INSERTTIME DESC, sa.JSID DESC, sa.ID DESC
            ) AS rn
        FROM fund_pool fp
        JOIN mf_scaleanalysis sa
          ON sa.INNERCODE = fp.INNERCODE
        WHERE sa.ENDDATE <= DATE('{AS_OF_DATE}')
          AND sa.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
    ),
    top_scale AS (
        SELECT
            INNERCODE,
            SECUCODE,
            fund_name,
            scale_enddate,
            COMBNVI
        FROM scale_ranked
        WHERE rn = 1 AND COMBNVI > 0
        ORDER BY COMBNVI DESC, SECUCODE ASC
        LIMIT {TOP_SCALE_N}
    ),
    purchase_ranked AS (
        SELECT
            ts.INNERCODE,
            pr.APPLYINGTYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ts.INNERCODE
                ORDER BY pr.ENDDATE DESC, pr.INSERTTIME DESC, pr.JSID DESC, pr.ID DESC
            ) AS rn
        FROM top_scale ts
        LEFT JOIN mf_purchaseandredeem pr
          ON pr.INNERCODE = ts.INNERCODE
         AND pr.ENDDATE <= DATE('{AS_OF_DATE}')
         AND pr.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
    )
    SELECT
        ts.INNERCODE,
        ts.SECUCODE,
        ts.fund_name,
        ts.scale_enddate,
        ts.COMBNVI,
        pr.APPLYINGTYPE AS applying_type
    FROM top_scale ts
    LEFT JOIN purchase_ranked pr
      ON pr.INNERCODE = ts.INNERCODE
     AND pr.rn = 1
    ORDER BY ts.COMBNVI DESC, ts.SECUCODE ASC
    """
    funds = query_to_dataframe(sql, max_rows=0)
    if funds.empty:
        raise ValueError("未取到存续开放式货币主基金的合并规模样本。")
    funds["INNERCODE"] = pd.to_numeric(funds["INNERCODE"], errors="raise").astype(int)
    funds["COMBNVI"] = pd.to_numeric(funds["COMBNVI"], errors="coerce")
    funds["scale_enddate"] = pd.to_datetime(funds["scale_enddate"])
    return funds


def fetch_nav(fund_innercodes: list[int]) -> pd.DataFrame:
    sql = f"""
    WITH nav_ranked AS (
        SELECT
            n.INNERCODE,
            DATE(n.TRADINGDAY) AS trading_day,
            n.UNITNVRESTORED,
            ROW_NUMBER() OVER (
                PARTITION BY n.INNERCODE, n.TRADINGDAY
                ORDER BY n.INSERTTIME DESC, n.JSID DESC, n.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans n
        WHERE n.INNERCODE IN {sql_in(fund_innercodes)}
          AND n.TRADINGDAY IN (DATE('{START_DATE}'), DATE('{AS_OF_DATE}'))
          AND n.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
    )
    SELECT
        INNERCODE,
        trading_day,
        UNITNVRESTORED
    FROM nav_ranked
    WHERE rn = 1
    ORDER BY INNERCODE, trading_day
    """
    nav = query_to_dataframe(sql, max_rows=0)
    if nav.empty:
        raise ValueError("规模前20只基金缺少指定端点的复权净值。")
    nav["INNERCODE"] = pd.to_numeric(nav["INNERCODE"], errors="raise").astype(int)
    nav["trading_day"] = pd.to_datetime(nav["trading_day"])
    nav["UNITNVRESTORED"] = pd.to_numeric(nav["UNITNVRESTORED"], errors="coerce")
    return nav.sort_values(["INNERCODE", "trading_day"])


def rank_returns(funds: pd.DataFrame, nav: pd.DataFrame) -> pd.DataFrame:
    endpoints = nav.pivot(index="INNERCODE", columns="trading_day", values="UNITNVRESTORED")
    endpoints = endpoints.reindex(index=funds["INNERCODE"],
                                  columns=pd.to_datetime([START_DATE, AS_OF_DATE]))
    if not (endpoints.notna() & endpoints.gt(0) & endpoints.lt(float("inf"))).all().all():
        # Do not silently fill endpoints or replace a Top20 fund with rank 21.
        raise ValueError("规模前20只基金存在无效端点；不可补值或从规模排名后续基金补位。")
    income = (endpoints.iloc[:, 1] / endpoints.iloc[:, 0] - 1).rename("interval_return")
    ranked = funds.merge(income, left_on="INNERCODE", right_index=True, validate="one_to_one")
    return ranked.sort_values(["interval_return", "SECUCODE"],
                              ascending=[False, True]).head(TOP_RETURN_N)


def build_result() -> pd.DataFrame:
    funds = fetch_top_scale_funds()
    ranked = rank_returns(funds, fetch_nav(funds["INNERCODE"].tolist()))

    output = pd.DataFrame(
        {
            "主份额代码": ranked["SECUCODE"],
            "基金简称": ranked["fund_name"],
            "规模截止日期": ranked["scale_enddate"].dt.strftime("%Y-%m-%d"),
            "合并规模": (ranked["COMBNVI"] / 100_000_000).map(fmt2),
            "区间收益率": (ranked["interval_return"] * 100).map(fmt2),
            "观察日申购状态": ranked["applying_type"].fillna(""),
        }
    ).reset_index(drop=True)
    return output


def main() -> pd.DataFrame:
    output = build_result()
    output.to_csv(OUTPUT_DIR / "result.csv", index=False, encoding="utf-8-sig")
    return output


if __name__ == "__main__":
    print(main().to_string(index=False))
