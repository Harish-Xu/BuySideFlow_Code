import sys
import types

import pandas as pd
import pytest

try:
    import pymysql
except ModuleNotFoundError:
    pymysql = types.SimpleNamespace(
        MySQLError=Exception,
        connect=lambda **kwargs: None,
        cursors=types.SimpleNamespace(DictCursor=object),
        err=types.SimpleNamespace(
            OperationalError=type("OperationalError", (Exception,), {}),
        ),
    )
    sys.modules["pymysql"] = pymysql
    sys.modules["pymysql.cursors"] = pymysql.cursors

import sweagent.text2sql.evaluator as evaluator_module
from sweagent.text2sql.evaluator import (
    ExecuteResult,
    _evaluator_litellm_completion,
    _compare_result_lists,
    _df_values_match,
    _extract_compare_decimals,
    _flat_values,
    _flat_values_match,
    _get_result_plan,
    _merge_results_for_comparison,
    _normalize_df,
    _serialize_for_text_judge,
    _should_force_ai_judge,
    _to_df,
    compare_items,
    execute_code,
    format_result_for_report,
)


def _wrap_results(values, *, sort_rows=True):
    result = ExecuteResult()
    result.success = True
    for value in values:
        result.raw_results.append(value.copy() if isinstance(value, pd.DataFrame) else value)
        if isinstance(value, pd.DataFrame):
            result.results.append(_normalize_df(value, sort_rows=sort_rows))
            result.result_types.append("dataframe")
        else:
            result.results.append(value)
            result.result_types.append("other")
    return result


def test_execute_code_keeps_stdout_but_prefers_requested_var():
    code = """
import pandas as pd
result_df = pd.DataFrame({"x": [1]})
print("noise")
"""
    result = execute_code(
        mode="python",
        python_code=code,
        result_vars=["result_df"],
        label="test",
    )
    assert result.success is True
    assert result.stdout_text == "noise"
    assert len(result.raw_results) == 1
    assert isinstance(result.raw_results[0], pd.DataFrame)


def test_execute_code_keeps_figure_even_with_result_vars(monkeypatch):
    monkeypatch.setattr(
        "sweagent.text2sql.evaluator._capture_figure",
        lambda label: b"fakepng",
    )
    code = """
import pandas as pd
result_df = pd.DataFrame({"x": [1]})
"""
    result = execute_code(
        mode="python",
        python_code=code,
        result_vars=["result_df"],
        label="test",
    )
    assert result.success is True
    assert len(result.raw_results) == 2
    assert result.result_types == ["dataframe", "image"]


def test_execute_code_missing_var_falls_back_to_structured_print():
    code = """
import pandas as pd
df = pd.DataFrame({"x": [1]})
print("结果如下")
print(df)
"""
    result = execute_code(
        mode="python",
        python_code=code,
        result_vars=["missing"],
        label="test",
    )
    assert result.success is True
    assert "结果如下" in result.stdout_text
    assert len(result.raw_results) == 1
    assert isinstance(result.raw_results[0], pd.DataFrame)


def test_execute_code_missing_var_falls_back_to_stdout_text():
    result = execute_code(
        mode="python",
        python_code='print("年化收益率为 12.3%")',
        result_vars=["missing"],
        label="test",
    )
    assert result.success is True
    assert result.raw_results == ["年化收益率为 12.3%"]


def test_execute_code_unfolds_eval_results():
    code = """
import pandas as pd
df1 = pd.DataFrame({"a": [1]})
df2 = pd.DataFrame({"a": [2]})
_eval_results = [df1, df2]
"""
    result = execute_code(
        mode="python",
        python_code=code,
        result_vars=["_eval_results"],
        label="test",
    )
    assert result.success is True
    assert len(result.raw_results) == 2


