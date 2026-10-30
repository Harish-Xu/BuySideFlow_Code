#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_connection, query_to_dataframe


AS_OF_DATE = "2024-12-31"
# Verified from bond_cbvaluationall recommended-valuation dates: the latest 60
# valuation trading days not later than 2024-12-31 span 2024-10-10 to 2024-12-31.
VALUATION_START_DATE = "2024-10-10"
VALUATION_END_DATE = AS_OF_DATE
WEAK_THRESHOLD_DAYS = 30

RESULT_COLUMNS = ["存续只数", "估值覆盖偏弱只数", "估值覆盖偏弱占比(%)"]


def norm_int(value) -> str:
    return str(int(value))


def load_candidates(base_dir: Path) -> pd.DataFrame:
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=180)
    if df is None:
        raise RuntimeError("candidate SQL returned None")
    if df.empty:
        return df
    df = df.copy()
    df["maincode_key"] = df["maincode"].map(norm_int)
    df["innercode_keys"] = df["innercodes"].fillna("").map(
        lambda value: [item for item in str(value).split(",") if item]
    )
    return df


def load_effective_valuation_counts(candidates: pd.DataFrame) -> pd.Series:
    if candidates.empty:
        return pd.Series(dtype=int)

    rows: list[tuple[int, int]] = []
    for row in candidates[["maincode_key", "innercode_keys"]].itertuples(index=False):
        for innercode in row.innercode_keys:
            rows.append((int(innercode), int(row.maincode_key)))

    if not rows:
        return pd.Series(dtype=int)

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION sql_mode=''")
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=300000")
            cursor.execute(
                """
                CREATE TEMPORARY TABLE tmp_bond035_inner_map (
                    innercode BIGINT NOT NULL PRIMARY KEY,
                    maincode BIGINT NOT NULL,
                    KEY idx_maincode (maincode)
                ) ENGINE=MEMORY
                """
            )
            cursor.executemany(
                "INSERT INTO tmp_bond035_inner_map (innercode, maincode) VALUES (%s, %s)",
                rows,
            )
            cursor.execute(
                f"""
                SELECT
                    m.maincode,
                    COUNT(DISTINCT DATE(v.ENDDATE)) AS effective_valuation_days
                FROM tmp_bond035_inner_map m
                STRAIGHT_JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
                  ON v.INNERCODE = m.innercode
                 AND v.ENDDATE >= DATE('{VALUATION_START_DATE}')
                 AND v.ENDDATE < DATE_ADD(DATE('{VALUATION_END_DATE}'), INTERVAL 1 DAY)
                 AND v.CREDIBILITYCODE = 1
                 AND v.VPYIELD IS NOT NULL
                 AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(DATE(v.ENDDATE), INTERVAL 1 DAY))
                GROUP BY m.maincode
                """
            )
            count_rows = cursor.fetchall()
    finally:
        connection.close()

    if not count_rows:
        return pd.Series(dtype=int)
    counts = pd.DataFrame(count_rows)
    counts["maincode_key"] = counts["maincode"].map(norm_int)
    return counts.set_index("maincode_key")["effective_valuation_days"].astype(int)


def build_result(candidates: pd.DataFrame, counts: pd.Series) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    panel = candidates.copy()
    panel["effective_valuation_days"] = panel["maincode_key"].map(counts).fillna(0).astype(int)
    panel["weak_flag"] = (panel["effective_valuation_days"] < WEAK_THRESHOLD_DAYS).astype(int)

    result = pd.DataFrame(
        {
            "存续只数": [int(len(panel))],
            "估值覆盖偏弱只数": [int(panel["weak_flag"].sum())],
        }
    )
    result["估值覆盖偏弱占比(%)"] = (
        result["估值覆盖偏弱只数"] / result["存续只数"] * 100
    ).round(2)
    result["存续只数"] = result["存续只数"].astype(int)
    result["估值覆盖偏弱只数"] = result["估值覆盖偏弱只数"].astype(int)
    return result[RESULT_COLUMNS].reset_index(drop=True)


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    candidates = load_candidates(base_dir)
    counts = load_effective_valuation_counts(candidates)
    result = build_result(candidates, counts)
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    detail_path = base_dir / "result_detail.csv"
    if detail_path.exists():
        detail_path.unlink()
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
