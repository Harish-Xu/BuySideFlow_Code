#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
import pymysql
from pymysql.cursors import DictCursor


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import (  # noqa: E402
    _convert_value,
    _normalize_sql_for_mysql,
    _patch_sql_aliases,
)


BUY_DATE = "2025-06-19"
SELL_DATE = "2025-06-27"
COUPON_DATE = "2025-06-20"

OUTPUT_COLUMNS = [
    "债券代码",
    "债券简称",
    "买入全价",
    "卖出全价",
    "期间票息",
    "计票息持有期收益率",
    "不计票息持有期收益率",
    "票息贡献",
]

_CONNECTION = None


def query_to_dataframe(sql: str, max_rows: int = 0, timeout: int = 120) -> pd.DataFrame:
    global _CONNECTION
    normalized_sql = _patch_sql_aliases(_normalize_sql_for_mysql(sql))

    for query_attempt in range(2):
        if _CONNECTION is None or not getattr(_CONNECTION, "open", False):
            for connect_attempt in range(3):
                try:
                    _CONNECTION = pymysql.connect(
                        host=os.getenv("DB_HOST", "localhost"),
                        port=int(os.getenv("DB_PORT", "3306")),
                        user=os.getenv("DB_USER", "root"),
                        password=os.getenv("DB_PASSWORD", ""),
                        database=os.getenv("DB_NAME", "jydb"),
                        charset="utf8mb4",
                        cursorclass=DictCursor,
                        autocommit=True,
                        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT_SEC", "5")),
                        read_timeout=timeout,
                        write_timeout=timeout,
                    )
                    break
                except Exception:
                    if connect_attempt == 2:
                        raise
                    time.sleep(1.5 * (connect_attempt + 1))
        try:
            with _CONNECTION.cursor() as cursor:
                cursor.execute(normalized_sql)
                rows = cursor.fetchall() if max_rows == 0 else cursor.fetchmany(max_rows)
            return pd.DataFrame([{key: _convert_value(value) for key, value in row.items()} for row in rows])
        except pymysql.MySQLError:
            try:
                _CONNECTION.close()
            except Exception:
                pass
            _CONNECTION = None
            if query_attempt == 1:
                raise
            time.sleep(1.5)

    return pd.DataFrame()


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def build_result() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=120)
    if raw.empty:
        return empty_result()

    if len(raw) != 1:
        raise ValueError(f"expected exactly one bond row, got {len(raw)}")

    row = raw.iloc[0]
    buy_full = pd.to_numeric(row["buy_full_price"], errors="coerce")
    sell_full = pd.to_numeric(row["sell_full_price"], errors="coerce")
    coupon_rate = pd.to_numeric(row["coupon_rate_pct"], errors="coerce")
    pay_frequency = pd.to_numeric(row["pay_frequency"], errors="coerce")
    par_value = pd.to_numeric(row["par_value"], errors="coerce")

    if pd.isna(buy_full) or pd.isna(sell_full):
        raise ValueError("buy or sell full price is missing")
    if pd.isna(coupon_rate) or coupon_rate < 0:
        raise ValueError("coupon rate is missing")
    if pd.isna(pay_frequency) or pay_frequency <= 0:
        raise ValueError("pay frequency is missing")
    if pd.isna(par_value) or par_value <= 0:
        par_value = 100.0

    # Prices are per 100 face value. The coupon cash flow over one coupon period
    # is coupon_rate / frequency per 100 face value.
    period_coupon = float(par_value) * float(coupon_rate) / 100.0 / float(pay_frequency)
    return_with_coupon = (float(sell_full) + period_coupon - float(buy_full)) / float(buy_full) * 100.0
    return_without_coupon = (float(sell_full) - float(buy_full)) / float(buy_full) * 100.0
    coupon_contribution = return_with_coupon - return_without_coupon

    result = pd.DataFrame(
        [
            {
                "债券代码": str(row["bond_code"]),
                "债券简称": str(row["bond_name"]),
                "买入全价": float(buy_full),
                "卖出全价": float(sell_full),
                "期间票息": period_coupon,
                "计票息持有期收益率": return_with_coupon,
                "不计票息持有期收益率": return_without_coupon,
                "票息贡献": coupon_contribution,
            }
        ]
    )

    for col in ["买入全价", "卖出全价", "期间票息"]:
        result[col] = result[col].round(4)
    for col in ["计票息持有期收益率", "不计票息持有期收益率", "票息贡献"]:
        result[col] = result[col].round(4).map(lambda value: f"{value:.4f}%")

    return result[OUTPUT_COLUMNS]


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    write_result(build_result())
