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
    "债项评级",
    "标准无保留债券只数",
    "标准无保留发行人家数",
    "标准无保留估值收益率中位数(%)",
    "非标债券只数",
    "非标发行人家数",
    "非标估值收益率中位数(%)",
    "收益率差(百分点)",
]

RATING_LABELS = {
    1000: "AAA+",
    999: "AAA",
    990: "AAA-",
    980: "AA+",
    970: "AA",
    960: "AA-",
    950: "A+",
    940: "A",
    930: "A-",
    899: "BBB+",
    895: "BBB",
    891: "BBB-",
    880: "BB+",
    870: "BB",
    860: "BB-",
    850: "B+",
    840: "B",
    830: "B-",
    799: "CCC",
    770: "CC",
    760: "C",
    740: "D",
    -1: "未评级",
}


def normalize_rating(code: object, raw_desc: object) -> str:
    if pd.isna(code):
        return "未评级"
    int_code = int(code)
    if int_code in RATING_LABELS:
        return RATING_LABELS[int_code]
    if pd.isna(raw_desc) or str(raw_desc).strip() == "":
        return "未评级"
    return str(raw_desc).strip()


def format_float(value: object) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value):.2f}"


def build_result(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    data = raw.copy()
    data["rating_code"] = pd.to_numeric(data["rating_code"], errors="coerce").fillna(-1).astype(int)
    data["valuation_yield_pct"] = pd.to_numeric(data["valuation_yield_pct"], errors="raise")
    data["债项评级"] = [
        normalize_rating(code, desc)
        for code, desc in zip(data["rating_code"], data["raw_rating_desc"], strict=True)
    ]

    grouped = (
        data.groupby(["rating_code", "债项评级", "audit_group"], as_index=False)
        .agg(
            bond_cnt=("MAINCODE", "nunique"),
            issuer_cnt=("COMPANYCODE", "nunique"),
            median_yield=("valuation_yield_pct", "median"),
        )
    )

    rows: list[dict[str, object]] = []
    for (rating_code, rating_label), rating_df in grouped.groupby(["rating_code", "债项评级"], sort=False):
        by_group = {row["audit_group"]: row for _, row in rating_df.iterrows()}
        standard = by_group.get("标准无保留")
        modified = by_group.get("非标")
        standard_yield = None if standard is None else float(standard["median_yield"])
        modified_yield = None if modified is None else float(modified["median_yield"])
        rows.append(
            {
                "rating_code": int(rating_code),
                "债项评级": rating_label,
                "标准无保留债券只数": 0 if standard is None else int(standard["bond_cnt"]),
                "标准无保留发行人家数": 0 if standard is None else int(standard["issuer_cnt"]),
                "标准无保留估值收益率中位数(%)": standard_yield,
                "非标债券只数": 0 if modified is None else int(modified["bond_cnt"]),
                "非标发行人家数": 0 if modified is None else int(modified["issuer_cnt"]),
                "非标估值收益率中位数(%)": modified_yield,
                "收益率差(百分点)": (
                    None if standard_yield is None or modified_yield is None else modified_yield - standard_yield
                ),
            }
        )

    result = pd.DataFrame(rows)
    result = result.sort_values(["rating_code", "债项评级"], ascending=[False, True]).reset_index(drop=True)
    for column in [
        "标准无保留估值收益率中位数(%)",
        "非标估值收益率中位数(%)",
        "收益率差(百分点)",
    ]:
        result[column] = result[column].map(format_float)
    return result[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw is None:
        raise RuntimeError("SQL returned None")
    result = build_result(raw)
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