def test_execute_code_reuses_db_connection_within_single_python_run(monkeypatch):
    import sweagent.text2sql.db_connector as db_module

    created_connections = []

    class FakeCursor:
        def __init__(self):
            self._rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("SET SESSION"):
                self._rows = []
                return
            self._rows = [{"value": 1}]

        def fetchall(self):
            return list(self._rows)

        def fetchmany(self, max_rows):
            return list(self._rows[:max_rows])

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def cursor(self, *args, **kwargs):
            return FakeCursor()

        def ping(self, reconnect=False):
            if self.closed:
                raise RuntimeError("connection already closed")

        def close(self):
            self.closed = True

    def fake_connect(**kwargs):
        conn = FakeConnection()
        created_connections.append(conn)
        return conn

    monkeypatch.setattr(db_module, "_throttle_connection_attempt", lambda: None)
    monkeypatch.setattr(db_module.pymysql, "connect", fake_connect)

    code = """
from db_connector import query_to_dataframe

df1 = query_to_dataframe("SELECT 1 AS value")
result_df = query_to_dataframe("SELECT 2 AS value")
"""
    result = execute_code(
        mode="python",
        python_code=code,
        result_vars=["result_df"],
        label="test_shared_conn",
    )

    assert result.success is True
    assert len(created_connections) == 1
    assert created_connections[0].closed is True


def test_execute_code_shared_db_connection_does_not_leak_across_runs(monkeypatch):
    import sweagent.text2sql.db_connector as db_module

    created_connections = []

    class FakeCursor:
        def __init__(self):
            self._rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("SET SESSION"):
                self._rows = []
                return
            self._rows = [{"value": 1}]

        def fetchall(self):
            return list(self._rows)

        def fetchmany(self, max_rows):
            return list(self._rows[:max_rows])

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def cursor(self, *args, **kwargs):
            return FakeCursor()

        def ping(self, reconnect=False):
            if self.closed:
                raise RuntimeError("connection already closed")

        def close(self):
            self.closed = True

    def fake_connect(**kwargs):
        conn = FakeConnection()
        created_connections.append(conn)
        return conn

    monkeypatch.setattr(db_module, "_throttle_connection_attempt", lambda: None)
    monkeypatch.setattr(db_module.pymysql, "connect", fake_connect)

    code = """
from db_connector import query_to_dataframe
result_df = query_to_dataframe("SELECT 1 AS value")
"""
    first = execute_code(
        mode="python",
        python_code=code,
        result_vars=["result_df"],
        label="test_shared_conn_first",
    )
    second = execute_code(
        mode="python",
        python_code=code,
        result_vars=["result_df"],
        label="test_shared_conn_second",
    )

    assert first.success is True
    assert second.success is True
    assert len(created_connections) == 2
    assert all(conn.closed for conn in created_connections)


def test_execute_code_reuses_db_connection_for_db_connection_contexts(monkeypatch):
    import sweagent.text2sql.db_connector as db_module

    created_connections = []

    class FakeCursor:
        def __init__(self):
            self._rows = []
            self.description = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("SET SESSION"):
                self.description = None
                self._rows = []
                return
            self.description = (("value",),)
            self._rows = [{"value": 1}]

        def fetchall(self):
            return list(self._rows)

        def fetchmany(self, max_rows):
            return list(self._rows[:max_rows])

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def cursor(self, *args, **kwargs):
            return FakeCursor()

        def ping(self, reconnect=False):
            if self.closed:
                raise RuntimeError("connection already closed")

        def close(self):
            self.closed = True

    def fake_connect(**kwargs):
        conn = FakeConnection()
        created_connections.append(conn)
        return conn

    monkeypatch.setattr(db_module, "_throttle_connection_attempt", lambda: None)
    monkeypatch.setattr(db_module.pymysql, "connect", fake_connect)

    code = """
from db_connector import get_db_connector

DB = get_db_connector()
with DB.connection() as conn:
    df1 = DB.execute_sql_to_dataframe_on_connection(conn, "SELECT 1 AS value")
with DB.connection() as conn:
    result_df = DB.execute_sql_to_dataframe_on_connection(conn, "SELECT 2 AS value")
"""
    result = execute_code(
        mode="python",
        python_code=code,
        result_vars=["result_df"],
        label="test_db_connection_ctx",
    )

    assert result.success is True
    assert len(created_connections) == 1
    assert created_connections[0].closed is True


def test_local_inject_plan_uses_explicit_result_vars():
    code = """
import json

def build():
    return {"a": 1}

def main():
    print(json.dumps(build(), ensure_ascii=False))
"""
    plan = _get_result_plan("test", code, expected_count=2)
    assert plan["result_vars"] == ["build_result"]
    assert "build_result =" in plan["inject_code"]
    assert plan["needs_ai_judge"] is False


