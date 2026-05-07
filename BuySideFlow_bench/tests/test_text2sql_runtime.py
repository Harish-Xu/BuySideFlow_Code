import json
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from sweagent.text2sql.runtime import (
    _extract_sql_references,
    _validate_sql_references,
    build_run_code_observation,
    parse_get_trading_days_action,
    reset_run_code_count,
)
from sweagent.text2sql.schema import (
    render_focused_schema_catalog,
    render_schema_catalog,
    render_selected_schema,
)


class TestExtractSqlReferences:
    """Tests for _extract_sql_references."""

    def test_simple_select(self):
        tables, columns = _extract_sql_references("SELECT SECUCODE FROM secumain")
        assert tables == {"secumain"}
        assert columns == {"SECUCODE"}

    def test_join_with_alias(self):
        sql = (
            "SELECT a.fund_code FROM mf_fundarchives AS a "
            "JOIN secumain ON a.code = secumain.code"
        )
        tables, columns = _extract_sql_references(sql)
        assert tables == {"mf_fundarchives", "secumain"}
        assert "fund_code" in columns
        assert "code" in columns
        assert "a" not in columns  # alias should be filtered

    def test_where_and_group_by(self):
        sql = "SELECT col1, COUNT(*) FROM table1 WHERE col2 > 1 GROUP BY col1"
        tables, columns = _extract_sql_references(sql)
        assert tables == {"table1"}
        assert "col1" in columns
        assert "col2" in columns
        assert "COUNT" not in columns  # SQL keyword/function

    def test_empty_sql(self):
        tables, columns = _extract_sql_references("")
        assert tables == set()
        assert columns == set()

    def test_star_select(self):
        tables, columns = _extract_sql_references("SELECT * FROM t1")
        assert tables == {"t1"}
        assert columns == set()  # * should not be collected

    def test_cte_names_and_relation_aliases_are_ignored(self):
        sql = """
        WITH etf_pool AS (
            SELECT INNERCODE FROM secumain
        ),
        daily_prices AS (
            SELECT etf_pool.INNERCODE, q.TRADINGDAY
            FROM etf_pool
            JOIN qt_dailyquote AS q ON q.INNERCODE = etf_pool.INNERCODE
        )
        SELECT dp.INNERCODE, dp.TRADINGDAY
        FROM daily_prices AS dp
        """
        tables, columns = _extract_sql_references(sql)
        assert tables == {"secumain", "qt_dailyquote"}
        assert "INNERCODE" in columns
        assert "TRADINGDAY" in columns
        assert "etf_pool" not in tables
        assert "daily_prices" not in tables
        assert "etf_pool" not in columns
        assert "daily_prices" not in columns
        assert "dp" not in columns
        assert "q" not in columns

    def test_cte_base_tables_and_outer_tables_are_all_collected(self):
        sql = """
        WITH latest_pool AS (
            SELECT INNERCODE FROM secumain
        )
        SELECT lp.INNERCODE, q.TRADINGDAY
        FROM latest_pool AS lp
        JOIN qt_dailyquote AS q ON q.INNERCODE = lp.INNERCODE
        """
        tables, columns = _extract_sql_references(sql)
        assert tables == {"secumain", "qt_dailyquote"}
        assert "INNERCODE" in columns
        assert "TRADINGDAY" in columns
        assert "latest_pool" not in tables
        assert "lp" not in columns
        assert "q" not in columns


