import pytest

pytest.importorskip("simple_parsing")
pytest.importorskip("swerex")

from sweagent.agent.agents import DefaultAgent
from sweagent.types import StepOutput


class _DummyHook:
    def on_action_started(self, *, step):
        pass

    def on_action_executed(self, *, step):
        pass


class _DummyLogger:
    def warning(self, *args, **kwargs):
        pass


def _make_text2sql_agent_for_repeat_tests():
    agent = object.__new__(DefaultAgent)
    agent._last_text2sql_run_code_request = None
    agent._last_text2sql_run_code_request_count = 0
    agent._last_text2sql_run_code_observation = None
    agent._last_text2sql_schema_action = None
    agent._last_text2sql_schema_action_count = 0
    agent._text2sql_run_code_repeat_limit_reached = False
    agent._text2sql_consecutive_database_timeouts = 0
    agent._chook = _DummyHook()
    agent.logger = _DummyLogger()
    agent._problem_statement = object()
    return agent


def test_identical_run_code_is_refused_on_third_repeat(monkeypatch):
    calls = []

    def fake_build_run_code_observation(problem_statement, payload):
        calls.append(payload)
        return f"executed {len(calls)}"

    monkeypatch.setattr(
        "sweagent.agent.agents.build_run_code_observation",
        fake_build_run_code_observation,
    )

    agent = _make_text2sql_agent_for_repeat_tests()
    payload = '{"mode":"sql","sql_code":"SELECT 1","python_code":null,"result_vars":[]}'

    first = agent._handle_text2sql_run_code(StepOutput(), payload)
    second = agent._handle_text2sql_run_code(StepOutput(), payload)
    third = agent._handle_text2sql_run_code(StepOutput(), payload)

    assert first.observation == "executed 1"
    assert second.observation == "executed 2"
    assert len(calls) == 2
    assert "Repeated identical run_code request detected (x3)" in third.observation
    assert "refused and was not executed again" in third.observation
    assert "Hard repeat limit" not in third.observation


def test_identical_run_code_fifth_repeat_requires_submit(monkeypatch):
    calls = []

    def fake_build_run_code_observation(problem_statement, payload):
        calls.append(payload)
        return f"executed {len(calls)}"

    monkeypatch.setattr(
        "sweagent.agent.agents.build_run_code_observation",
        fake_build_run_code_observation,
    )

    agent = _make_text2sql_agent_for_repeat_tests()
    payload = '{"mode":"sql","sql_code":"SELECT 1","python_code":null,"result_vars":[]}'

    for _ in range(4):
        agent._handle_text2sql_run_code(StepOutput(), payload)
    fifth = agent._handle_text2sql_run_code(StepOutput(), payload)

    assert len(calls) == 2
    assert "Repeated identical run_code request detected (x5)" in fifth.observation
    assert "Hard repeat limit reached" in fifth.observation
    assert "next action must be submit" in fifth.observation
    assert "Previous run_code result" not in fifth.observation
    assert agent._text2sql_run_code_repeat_limit_reached is True
    assert agent._get_text2sql_submission_exit_status() == "submitted (run_code_repeat_limit)"


def test_changed_run_code_payload_is_not_treated_as_identical(monkeypatch):
    calls = []

    def fake_build_run_code_observation(problem_statement, payload):
        calls.append(payload)
        return f"executed {len(calls)}"

    monkeypatch.setattr(
        "sweagent.agent.agents.build_run_code_observation",
        fake_build_run_code_observation,
    )

    agent = _make_text2sql_agent_for_repeat_tests()
    payload = '{"mode":"sql","sql_code":"SELECT 1","python_code":null,"result_vars":[]}'
    changed_payload = '{"mode":"sql","sql_code":"SELECT  1","python_code":null,"result_vars":[]}'

    for _ in range(3):
        agent._handle_text2sql_run_code(StepOutput(), payload)
    changed = agent._handle_text2sql_run_code(StepOutput(), changed_payload)

    assert len(calls) == 3
    assert changed.observation == "executed 3"


def test_consecutive_database_timeouts_add_soft_warning(monkeypatch):
    observations = iter(
        [
            "执行出错: MySQL execution failed: (3024, 'Query execution was interrupted, maximum statement execution time exceeded')",
            "执行出错: MySQL execution failed: (3024, 'Query execution was interrupted, maximum statement execution time exceeded')",
        ]
    )

    def fake_build_run_code_observation(problem_statement, payload):
        return next(observations)

    monkeypatch.setattr(
        "sweagent.agent.agents.build_run_code_observation",
        fake_build_run_code_observation,
    )

    agent = _make_text2sql_agent_for_repeat_tests()
    first_payload = '{"mode":"sql","sql_code":"SELECT 1","python_code":null,"result_vars":[]}'
    second_payload = '{"mode":"sql","sql_code":"SELECT 2","python_code":null,"result_vars":[]}'

    first = agent._handle_text2sql_run_code(StepOutput(), first_payload)
    second = agent._handle_text2sql_run_code(StepOutput(), second_payload)

    assert "Database query timeout detected" not in first.observation
    assert "Database query timeout detected in consecutive run_code executions (x2)" in second.observation
    assert "This is a soft warning, not a rejection" in second.observation


def test_non_timeout_run_code_resets_database_timeout_warning(monkeypatch):
    observations = iter(
        [
            "执行出错: MySQL execution failed: (3024, 'Query execution was interrupted')",
            "结果 1（DataFrame，显示前 20 行）",
            "执行出错: MySQL execution failed: (3024, 'Query execution was interrupted')",
        ]
    )

    def fake_build_run_code_observation(problem_statement, payload):
        return next(observations)

    monkeypatch.setattr(
        "sweagent.agent.agents.build_run_code_observation",
        fake_build_run_code_observation,
    )

    agent = _make_text2sql_agent_for_repeat_tests()
    payloads = [
        '{"mode":"sql","sql_code":"SELECT 1","python_code":null,"result_vars":[]}',
        '{"mode":"sql","sql_code":"SELECT 2","python_code":null,"result_vars":[]}',
        '{"mode":"sql","sql_code":"SELECT 3","python_code":null,"result_vars":[]}',
    ]

    agent._handle_text2sql_run_code(StepOutput(), payloads[0])
    agent._handle_text2sql_run_code(StepOutput(), payloads[1])
    third = agent._handle_text2sql_run_code(StepOutput(), payloads[2])

    assert "Database query timeout detected" not in third.observation
