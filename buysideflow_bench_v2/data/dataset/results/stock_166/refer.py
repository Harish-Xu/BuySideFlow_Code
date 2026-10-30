"""Five-session closing-limit-up concept leaders for the canonical stock_166 task."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sweagent.text2sql import db_connector


def canonical_as_of_date() -> str:
    # Replay copies references but imports the project's connector.
    # Read the canonical input instead of embedding another stale date.
    root = Path(db_connector.__file__).resolve().parents[2]
    with (root / "data/dataset/question/dataset.jsonl").open(encoding="utf-8") as f:
        for line in f:
            task = json.loads(line)
            if task["instance_id"] == "stock_166":
                return date.fromisoformat(task["inputs"]["as_of_date"]).isoformat()
    raise ValueError("stock_166 is missing from the canonical dataset")


def render_sql(as_of_date: str) -> str:
    anchor = date.fromisoformat(as_of_date)
    return (Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
            .replace("{{as_of_date}}", anchor.isoformat())
            .replace("{{cutoff}}", (anchor + timedelta(days=1)).isoformat()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    args = parser.parse_args()
    sql = render_sql(args.as_of_date or canonical_as_of_date())
    result = db_connector.query_to_dataframe(sql, max_rows=0, timeout=600)
    if result is None:
        raise RuntimeError("Reference query returned no DataFrame")
    # Empty results retain the SQL's five-column contract.
    result["5日收盘涨停次数"] = result["5日收盘涨停次数"].astype("int64")
    result.to_csv(args.output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")


if __name__ == "__main__":
    main()