class TestValidateSqlReferences:
    """Tests for _validate_sql_references."""

    @pytest.fixture
    def fake_schema_path(self):
        payload = {
            "tables": [
                {
                    "table_name": "secumain",
                    "columns": [
                        {"column_name": "INNERCODE"},
                        {"column_name": "SECUCODE"},
                        {"column_name": "SECUABBR"},
                    ],
                },
                {
                    "table_name": "mf_fundarchives",
                    "columns": [
                        {"column_name": "FUNDCODE"},
                        {"column_name": "FUNDNAME"},
                    ],
                },
                {
                    "table_name": "qt_dailyquote",
                    "columns": [
                        {"column_name": "INNERCODE"},
                        {"column_name": "TRADINGDAY"},
                    ],
                },
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f)
            path = f.name
        yield Path(path)
        Path(path).unlink(missing_ok=True)

    def test_valid_sql_returns_empty(self, fake_schema_path):
        warnings = _validate_sql_references(
            "SELECT SECUCODE FROM secumain", fake_schema_path
        )
        assert warnings == []

    def test_unknown_table(self, fake_schema_path):
        warnings = _validate_sql_references(
            "SELECT * FROM unknown_table", fake_schema_path
        )
        assert any("unknown_table" in w for w in warnings)

    def test_unknown_column(self, fake_schema_path):
        warnings = _validate_sql_references(
            "SELECT fake_col FROM secumain", fake_schema_path
        )
        assert any("fake_col" in w for w in warnings)

    def test_multiple_issues(self, fake_schema_path):
        warnings = _validate_sql_references(
            "SELECT fake_col FROM fake_table", fake_schema_path
        )
        assert len(warnings) == 2
        assert any("fake_table" in w for w in warnings)
        assert any("fake_col" in w for w in warnings)

    def test_empty_sql_returns_empty(self, fake_schema_path):
        warnings = _validate_sql_references("", fake_schema_path)
        assert warnings == []

    def test_valid_cte_sql_returns_empty(self, fake_schema_path):
        sql = """
        WITH etf_pool AS (
            SELECT INNERCODE FROM secumain
        ),
        daily_prices AS (
            SELECT etf_pool.INNERCODE, q.TRADINGDAY
            FROM etf_pool
            JOIN qt_dailyquote AS q ON q.INNERCODE = etf_pool.INNERCODE
        )
        SELECT dp.INNERCODE, dp.TRADINGDAY
        FROM daily_prices AS dp
        """
        warnings = _validate_sql_references(sql, fake_schema_path)
        assert warnings == []


class TestGetTradingDaysParsing:
    def test_parse_get_trading_days_accepts_keyword_markets(self):
        assert parse_get_trading_days_action(
            "get_trading_days 2024-01-01 2024-12-31 market=83"
        ) == ("2024-01-01", "2024-12-31", "83")


class TestRunCodePayloadParsing:
    def test_build_run_code_observation_accepts_json_string_sql(self, monkeypatch):
        calls = {}

        def fake_execute_code(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(
                success=True,
                raw_results=[],
                result_types=[],
                error=None,
                stdout_text="ok",
            )

        monkeypatch.setattr("sweagent.text2sql.evaluator.execute_code", fake_execute_code)
        problem_statement = SimpleNamespace(id="json_string_sql", extra_fields={})

        reset_run_code_count("json_string_sql")
        observation = build_run_code_observation(problem_statement, json.dumps("SELECT 1"))

        assert calls["mode"] == "sql"
        assert calls["sql_code"] == "SELECT 1"
        assert calls["python_code"] == ""
        assert calls["result_vars"] == []
        assert calls["force_subprocess"] is True
        assert "执行成功，捕获到打印输出" in observation

    def test_build_run_code_observation_includes_stdout_with_result_objects(self, monkeypatch):
        def fake_execute_code(**kwargs):
            return SimpleNamespace(
                success=True,
                raw_results=["result object"],
                result_types=["other"],
                error=None,
                stdout_text="full printed detail",
            )

        monkeypatch.setattr("sweagent.text2sql.evaluator.execute_code", fake_execute_code)
        problem_statement = SimpleNamespace(id="stdout_with_result", extra_fields={})

        reset_run_code_count("stdout_with_result")
        observation = build_run_code_observation(
            problem_statement,
            '{"mode":"python","sql_code":null,"python_code":"print(1)","result_vars":["x"]}',
        )

        assert "结果 1: result object" in observation
        assert "执行成功，捕获到打印输出" in observation
        assert "full printed detail" in observation

    def test_parse_get_trading_days_accepts_positional_markets(self):
        assert parse_get_trading_days_action(
            "get_trading_days 2024-01-01 2024-12-31 83"
        ) == ("2024-01-01", "2024-12-31", "83")


class TestSchemaTruncationHints:
    """Tests for truncation hints in schema rendering functions."""

    @pytest.fixture
    def schema_path(self):
        payload = {
            "tables": [
                {
                    "table_name": "big_table",
                    "table_comment": "A table with many columns",
                    "columns": [{"column_name": f"col_{i}", "comment": f"comment {i}"} for i in range(100)],
                },
                {
                    "table_name": "small_table",
                    "table_comment": "A small table",
                    "columns": [{"column_name": "id", "comment": "ID"}],
                },
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f)
            path = f.name
        yield Path(path)
        Path(path).unlink(missing_ok=True)

    def test_render_schema_catalog_truncation(self, schema_path):
        text = render_schema_catalog(schema_path, max_columns_per_table=10)
        assert "[注意]" in text
        assert "共有 100 列" in text
        assert "另有 90 列被省略" in text
        assert "request_schema big_table" in text

    def test_render_focused_schema_catalog_truncation(self, schema_path):
        text = render_focused_schema_catalog(
            schema_path, tables=["big_table"], max_columns_per_table=10
        )
        assert "[注意]" in text
        assert "共有 100 列" in text

    def test_render_selected_schema_truncation(self, schema_path):
        rendered, matched, missing = render_selected_schema(
            schema_path, requested_tables=["big_table"], max_columns_per_table=10
        )
        assert matched == ["big_table"]
        assert "[注意]" in rendered
        assert "共有 100 列" in rendered
        assert "另有 90 列被省略" in rendered
        assert "truncated_columns" in rendered  # JSON still contains the key

    def test_no_truncation_no_hint(self, schema_path):
        text = render_schema_catalog(schema_path, max_columns_per_table=200)
        assert "[注意]" not in text
