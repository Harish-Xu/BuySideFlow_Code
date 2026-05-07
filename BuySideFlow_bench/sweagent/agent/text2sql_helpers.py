from __future__ import annotations

from typing import Any


TEXT2SQL_CONTEXT_TRUNCATION_NOTICE = (
    "[系统提示] 由于对话历史过长，已自动截断早期步骤。"
    "请基于最近的上下文继续完成当前任务，直接给出下一步操作。"
)


def format_step_budget_warning(warning_step: int) -> str:
    return (
        f"【系统提醒】当前已进行 {warning_step} 步，你还有 20 步的交互额度。"
        "请务必在 20 步内调用 submit 提交最终答案，否则系统将强制提交。"
    )


def normalize_text2sql_schema_action(
    tool_name: str,
    *,
    table_names: list[str] | None = None,
    table_name: str | None = None,
    keywords: list[str] | None = None,
    table_scope: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    markets: str | None = None,
) -> str:
    if table_names is not None:
        args = ",".join(name.strip() for name in table_names if name.strip())
        return f"{tool_name} {args}".strip()
    if table_name is not None:
        args = " ".join(table_name.split())
        return f"{tool_name} {args}".strip()
    if start_date is not None or end_date is not None:
        parts = [p for p in [start_date, end_date] if p is not None]
        if markets is not None:
            parts.append(f"market={markets}")
        return f"{tool_name} {' '.join(parts)}".strip()
    kw_part = ",".join(word.strip() for word in (keywords or []) if word.strip())
    scope_part = ",".join(name.strip() for name in (table_scope or []) if name.strip())
    args = f"{kw_part} in {scope_part}" if scope_part else kw_part
    return f"{tool_name} {args}".strip()


def truncate_history_for_context_overflow(
    history: list[dict[str, Any]],
    *,
    agent_name: str,
    keep_recent_messages: int = 10,
) -> list[dict[str, Any]]:
    current_agent_positions = [i for i, entry in enumerate(history) if entry.get("agent") == agent_name]
    if len(current_agent_positions) <= keep_recent_messages:
        return list(history)

    keep_positions: set[int] = set(current_agent_positions[-keep_recent_messages:])
    for idx in current_agent_positions:
        entry = history[idx]
        if entry.get("role") == "system":
            keep_positions.add(idx)
            break
    for idx in current_agent_positions:
        entry = history[idx]
        if (
            entry.get("role") == "user"
            and entry.get("message_type") == "observation"
            and not entry.get("is_demo", False)
        ):
            keep_positions.add(idx)
            break

    truncated = [
        entry
        for idx, entry in enumerate(history)
        if entry.get("agent") != agent_name or idx in keep_positions
    ]
    truncated.append(
        {
            "role": "user",
            "content": TEXT2SQL_CONTEXT_TRUNCATION_NOTICE,
            "agent": agent_name,
            "message_type": "user",
        }
    )
    return truncated