def test_local_plan_marks_text_question_for_ai_judge():
    code = """
def main():
    print("这是文字分析结论")
"""
    plan = _get_result_plan("请描述原因并分析", code, expected_count=1)
    assert plan["needs_ai_judge"] is True


def test_to_df_is_conservative_for_nested_dict():
    assert _to_df({"a": {"b": 1}}) is None


def test_to_df_converts_list_of_dicts():
    df = _to_df([{"a": 1}, {"a": 2}])
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2


def test_flat_values_recursive_match_for_nested_vs_flat():
    ref_flat = _flat_values([{"summary": {"x": 1}}, pd.DataFrame({"y": [2]})])
    gen_flat = _flat_values([pd.DataFrame({"a": [1, 2]})])
    assert ref_flat is not None
    assert gen_flat is not None
    assert _flat_values_match(ref_flat, gen_flat) is True


def test_extract_compare_decimals_prefers_question_precision():
    assert _extract_compare_decimals("百分比保留2位小数") == 2
    assert _extract_compare_decimals("结果保留 6 位小数") == 6
    assert _extract_compare_decimals("没有明确说明") == 4


def test_compare_result_lists_uses_default_four_decimals():
    ref = _wrap_results([pd.DataFrame({"a": [1.23444]})])
    gen = _wrap_results([pd.DataFrame({"a": [1.23445]})])
    passed, err = _compare_result_lists(ref, gen, question="普通数值题")
    assert passed is True
    assert err is None


def test_compare_result_lists_respects_question_decimals():
    ref_df = _normalize_df(pd.DataFrame({"a": [1.2344]}), decimals=4)
    gen_df = _normalize_df(pd.DataFrame({"a": [1.2345]}), decimals=4)
    assert _df_values_match(ref_df, gen_df, decimals=4) is False


def test_df_values_match_accepts_percent_scale_with_shifted_precision():
    ref_df = pd.DataFrame({"value": [-22.45, 35.91, 36.5, 105.45, 110.7]})
    gen_df = pd.DataFrame({"value": [-0.22, 0.36, 0.37, 1.05, 1.11]})
    assert _df_values_match(ref_df, gen_df, decimals=4) is True


def test_df_values_match_rejects_percent_scale_value_mismatch():
    ref_df = pd.DataFrame({"value": [35.91]})
    gen_df = pd.DataFrame({"value": [0.37]})
    assert _df_values_match(ref_df, gen_df, decimals=4) is False


def test_df_values_match_rejects_percent_scale_mismatch_with_question_precision():
    ref_df = pd.DataFrame({"value": [38.01]})
    gen_df = pd.DataFrame({"value": [0.39]})
    assert _df_values_match(ref_df, gen_df, decimals=2) is False


def test_df_values_match_uses_decimal_half_up_for_percent_scale():
    ref_df = pd.DataFrame({"value": [105.5, -27.5]})
    gen_df = pd.DataFrame({"value": [1.06, -0.28]})
    assert _df_values_match(ref_df, gen_df, decimals=2) is True

    wrong_df = pd.DataFrame({"value": [1.05, -0.27]})
    assert _df_values_match(ref_df, wrong_df, decimals=2) is False


def test_df_values_match_does_not_loosen_non_percent_scales():
    ref_df = pd.DataFrame({"value": [123456789.0]})
    gen_df = pd.DataFrame({"value": [1.23]})
    assert _df_values_match(ref_df, gen_df, decimals=4) is False


def test_flat_values_match_accepts_percent_scale_with_shifted_precision():
    ref_flat = _flat_values([pd.DataFrame({"value": [-22.45, 35.91, 36.5, 105.45, 110.7]})])
    gen_flat = _flat_values([pd.DataFrame({"value": [-0.22, 0.36, 0.37, 1.05, 1.11]})])
    assert ref_flat is not None
    assert gen_flat is not None
    assert _flat_values_match(ref_flat, gen_flat, decimals=4) is True


def test_merge_results_for_comparison_combines_split_tables():
    merged = _merge_results_for_comparison(
        [pd.DataFrame({"a": [1]}), pd.DataFrame({"a": [2]})],
        sort_rows=True,
    )
    assert isinstance(merged, pd.DataFrame)
    assert len(merged) == 2


