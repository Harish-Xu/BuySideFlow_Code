from __future__ import annotations

from typing import Any
import importlib

from .actions import ACTION_CLASSES
from .model_gateway import install_model_gateway, resolve_model_name
from .prompt import build_system_prompt
from .upstream import load_upstream_prompt_agent


def create_agent(**kwargs: Any):
    """Create an upstream PromptAgent with only its environment binding adapted."""
    upstream_prompt_agent = load_upstream_prompt_agent()
    upstream_agent_module = importlib.import_module("spider_agent.agent.agents")
    install_model_gateway(upstream_agent_module)
    kwargs["model"] = resolve_model_name(str(kwargs.get("model") or ""))

    class BuySideFlowPromptAgent(upstream_prompt_agent):
        def set_env_and_task(self, env) -> None:
            if self.use_plan:
                raise ValueError("Reference-plan mode is not part of the BuySideFlow comparison")
            self.env = env
            self.thoughts = []
            self.responses = []
            self.actions = []
            self.observations = []
            self.codes = []
            self.history_messages = []
            self.instruction = env.task_config["question"]
            self._AVAILABLE_ACTION_CLASSES = list(ACTION_CLASSES)
            action_space = "".join(
                action_cls.get_action_description() for action_cls in self._AVAILABLE_ACTION_CLASSES
            )
            self.system_message = build_system_prompt(
                env.problem_statement, action_space=action_space, max_steps=self.max_steps
            )
            self.history_messages.append(
                {
                    "role": "system",
                    "content": [{"type": "text", "text": self.system_message}],
                }
            )

    return BuySideFlowPromptAgent(**kwargs)
