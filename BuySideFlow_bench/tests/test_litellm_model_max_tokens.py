from types import SimpleNamespace
import sys
import types

import litellm

if "swerex" not in sys.modules:
    sys.modules["swerex"] = types.ModuleType("swerex")
if "swerex.exceptions" not in sys.modules:
    exceptions = types.ModuleType("swerex.exceptions")

    class SwerexException(Exception):
        pass

    exceptions.SwerexException = SwerexException
    sys.modules["swerex.exceptions"] = exceptions
    sys.modules["swerex"].exceptions = exceptions
if "sweagent.tools.tools" not in sys.modules:
    tools_module = types.ModuleType("sweagent.tools.tools")

    class ToolConfig:
        pass

    tools_module.ToolConfig = ToolConfig
    sys.modules["sweagent.tools.tools"] = tools_module

from sweagent.agent.models import GenericAPIModelConfig, LiteLLMModel


class _DummyTools:
    use_function_calling = False
    tools = []


def _fake_response(content: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


def _fake_token_counter(*args, **kwargs):
    if "text" in kwargs:
        return max(1, len(kwargs["text"]))
    return 1


def test_litellm_model_passes_registry_max_tokens(monkeypatch):
    captured = {}

    monkeypatch.setattr(litellm.utils, "token_counter", _fake_token_counter)
    monkeypatch.setattr(litellm.cost_calculator, "completion_cost", lambda *args, **kwargs: 0.0)

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = LiteLLMModel(
        GenericAPIModelConfig(name="deepseek/deepseek-chat"),
        _DummyTools(),
    )

    model._single_query([{"role": "user", "content": "hello"}])

    assert captured["max_tokens"] == 8192


def test_litellm_model_preserves_explicit_max_tokens(monkeypatch):
    captured = {}

    monkeypatch.setattr(litellm.utils, "token_counter", _fake_token_counter)
    monkeypatch.setattr(litellm.cost_calculator, "completion_cost", lambda *args, **kwargs: 0.0)

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = LiteLLMModel(
        GenericAPIModelConfig(
            name="deepseek/deepseek-chat",
            completion_kwargs={"max_tokens": 2048},
        ),
        _DummyTools(),
    )

    model._single_query([{"role": "user", "content": "hello"}])

    assert captured["max_tokens"] == 2048