def test_merge_results_for_comparison_handles_nested_dict():
    merged = _merge_results_for_comparison(
        [{"summary": {"x": 1}, "rows": [{"y": 2}, {"y": 3}]}],
        sort_rows=True,
    )
    assert isinstance(merged, pd.DataFrame)
    assert len(merged) >= 3


def test_compare_result_lists_pass_for_split_vs_combined():
    ref = _wrap_results([pd.DataFrame({"a": [1]}), pd.DataFrame({"a": [2]})])
    gen = _wrap_results([pd.DataFrame({"a": [1, 2]})])
    passed, err = _compare_result_lists(ref, gen)
    assert passed is True
    assert err is None


def test_compare_result_lists_falls_back_to_ai_text(monkeypatch):
    monkeypatch.setattr(
        "sweagent.text2sql.evaluator._compare_text_with_ai",
        lambda ref_text, gen_text, question, model=None: (True, ""),
    )
    ref = _wrap_results([{"summary": {"x": 1}}])
    gen = _wrap_results([{"another": {"x": 1}}])
    passed, err = _compare_result_lists(ref, gen)
    assert passed is True
    assert err is None


def test_should_force_ai_judge_for_text_question():
    ref = _wrap_results(["文字结论"])
    gen = _wrap_results(["另一种说法"])
    assert _should_force_ai_judge(ref, gen, "请描述原因") is True


def test_should_force_ai_judge_for_non_structured_outputs():
    ref = _wrap_results([{"summary": {"x": 1}}])
    gen = _wrap_results([{"summary": {"x": 1}}])
    assert _should_force_ai_judge(ref, gen, "普通问题") is False


def test_should_force_ai_judge_for_plain_text_output():
    ref = _wrap_results(["这是文字结果"])
    gen = _wrap_results(["这是另一种文字结果"])
    assert _should_force_ai_judge(ref, gen, "普通问题") is True


def test_compare_result_lists_forced_ai_judge(monkeypatch):
    monkeypatch.setattr(
        "sweagent.text2sql.evaluator._compare_text_with_ai",
        lambda ref_text, gen_text, question, model=None: (True, ""),
    )
    ref = _wrap_results(["文字结论"])
    gen = _wrap_results(["另一种说法"])
    passed, err = _compare_result_lists(ref, gen, question="请描述原因")
    assert passed is True
    assert err is None


def test_compare_result_lists_explicit_force_ai_judge(monkeypatch):
    monkeypatch.setattr(
        "sweagent.text2sql.evaluator._compare_text_with_ai",
        lambda ref_text, gen_text, question, model=None: (True, ""),
    )
    ref = _wrap_results([pd.DataFrame({"a": [1]})])
    gen = _wrap_results([pd.DataFrame({"a": [999]})])
    passed, err = _compare_result_lists(ref, gen, question="普通问题", force_ai_judge=True)
    assert passed is True
    assert err is None


def test_compare_result_lists_text_output_overrides_force_false(monkeypatch):
    monkeypatch.setattr(
        "sweagent.text2sql.evaluator._compare_with_ai_judge",
        lambda ref_exec, gen_exec, question="", decimals=4: (True, ""),
    )
    ref = _wrap_results(["alpha summary"])
    gen = _wrap_results(["beta summary"])
    passed, err = _compare_result_lists(ref, gen, question="普通问题", force_ai_judge=False)
    assert passed is True
    assert err is None


def test_compare_result_lists_force_ai_judge_with_images(monkeypatch):
    monkeypatch.setattr("sweagent.text2sql.evaluator._compare_images_with_ai", lambda a, b: (True, ""))
    monkeypatch.setattr("sweagent.text2sql.evaluator._compare_text_with_ai", lambda ref_text, gen_text, question, model=None: (True, ""))
    ref = _wrap_results([pd.DataFrame({"a": [1]}), b"refimg"])
    ref.result_types = ["dataframe", "image"]
    gen = _wrap_results([pd.DataFrame({"a": [2]}), b"genimg"])
    gen.result_types = ["dataframe", "image"]
    passed, err = _compare_result_lists(ref, gen, question="请绘图展示结果", force_ai_judge=True)
    assert passed is True
    assert err is None


