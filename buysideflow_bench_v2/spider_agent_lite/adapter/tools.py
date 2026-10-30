"""Names of the BuySideFlow runtime contracts exposed by the future bridge.

The implementations remain in ``sweagent.text2sql.runtime``. This module is an
explicit boundary declaration; it does not create a second executor.
"""

TOOL_CONTRACTS = {
    "describe_tables": "Return descriptions for selected JYDB tables.",
    "search_tables": "Search the shared JYDB table catalog.",
    "get_columns": "Return all columns and comments for one JYDB table.",
    "search_columns": "Search JYDB columns by name or comment.",
    "request_schema": "Return the full schema for selected JYDB tables.",
    "get_trading_days": "Query the exact JYDB trading-day calendar.",
    "run_code": "Execute and verify a complete candidate submission.",
    "submit": "Submit the exact candidate most recently verified by run_code.",
}
