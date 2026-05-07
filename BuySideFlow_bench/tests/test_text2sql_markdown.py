from pathlib import Path

from sweagent.text2sql.markdown import parse_text2sql_tasks


def test_parse_benchmark_markdown_extracts_reference_results(tmp_path: Path):
    benchmark_path = tmp_path / "benchmark.md"
    benchmark_path.write_text(
        """### SQLPy-01
**题面**给定观察日参数 as_of_date，输出 ETF 权重。

**ETF池**
`('518880', '159985')`

**标准评测观察日**：`2025-12-31`

**输出**
- `结果字段`：`signal_date`、`etf_code`、`epo_weight`。
- `稳定输出约束`：按 `etf_code` 升序输出。

**参考实现**
[sqlpy_01_demo.py]

真实输出：

| signal_date | etf_code | epo_weight |
|---|---|---:|
| 2025-12-31 | 159985 | 0.4 |
| 2025-12-31 | 518880 | 0.6 |

### WF-06
**题面**
只给题面，没有参考答案表。

**输出**
- `结果字段`：`as_of_date`、`stock_code`。

**实现**
[wf_06_demo.py]
""",
        encoding="utf-8",
    )

    tasks = parse_text2sql_tasks(benchmark_path)

    assert len(tasks) == 2

    first = tasks[0]
    assert first["id"] == "q1"
    assert first["benchmark_title"] == "SQLPy-01"
    assert first["canonical_question"] == "给定观察日参数 as_of_date，输出 ETF 权重。"
    assert "补充要求" in first["question"]
    assert "ETF池" in first["question"]
    assert "输出要求" in first["question"]
    assert first["output_contract"] == "- `结果字段`：`signal_date`、`etf_code`、`epo_weight`。"
    assert first["evaluation_contract"] == "- `稳定输出约束`：按 `etf_code` 升序输出。"
    assert first["reference_results"] == [[
        {"signal_date": "2025-12-31", "etf_code": "159985", "epo_weight": 0.4},
        {"signal_date": "2025-12-31", "etf_code": "518880", "epo_weight": 0.6},
    ]]
    assert first["reference_links"] == ["sqlpy_01_demo.py"]

    second = tasks[1]
    assert second["id"] == "q2"
    assert second["reference_results"] == []
    assert second["reference_links"] == ["wf_06_demo.py"]


def test_parse_benchmark_markdown_accepts_plain_output_heading(tmp_path: Path):
    benchmark_path = tmp_path / "benchmark.md"
    benchmark_path.write_text(
        """### NL2SQL-02
**题面**观察日参数 as_of_date，按约束输出股票列表。

输出：
- `结果字段`：`as_of_date`、`stock_code`、`stock_name`。
- `稳定输出约束`：按 `stock_code` 升序输出。

**参考 SQL**
[nl2sql_02_demo.sql]

真实输出：

| as_of_date | stock_code | stock_name |
|---|---|---|
| 2025-05-31 | 000001 | Alpha |
""",
        encoding="utf-8",
    )

    tasks = parse_text2sql_tasks(benchmark_path)

    assert len(tasks) == 1
    first = tasks[0]
    assert first["output_contract"] == "- `结果字段`：`as_of_date`、`stock_code`、`stock_name`。"
    assert first["evaluation_contract"] == "- `稳定输出约束`：按 `stock_code` 升序输出。"
    assert "输出要求" in first["question"]