def test_compare_result_lists_ai_reason_propagates(monkeypatch):
    monkeypatch.setattr("sweagent.text2sql.evaluator._compare_text_with_ai", lambda ref_text, gen_text, question, model=None: (False, "统计口径不同"))
    ref = _wrap_results(["参考结论"])
    gen = _wrap_results(["生成结论"])
    passed, err = _compare_result_lists(ref, gen, question="请描述原因", force_ai_judge=True)
    assert passed is False
    assert err == "AI裁判判定不一致；理由：统计口径不同"


def test_compare_items_ref_sql_direct(monkeypatch):
    import sweagent.text2sql.db_connector as db_module

    monkeypatch.setattr(
        db_module,
        "query_to_dataframe",
        lambda sql, max_rows=0: pd.DataFrame({"a": [1]}),
    )

    ref = {
        "id": "q1",
        "question": "test",
        "mode": "sql",
        "sql_code": "SELECT 1",
        "python_code": "",
        "result_vars": [],
    }
    gen = {
        "id": "q1",
        "question": "test",
        "mode": "sql",
        "sql_code": "SELECT 1",
        "python_code": "",
        "result_vars": [],
    }
    result = compare_items(ref, gen)
    assert result.passed is True


def test_db_connect_throttle_paces_connection_attempts(monkeypatch, tmp_path):
    import sweagent.text2sql.db_connector as db_module

    now = [100.0]
    sleeps = []

    monkeypatch.setenv("DB_CONNECT_MIN_INTERVAL_SEC", "0.5")
    monkeypatch.setenv("DB_CONNECT_RATE_LIMIT_FILE", str(tmp_path / "db_connect.lock"))
    monkeypatch.setattr(db_module.time, "time", lambda: now[0])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(db_module.time, "sleep", fake_sleep)

    db_module._throttle_connection_attempt()
    db_module._throttle_connection_attempt()

    assert sleeps == [pytest.approx(0.5)]


def test_db_connect_cooldown_delays_next_connection(monkeypatch, tmp_path):
    import sweagent.text2sql.db_connector as db_module

    now = [200.0]
    sleeps = []

    monkeypatch.setenv("DB_CONNECT_MIN_INTERVAL_SEC", "0.1")
    monkeypatch.setenv("DB_CONNECT_RATE_LIMIT_FILE", str(tmp_path / "db_connect.lock"))
    monkeypatch.setattr(db_module.time, "time", lambda: now[0])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(db_module.time, "sleep", fake_sleep)

    db_module._record_connection_cooldown(2.0)
    db_module._throttle_connection_attempt()

    assert sleeps == [pytest.approx(2.0)]


def test_db_connect_adaptive_burst_limit_only_slows_after_threshold(monkeypatch, tmp_path):
    import sweagent.text2sql.db_connector as db_module

    now = [300.0]
    sleeps = []

    monkeypatch.setenv("DB_CONNECT_MIN_INTERVAL_SEC", "0")
    monkeypatch.setenv("DB_CONNECT_BURST_LIMIT", "2")
    monkeypatch.setenv("DB_CONNECT_BURST_WINDOW_SEC", "10")
    monkeypatch.setenv("DB_CONNECT_RATE_LIMIT_FILE", str(tmp_path / "db_connect.lock"))
    monkeypatch.setattr(db_module.time, "time", lambda: now[0])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(db_module.time, "sleep", fake_sleep)

    db_module._throttle_connection_attempt()
    db_module._throttle_connection_attempt()
    assert sleeps == []

    db_module._throttle_connection_attempt()
    assert sleeps == [pytest.approx(10.0)]


def test_execution_timeout_error_records_db_cooldown(monkeypatch):
    import sweagent.text2sql.db_connector as db_module

    calls = []
    monkeypatch.setenv("TEXT2SQL_EVAL_TIMEOUT_DB_COOLDOWN_SEC", "12")
    monkeypatch.setattr(db_module, "_record_connection_cooldown", lambda seconds: calls.append(seconds))

    evaluator_module._record_db_cooldown_for_execution_error("subprocess timed out")

    assert calls == [12.0]


def test_compare_items_generated_missing_var_does_not_hard_fail():
    ref = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": "import pandas as pd\nref_df = pd.DataFrame({'a': [1]})",
        "result_vars": ["ref_df"],
    }
    gen = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": "import pandas as pd\ngen_df = pd.DataFrame({'a': [1]})",
        "result_vars": ["missing"],
    }
    result = compare_items(ref, gen)
    assert result.passed is True


