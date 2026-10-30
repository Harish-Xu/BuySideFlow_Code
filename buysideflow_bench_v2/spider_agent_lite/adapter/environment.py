from __future__ import annotations

import json
from typing import Any

from sweagent.text2sql.runtime import (
    build_describe_tables_observation,
    build_get_columns_observation,
    build_get_trading_days_observation,
    build_run_code_observation,
    build_schema_observation,
    build_search_columns_observation,
    build_search_tables_observation,
    get_last_run_code_success,
    get_run_code_count,
    reset_run_code_count,
    validate_submission_payload,
)

from .actions import (
    DescribeTables,
    GetColumns,
    GetTradingDays,
    RequestSchema,
    RunCode,
    SearchColumns,
    SearchTables,
    Submit,
)
MAX_OBSERVATION_CHARS = 40_000


def _payload_dict(payload: str) -> dict[str, Any]:
    data = json.loads(payload)
    if isinstance(data, str):
        return {"mode": "sql", "sql_code": data.strip(), "python_code": "", "result_vars": []}
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


class BuySideFlowEnv:
    """Host-side Spider environment backed by the formal JYDB runtime."""

    def __init__(self, problem_statement: Any, *, max_run_code_calls: int = 100):
        self.problem_statement = problem_statement
        self.task_config = {
            "instance_id": str(problem_statement.id),
            "question": problem_statement.question,
            "type": "BuySideFlow",
        }
        self.max_run_code_calls = max_run_code_calls
        self.submission: dict[str, Any] | None = None
        self.closed = False
        reset_run_code_count(str(problem_statement.id))

    def step(self, action: Any) -> tuple[str, bool]:
        if self.closed:
            raise RuntimeError("Environment is closed")
        done = False
        if isinstance(action, RunCode):
            observation = self._run_code(action)
        elif isinstance(action, Submit):
            observation, done = self._submit(action)
        elif isinstance(action, DescribeTables):
            observation = build_describe_tables_observation(self.problem_statement, action.tables)
        elif isinstance(action, SearchTables):
            observation = build_search_tables_observation(self.problem_statement, action.keywords)
        elif isinstance(action, GetColumns):
            observation = build_get_columns_observation(self.problem_statement, action.table_name)
        elif isinstance(action, SearchColumns):
            observation = build_search_columns_observation(
                self.problem_statement, action.keywords, action.table_scope
            )
        elif isinstance(action, RequestSchema):
            observation = build_schema_observation(self.problem_statement, action.tables)
        elif isinstance(action, GetTradingDays):
            observation = build_get_trading_days_observation(
                action.start_date, action.end_date, action.markets
            )
        else:
            observation = f"Unrecognized BuySideFlow action: {type(action).__name__}"
        return self._truncate(observation), done

    def _run_code(self, action: RunCode) -> str:
        qid = str(self.problem_statement.id)
        if self.max_run_code_calls > 0 and get_run_code_count(qid) >= self.max_run_code_calls:
            return f"run_code call limit reached ({self.max_run_code_calls}). Submit the best verified answer."
        observation = build_run_code_observation(self.problem_statement, action.payload)
        return observation

    def _submit(self, action: Submit) -> tuple[str, bool]:
        validation_error = validate_submission_payload(action.payload)
        if validation_error is not None:
            return f"Submission rejected.\n{validation_error}", False
        qid = str(self.problem_statement.id)
        if get_run_code_count(qid) == 0:
            return "Submission rejected: call run_code before submit.", False
        if not get_last_run_code_success(qid):
            return "Submission rejected: the latest run_code did not complete successfully.", False
        try:
            candidate = _payload_dict(action.payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return f"Submission rejected: {exc}", False
        self.submission = candidate
        action.output = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        return "Text2SQL submission accepted.", True

    @staticmethod
    def _truncate(observation: Any) -> str:
        text = str(observation)
        if len(text) <= MAX_OBSERVATION_CHARS:
            return text
        return text[:MAX_OBSERVATION_CHARS] + "\n[Observation truncated.]"

    def close(self) -> None:
        self.closed = True
