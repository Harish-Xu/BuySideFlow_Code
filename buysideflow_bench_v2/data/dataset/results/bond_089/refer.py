#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "发行人",
    "经营活动现金流净额",
    "总负债同比增速",
    "营业收入同比增速",
    "存续债余额",
]


def load_result() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=900)
    if df is None:
        raise ValueError("参考 SQL 未返回结果")
    return df


def validate_result(df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in RESULT_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"结果缺少列: {missing}")

    df = df[RESULT_COLUMNS].copy()
    if df.empty:
        raise ValueError("参考 SQL 未找到符合条件的发行人")

    if df["发行人"].isna().any() or (df["发行人"].astype(str).str.strip() == "").any():
        raise ValueError("发行人不能为空")

    numeric_cols = RESULT_COLUMNS[1:]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[numeric_cols].isna().any().any():
        raise ValueError("数值列存在空值或非数值")

    if not (df["存续债余额"] >= 200).all():
        raise ValueError("筛选结果中存在存续债余额低于 200 亿元主体")

    for col in numeric_cols:
        df[col] = df[col].round(2)

    return df.reset_index(drop=True)


def main() -> pd.DataFrame:
    return validate_result(load_result())


if __name__ == "__main__":
    result = main()
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(f"wrote {out_path} rows={len(result)}")