def test_compare_items_direct_reference_results():
    ref = {
        "id": "q1",
        "question": "输出权重表",
        "mode": "python",
        "sql_code": "",
        "python_code": "",
        "result_vars": [],
        "reference_results": [[
            {"stock_code": "001234", "weight": 0.4},
            {"stock_code": "600000", "weight": 0.6},
        ]],
    }
    gen = {
        "id": "q1",
        "question": "输出权重表",
        "mode": "python",
        "sql_code": "",
        "python_code": (
            "import pandas as pd\n"
            "result_df = pd.DataFrame([\n"
            "    {'stock_code': '001234', 'weight': 0.4},\n"
            "    {'stock_code': '600000', 'weight': 0.6},\n"
            "])"
        ),
        "result_vars": ["result_df"],
    }
    result = compare_items(ref, gen)
    assert result.passed is True


def test_compare_items_without_reference_result_or_code_returns_ref_error():
    ref = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": "",
        "result_vars": [],
        "reference_results": [],
    }
    gen = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": "import pandas as pd\nresult_df = pd.DataFrame({'a': [1]})",
        "result_vars": ["result_df"],
    }
    result = compare_items(ref, gen)
    assert result.passed is False
    assert result.error_type == "ref_error"


def test_compare_items_uses_ai_injection_when_needed(monkeypatch):
    monkeypatch.setattr(
        "sweagent.text2sql.evaluator.get_result_plan_ai",
        lambda question, code, expected_count=None, model=None: {
            "result_vars": ["build_result"],
            "inject_code": "build_result = build()",
        },
    )

    ref = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": "import pandas as pd\n\ndef build():\n    return pd.DataFrame({'a': [1]})",
        "result_vars": [],
    }
    gen = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": "import pandas as pd\ngen_df = pd.DataFrame({'a': [1]})",
        "result_vars": ["gen_df"],
    }
    result = compare_items(ref, gen)
    assert result.passed is True


def test_evaluator_litellm_completion_retries_with_fixed_temperature():
    evaluator_module._LITELLM_FORCED_TEMPERATURES.clear()
    calls = []

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            calls.append(kwargs["temperature"])
            if kwargs["temperature"] == 0.0:
                raise Exception("OpenAIException - invalid temperature: only 0.6 is allowed for this model")
            return {"ok": True}

    try:
        response = _evaluator_litellm_completion(
            _FakeLiteLLM(),
            model="kimi-k2.5",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert response == {"ok": True}
        assert calls == [0.0, 0.6]
        assert evaluator_module._LITELLM_FORCED_TEMPERATURES["kimi-k2.5"] == 0.6
    finally:
        evaluator_module._LITELLM_FORCED_TEMPERATURES.clear()


def test_evaluator_litellm_completion_uses_cached_temperature():
    evaluator_module._LITELLM_FORCED_TEMPERATURES.clear()
    evaluator_module._LITELLM_FORCED_TEMPERATURES["kimi-k2.5"] = 0.6
    calls = []

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            calls.append(kwargs["temperature"])
            return {"ok": True}

    try:
        response = _evaluator_litellm_completion(
            _FakeLiteLLM(),
            model="kimi-k2.5",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert response == {"ok": True}
        assert calls == [0.6]
    finally:
        evaluator_module._LITELLM_FORCED_TEMPERATURES.clear()


def test_compare_items_count_mismatch_merged_comparison_passes():
    ref = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": (
            "import pandas as pd\n"
            "df1 = pd.DataFrame({'a': [1]})\n"
            "df2 = pd.DataFrame({'a': [2]})"
        ),
        "result_vars": ["df1", "df2"],
    }
    gen = {
        "id": "q1",
        "question": "test",
        "mode": "python",
        "sql_code": "",
        "python_code": "import pandas as pd\ndf = pd.DataFrame({'a': [1, 2]})",
        "result_vars": ["df"],
    }
    result = compare_items(ref, gen)
    assert result.passed is True


def test_serialize_for_text_judge_is_json_like():
    serialized = _serialize_for_text_judge([{"a": 1}, pd.DataFrame({"b": [2]})])
    assert '"a"' in serialized
    assert '"type": "dataframe"' in serialized


def test_format_result_for_report_handles_list():
    text = format_result_for_report([{"a": 1}, {"b": 2}])
    assert "[Result 1]" in text
    assert "[Result 2]" in text
