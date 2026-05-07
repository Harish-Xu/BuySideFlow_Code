from sweagent.agent.text2sql_helpers import (
    TEXT2SQL_CONTEXT_TRUNCATION_NOTICE,
    format_step_budget_warning,
    normalize_text2sql_schema_action,
    truncate_history_for_context_overflow,
)


def test_normalize_get_trading_days_action_uses_keyword_markets():
    action = normalize_text2sql_schema_action(
        "get_trading_days",
        start_date="2024-01-01",
        end_date="2024-12-31",
        markets="83",
    )

    assert action == "get_trading_days 2024-01-01 2024-12-31 market=83"


def test_format_step_budget_warning_mentions_remaining_steps():
    warning = format_step_budget_warning(180)

    assert "当前已进行 180 步" in warning
    assert "还有 20 步的交互额度" in warning


def test_context_overflow_truncation_keeps_core_context_and_recent_steps():
    history = [
        {
            "role": "system",
            "content": "system",
            "agent": "main",
            "message_type": "system",
        },
        {
            "role": "user",
            "content": "instance prompt",
            "agent": "main",
            "message_type": "observation",
        },
    ]
    for idx in range(8):
        history.append(
            {
                "role": "assistant" if idx % 2 == 0 else "user",
                "content": f"message-{idx}",
                "agent": "main",
                "message_type": "action" if idx % 2 == 0 else "observation",
            }
        )
    history.append(
        {
            "role": "user",
            "content": "other-agent",
            "agent": "reviewer",
            "message_type": "observation",
        }
    )

    truncated = truncate_history_for_context_overflow(
        history,
        agent_name="main",
        keep_recent_messages=4,
    )
    main_contents = [entry["content"] for entry in truncated if entry.get("agent") == "main"]

    assert len(main_contents) < len([entry for entry in history if entry.get("agent") == "main"])
    assert main_contents[0] == "system"
    assert "instance prompt" in main_contents
    assert "message-0" not in main_contents
    assert main_contents[-1] == TEXT2SQL_CONTEXT_TRUNCATION_NOTICE
    assert any(entry["agent"] == "reviewer" for entry in truncated)
