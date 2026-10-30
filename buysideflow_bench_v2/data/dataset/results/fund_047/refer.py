#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
except Exception as exc:  # pragma: no cover - chart failure should be explicit at runtime
    raise RuntimeError(f"matplotlib is required to draw picture.png: {exc}") from exc

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

BASE_DIR = Path(__file__).resolve().parent
EXPECTED_COLUMNS = [
    "报告期",
    "本基金集中度(%)",
    "同类平均集中度(%)",
    "同类样本数",
]


def configure_plot_font() -> None:
    candidates = [
        "PingFang SC",
        "Hiragino Sans GB",
        "STHeiti",
        "Heiti SC",
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
    ]
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    plt.rcParams["axes.unicode_minus"] = False


def draw_chart(df: pd.DataFrame) -> None:
    configure_plot_font()
    plot_df = df.copy()
    plot_df["报告期"] = pd.to_datetime(plot_df["报告期"])

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(
        plot_df["报告期"],
        plot_df["本基金集中度(%)"],
        marker="o",
        linewidth=2.2,
        label="本基金集中度",
    )
    ax.plot(
        plot_df["报告期"],
        plot_df["同类平均集中度(%)"],
        marker="s",
        linewidth=2.0,
        linestyle="--",
        label="同类平均集中度",
    )
    ax.set_xlabel("报告期")
    ax.set_ylabel("前十大重仓股集中度(%)")
    ax.set_title("005739.OF 前十大重仓股集中度与同类均值")
    ax.grid(alpha=0.25, linestyle="--")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(BASE_DIR / "picture.png", dpi=150)
    plt.close(fig)


def main() -> pd.DataFrame:
    sql = (BASE_DIR / "refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("SQL returned no rows")

    missing = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")

    df = df[EXPECTED_COLUMNS].copy()
    df["报告期"] = pd.to_datetime(df["报告期"]).dt.strftime("%Y-%m-%d")
    df["同类样本数"] = df["同类样本数"].astype("Int64")
    df.to_csv(BASE_DIR / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    draw_chart(df)
    print(df.to_string(index=False))
    return df


if __name__ == "__main__":
    main()
