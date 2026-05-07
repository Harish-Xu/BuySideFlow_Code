import json

import pytest

from sweagent.exceptions import FunctionCallingFormatError
from sweagent.tools.commands import Argument, Command
from sweagent.tools.parsing import FunctionCallingParser


def _make_payload_command(name: str) -> Command:
    return Command(
        name=name,
        docstring=f"{name} payload command",
        signature=f"{name}\n<payload>\nEND",
        end_name="END",
        arguments=[
            Argument(
                name="payload",
                type="string",
                description="JSON payload",
                required=True,
            )
        ],
    )


def test_function_calling_parser_recovers_malformed_submit_payload():
    parser = FunctionCallingParser()
    command = _make_payload_command("submit")
    payload = json.dumps(
        {
            "mode": "sql+python",
            "sql_code": "SELECT 1",
            "python_code": "result = 1",
            "result_vars": ["result"],
        },
        ensure_ascii=False,
    )
    malformed_arguments = '{"payload": "' + json.dumps(payload)[1:-1].replace('\\"', '\\\\\\"')

    _, action = parser(
        {
            "message": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "submit",
                        "arguments": malformed_arguments,
                    }
                }
            ],
        },
        [command],
    )

    assert action == f"submit\n{payload}\nEND"


def test_function_calling_parser_keeps_valid_payload_arguments():
    parser = FunctionCallingParser()
    command = _make_payload_command("run_code")
    payload = json.dumps(
        {
            "mode": "sql",
            "sql_code": "SELECT 1",
            "python_code": None,
            "result_vars": [],
        },
        ensure_ascii=False,
    )

    _, action = parser(
        {
            "message": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "run_code",
                        "arguments": json.dumps({"payload": payload}, ensure_ascii=False),
                    }
                }
            ],
        },
        [command],
    )

    assert action == f"run_code\n{payload}\nEND"


@pytest.mark.parametrize("arguments", ['"just-a-string"', "[1, 2, 3]"])
def test_function_calling_parser_rejects_non_object_arguments(arguments: str):
    parser = FunctionCallingParser()
    command = _make_payload_command("submit")

    with pytest.raises(FunctionCallingFormatError) as exc_info:
        parser(
            {
                "message": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "submit",
                            "arguments": arguments,
                        }
                    }
                ],
            },
            [command],
        )

    assert exc_info.value.extra_info["error_code"] == "invalid_json"
