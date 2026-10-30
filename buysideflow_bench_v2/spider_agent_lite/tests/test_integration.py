from __future__ import annotations

import json
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spider_agent_lite.adapter.assets import SHARED_ASSETS, sha256_file, shared_asset_manifest
from spider_agent_lite.adapter.actions import GetTradingDays, RunCode, Submit
from spider_agent_lite.adapter.agent import create_agent
from spider_agent_lite.adapter.environment import BuySideFlowEnv
from spider_agent_lite.adapter.model_gateway import (
    call_current_deepseek,
    call_current_gpt55,
    configure_model_retries,
    resolve_model_name,
)
from spider_agent_lite.adapter.problem import build_problem_statement
from spider_agent_lite.adapter.prompt import build_system_prompt
from spider_agent_lite.adapter.task_loader import load_tasks, to_spider_task
from spider_agent_lite.adapter.trajectory import normalize_trajectory
from spider_agent_lite.adapter.runner import run_task


def _upstream_runtime_dependencies_available() -> bool:
    for module in ("dashscope", "docker", "google.generativeai", "groq", "gymnasium"):
        try:
            if importlib.util.find_spec(module) is None:
                return False
        except (ImportError, ModuleNotFoundError):
            return False
    return True


class IntegrationTests(unittest.TestCase):
    def test_upstream_manifest_matches_snapshot(self) -> None:
        base = ROOT / "spider_agent_lite"
        manifest = json.loads((base / "UPSTREAM_MANIFEST.json").read_text())
        self.assertEqual(manifest["commit"], "cafb867313aab4e674652054198f383cf4018943")
        self.assertEqual(manifest["license"]["sha256"], sha256_file(base / "LICENSE"))
        actual = {
            str(path.relative_to(base / "upstream")): sha256_file(path)
            for path in sorted((base / "upstream").rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
        self.assertEqual(manifest["files"], actual)

    def test_credentials_and_generated_artifacts_are_excluded(self) -> None:
        base = ROOT / "spider_agent_lite/upstream"
        self.assertFalse((base / "bigquery_credential.json").exists())
        self.assertFalse((base / "snowflake_credential.json").exists())
        self.assertFalse((base / "logs").exists())
        self.assertFalse((base / "output").exists())

    def test_shared_assets_are_not_copied(self) -> None:
        manifest = shared_asset_manifest()
        self.assertEqual(set(manifest), set(SHARED_ASSETS))
        self.assertFalse((ROOT / "spider_agent_lite/assets").exists())

    def test_canonical_tasks_load_without_references_in_envelope(self) -> None:
        tasks = load_tasks()
        self.assertEqual(len(tasks), 408)
        envelope = to_spider_task(tasks[0])
        self.assertEqual(envelope["type"], "BuySideFlow")
        self.assertNotIn("sql_code", envelope)
        self.assertNotIn("python_code", envelope)
        self.assertNotIn("reference_results", envelope)

    def test_spider_actions_parse_runtime_contract(self) -> None:
        payload = json.dumps(
            {"mode": "sql", "sql_code": "SELECT 1", "python_code": "", "result_vars": []}
        )
        run_action = RunCode.parse_action_from_text(f"run_code\n{payload}\nEND_RUN_CODE")
        submit_action = Submit.parse_action_from_text(f"submit\n{payload}\nEND_SUBMIT")
        calendar_action = GetTradingDays.parse_action_from_text(
            "get_trading_days 2025-01-01 2025-01-31 market=83,90"
        )
        self.assertIsInstance(run_action, RunCode)
        self.assertIsInstance(submit_action, Submit)
        self.assertEqual(run_action.payload, payload)
        self.assertEqual(submit_action.payload, payload)
        self.assertEqual(calendar_action.markets, "83,90")

    def test_current_deepseek_alias_and_backend_configuration(self) -> None:
        self.assertEqual(resolve_model_name("deepseek"), "deepseek/deepseek-v4-flash")
        self.assertEqual(
            resolve_model_name("deepseek-v4-pro"), "deepseek/deepseek-v4-pro"
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Thought: ok"))]
        )
        payload = {
            "model": "deepseek",
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "rules"}]},
                {"role": "user", "content": [{"type": "text", "text": "task"}]},
            ],
            "max_tokens": 65536,
            "top_p": None,
            "temperature": 0.0,
        }
        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEEK_API_KEY": "test-key",
                    "DEEPSEEK_API_BASE": "https://deepseek.example/v1",
                },
            ),
            patch("litellm.completion", return_value=response) as completion,
        ):
            success, content = call_current_deepseek(payload)
        self.assertTrue(success)
        self.assertEqual(content, "Thought: ok")
        request = completion.call_args.kwargs
        self.assertEqual(request["model"], "deepseek/deepseek-v4-flash")
        self.assertEqual(request["api_base"], "https://deepseek.example/v1")
        self.assertEqual(request["api_key"], "test-key")
        self.assertEqual(request["timeout"], 300.0)
        self.assertEqual(request["max_tokens"], 65536)
        self.assertEqual(request["temperature"], 0.0)
        self.assertNotIn("top_p", request)
        self.assertEqual(request["messages"][0], {"role": "system", "content": "rules"})

    def test_current_deepseek_retry_budget_is_bounded(self) -> None:
        configure_model_retries(attempts=2, min_wait=0, max_wait=0)
        payload = {
            "model": "deepseek",
            "messages": [{"role": "user", "content": "task"}],
            "max_tokens": 65536,
            "top_p": None,
            "temperature": 0.0,
        }
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}),
            patch("litellm.completion", side_effect=RuntimeError("temporary")) as completion,
            patch("spider_agent_lite.adapter.model_gateway.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "after 2 attempts"):
                call_current_deepseek(payload)
        self.assertEqual(completion.call_count, 2)
        configure_model_retries(attempts=20, min_wait=10, max_wait=120)

    def test_gpt55_gateway_uses_supported_completion_parameters(self) -> None:
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Thought: ok"))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
        )
        payload = {
            "model": "gpt-5.5",
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "rules"}]},
                {"role": "user", "content": [{"type": "text", "text": "task"}]},
            ],
            "max_tokens": 65536,
            "top_p": None,
            "temperature": 0.0,
        }
        with patch("openai.OpenAI", return_value=client) as factory:
            with patch.object(client.chat.completions, "create", return_value=response) as create:
                success, content = call_current_gpt55(payload)
        self.assertTrue(success)
        self.assertEqual(content, "Thought: ok")
        self.assertEqual(factory.call_args.kwargs["timeout"], 300.0)
        self.assertEqual(create.call_args.kwargs, {
            "model": "gpt-5.5",
            "messages": [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "task"},
            ],
            "max_completion_tokens": 65536,
        })

    def test_environment_bridges_run_code_and_submit(self) -> None:
        problem = SimpleNamespace(id="unit_bridge", question="test", extra_fields={})
        payload = {"mode": "sql", "sql_code": "SELECT 1", "python_code": "", "result_vars": []}
        payload_text = json.dumps(payload)
        env = BuySideFlowEnv(problem)
        with (
            patch("spider_agent_lite.adapter.environment.get_run_code_count", return_value=0),
            patch("spider_agent_lite.adapter.environment.get_last_run_code_success", return_value=False),
        ):
            rejected, rejected_done = env.step(Submit(output=payload_text))
            self.assertIn("call run_code", rejected)
            self.assertFalse(rejected_done)

        with (
            patch(
                "spider_agent_lite.adapter.environment.build_run_code_observation",
                return_value="execution succeeded",
            ) as execute,
            patch("spider_agent_lite.adapter.environment.get_run_code_count", return_value=1),
            patch("spider_agent_lite.adapter.environment.get_last_run_code_success", return_value=True),
        ):
            observation, done = env.step(RunCode(payload=payload_text))
            self.assertEqual(observation, "execution succeeded")
            self.assertFalse(done)
            execute.assert_called_once_with(problem, payload_text)

            changed = {**payload, "sql_code": "SELECT 2"}
            accepted, accepted_done = env.step(Submit(output=json.dumps(changed)))
            self.assertEqual(accepted, "Text2SQL submission accepted.")
            self.assertTrue(accepted_done)
            self.assertEqual(env.submission, changed)

        with (
            patch("spider_agent_lite.adapter.environment.get_run_code_count", return_value=1),
            patch("spider_agent_lite.adapter.environment.get_last_run_code_success", return_value=False),
        ):
            rejected, rejected_done = env.step(Submit(output=payload_text))
            self.assertIn("latest run_code", rejected)
            self.assertFalse(rejected_done)

    def test_formal_problem_loader_and_prompt_do_not_expose_reference_code(self) -> None:
        problem = build_problem_statement("bond_001")
        self.assertEqual(problem.id, "bond_001")
        prompt = build_system_prompt(problem, action_space="TOOLS", max_steps=20)
        self.assertIn(problem.question, prompt)
        self.assertIn("TOOLS", prompt)
        self.assertNotIn(problem.reference_sql, prompt)
        if problem.reference_python:
            self.assertNotIn(problem.reference_python, prompt)

    @unittest.skipUnless(
        _upstream_runtime_dependencies_available(),
        "optional upstream runtime dependencies are not installed",
    )
    def test_upstream_decision_loop_accepts_bridge_actions(self) -> None:
        payload = json.dumps(
            {"mode": "sql", "sql_code": "SELECT 1", "python_code": "", "result_vars": []}
        )
        responses = iter(
            [
                f"Thought: verify\nAction: run_code\n{payload}\nEND_RUN_CODE",
                f"Thought: finish\nAction: submit\n{payload}\nEND_SUBMIT",
            ]
        )
        problem = SimpleNamespace(
            id="loop_bridge",
            question="Return one.",
            reference_sql="SECRET_REFERENCE",
            reference_python="",
            extra_fields={
                "output_contract": "one row",
                "evaluation_contract": "exact",
                "business_background": "rules",
                "domain_rules": "",
                "table_name_list": "dual",
            },
        )
        env = BuySideFlowEnv(problem)
        agent = create_agent(
            model="deepseek",
            max_steps=3,
            max_memory_length=5,
            max_tokens=100,
            temperature=0,
            top_p=1,
            use_plan=False,
        )
        agent.set_env_and_task(env)
        self.assertEqual(agent.model, "deepseek/deepseek-v4-flash")
        import spider_agent.agent.agents as upstream_agents

        with (
            patch.object(upstream_agents, "call_llm", side_effect=lambda request: (True, next(responses))),
            patch(
                "spider_agent_lite.adapter.environment.build_run_code_observation",
                return_value="execution succeeded",
            ),
            patch("spider_agent_lite.adapter.environment.get_run_code_count", return_value=1),
            patch("spider_agent_lite.adapter.environment.get_last_run_code_success", return_value=True),
        ):
            finished, result = agent.run()
        self.assertTrue(finished)
        self.assertEqual(len(agent.get_trajectory()["trajectory"]), 2)
        self.assertEqual(json.loads(result), env.submission)
        self.assertEqual(agent.run.__func__.__module__, "spider_agent.agent.agents")
        self.assertEqual(agent.predict.__func__.__module__, "spider_agent.agent.agents")

    def test_runner_persists_submission_and_both_trajectory_forms(self) -> None:
        problem = SimpleNamespace(id="runner_test", question="test", extra_fields={})
        submission = {"mode": "sql", "sql_code": "SELECT 1", "python_code": "", "result_vars": []}

        class FakeAgent:
            def set_env_and_task(self, env):
                self.env = env

            def run(self):
                self.env.submission = submission
                return True, json.dumps(submission)

            def get_trajectory(self):
                return {"Task": "test", "system_message": "prompt", "trajectory": [{"action": "submit"}]}

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("spider_agent_lite.adapter.runner.require_db_snapshot_id", return_value="snapshot-v1"),
            patch("spider_agent_lite.adapter.runner.build_problem_statement", return_value=problem),
            patch("spider_agent_lite.adapter.runner.create_agent", return_value=FakeAgent()),
        ):
            result_path = run_task("runner_test", model="fake/model", output_dir=temp_dir)
            self.assertTrue(result_path.is_file())
            self.assertTrue((result_path.parent / "submission.json").is_file())
            self.assertTrue((result_path.parent / "trajectory.normalized.json").is_file())
            result = json.loads(result_path.read_text())
            self.assertEqual(result["submission"], submission)
            self.assertEqual(result["adapter"]["db_snapshot_id"], "snapshot-v1")
            self.assertEqual(result["adapter"]["max_steps"], 300)
            self.assertEqual(result["adapter"]["max_tokens"], 65536)
            self.assertEqual(result["adapter"]["temperature"], 0.0)
            self.assertIsNone(result["adapter"]["top_p"])
            self.assertEqual(result["adapter"]["model_retry_attempts"], 20)
            self.assertEqual(result_path.parent.parent.name, "spider_agent_lite__fake--model")
            reports = list(result_path.parent.parent.glob("benchmark_*_generation.md"))
            self.assertEqual(len(reports), 1)
            self.assertIn("NOT EVALUATED", reports[0].read_text())
            predictions = json.loads((result_path.parent.parent / "preds.json").read_text())
            self.assertEqual(json.loads(predictions["runner_test"]["model_patch"]), submission)

    def test_normalized_trajectory_keeps_raw_hash(self) -> None:
        raw = {"finished": True, "steps": 1, "result": "done", "trajectory": [{"action": "x"}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            normalized = normalize_trajectory(path, "bond_001")
            self.assertEqual(normalized["task_id"], "bond_001")
            self.assertEqual(normalized["trajectory"], raw["trajectory"])
            self.assertEqual(normalized["raw_result_sha256"], sha256_file(path))


if __name__ == "__main__":
    unittest.main()
