import json
import csv
from pathlib import Path
from types import SimpleNamespace

from sweagent.run.hooks import text2sql_evaluate
from sweagent.run.hooks.text2sql_evaluate import Text2SQLEvaluateHook, build_tool_use_record
from sweagent.text2sql.evaluator import CompareResult
from sweagent.types import AgentRunResult


def _problem_statement(instance_id: str, sql: str):
    return SimpleNamespace(
        id=instance_id,
        question=f"{instance_id} question",
        mode="sql",
        reference_sql=sql,
        reference_python="",
        result_vars=[],
        extra_fields={
            "evaluation_kind": "csv",
            "strict_output_schema": True,
        },
        difficulty="hard",
    )


def _trajectory_step(action: str) -> dict:
    return {
        "action": action,
        "observation": "",
        "response": "",
        "state": {},
        "thought": "",
        "execution_time": 0.0,
        "query": [],
        "extra_info": {},
    }


def test_text2sql_evaluate_uses_result_instance_id(tmp_path: Path, monkeypatch):
    hook = Text2SQLEvaluateHook(output_dir=tmp_path, model_name="test-model")
    stock_statement = _problem_statement("stock_102", "select 'stock'")
    fund_statement = _problem_statement("fund_042", "select 'fund'")
    captured = []

    def fake_compare_items(reference_item, generated_item):
        captured.append((reference_item["id"], reference_item["sql_code"], generated_item["id"]))
        compare_result = CompareResult(reference_item["id"])
        compare_result.passed = True
        compare_result.score = 1.0
        compare_result.max_score = 1.0
        return compare_result

    monkeypatch.setattr(text2sql_evaluate, "compare_items", fake_compare_items)

    hook.on_instance_start(index=0, env=None, problem_statement=stock_statement)
    hook.on_instance_start(index=1, env=None, problem_statement=fund_statement)
    hook.on_instance_completed(
        result=AgentRunResult(
            info={
                "instance_id": "stock_102",
                "submission": json.dumps({"mode": "sql", "sql_code": "select generated", "result_vars": []}),
                "exit_status": "submitted",
            },
            trajectory=[],
        )
    )

    assert captured == [("stock_102", "select 'stock'", "stock_102")]
    assert "stock_102" not in hook._problem_statement_by_instance_id
    assert "fund_042" in hook._problem_statement_by_instance_id


def test_text2sql_evaluate_counts_get_columns_and_exports_tool_use(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DB_SNAPSHOT_ID", "unit-test-snapshot")
    hook = Text2SQLEvaluateHook(output_dir=tmp_path, model_name="test-model")
    statement = _problem_statement("stock_102", "select 'stock'")

    def fake_compare_items(reference_item, generated_item):
        compare_result = CompareResult(reference_item["id"])
        compare_result.passed = False
        compare_result.error_type = "logic_mismatch"
        compare_result.error_msg = "mismatch"
        compare_result.score = 0.0
        compare_result.max_score = 1.0
        return compare_result

    monkeypatch.setattr(text2sql_evaluate, "compare_items", fake_compare_items)

    hook.on_instance_start(index=0, env=None, problem_statement=statement)
    hook.on_instance_completed(
        result=AgentRunResult(
            info={
                "instance_id": "stock_102",
                "submission": json.dumps({"mode": "sql", "sql_code": "select generated", "result_vars": []}),
                "exit_status": "submitted",
            },
            trajectory=[
                _trajectory_step("search_tables stock"),
                _trajectory_step("describe_tables secumain"),
                _trajectory_step("get_columns secumain"),
                _trajectory_step("get_columns qt_dailyquote"),
                _trajectory_step("search_columns market cap"),
                _trajectory_step("request_schema secumain"),
                _trajectory_step("run_code\n{\"mode\":\"sql\"}\nEND_RUN_CODE"),
                _trajectory_step("submit\n{}\nEND_SUBMIT"),
            ],
        )
    )
    hook.on_end()

    assert hook._per_instance_behavior[0]["get_columns_cnt"] == 2
    assert hook._per_instance_behavior[0]["schema_explore_cnt"] == 6
    assert hook._behavior_stats["navigation_trap_count"] == 1

    tool_use_paths = list(tmp_path.glob("tool_use_test-model_*.csv"))
    assert len(tool_use_paths) == 1
    with tool_use_paths[0].open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    assert rows[0]["qid"] == "stock_102"
    assert rows[0]["schema_explore_cnt"] == "6"
    assert rows[0]["run_code_cnt"] == "1"
    assert rows[0]["submit_cnt"] == "1"
    assert rows[0]["passed"] == "False"
    assert list(rows[0].keys()) == [
        "qid",
        "domain",
        "difficulty",
        "mode",
        "evaluation_kind",
        "passed",
        "score",
        "max_score",
        "total_turns",
        "schema_explore_cnt",
        "run_code_cnt",
        "submit_cnt",
    ]

    report_text = next(tmp_path.glob("benchmark_test-model_*.md")).read_text(encoding="utf-8")
    assert "per-task tool-use table" in report_text
    assert tool_use_paths[0].name in report_text


def test_text2sql_tool_use_counts_multiple_actions_in_one_step():
    record = build_tool_use_record(
        qid="stock_102",
        difficulty="hard",
        trajectory=[
            _trajectory_step(
                "search_tables stock\n\n"
                "<<<SWE_AGENT_ACTION_SEPARATOR>>>\n\n"
                "get_columns secumain"
            ),
            _trajectory_step(
                "run_code\n{}\nEND_RUN_CODE\n\n"
                "<<<SWE_AGENT_ACTION_SEPARATOR>>>\n\n"
                "submit\n{}\nEND_SUBMIT"
            ),
        ],
    )

    assert record["search_tables_cnt"] == 1
    assert record["get_columns_cnt"] == 1
    assert record["schema_explore_cnt"] == 2
    assert record["run_code_cnt"] == 1
    assert record["submit_cnt"] == 1
    assert record["total_turns"] == 2
