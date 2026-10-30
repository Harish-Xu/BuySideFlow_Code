from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .upstream import ensure_upstream_importable


ensure_upstream_importable()

from spider_agent.agent.action import Action, Terminate
from sweagent.text2sql.runtime import (
    parse_describe_tables_action,
    parse_get_columns_action,
    parse_get_trading_days_action,
    parse_run_code_action,
    parse_schema_request_action,
    parse_search_columns_action,
    parse_search_tables_action,
    parse_submit_action,
)


@dataclass
class RunCode(Action):
    payload: str
    action_type: str = field(default="run_code", init=False, repr=False)

    @classmethod
    def get_action_description(cls) -> str:
        return """
## run_code
* Signature:
run_code
<JSON payload>
END_RUN_CODE
* Description: Execute a complete candidate answer against JYDB. The JSON object must contain mode, sql_code, python_code, and result_vars. A successful execution is required before submit.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        payload = parse_run_code_action(text)
        return cls(payload=payload) if payload is not None else None

    def __repr__(self) -> str:
        return f"run_code\n{self.payload}\nEND_RUN_CODE"


@dataclass
class Submit(Terminate):
    action_type: str = field(default="submit", init=False, repr=False)

    @property
    def payload(self) -> str:
        return self.output or ""

    @classmethod
    def get_action_description(cls) -> str:
        return """
## submit
* Signature:
submit
<JSON payload>
END_SUBMIT
* Description: Submit the final JSON payload after a successful run_code. Submission ends the task only when accepted.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        payload = parse_submit_action(text)
        return cls(output=payload) if payload is not None else None

    def __repr__(self) -> str:
        return f"submit\n{self.payload}\nEND_SUBMIT"


@dataclass
class DescribeTables(Action):
    tables: list[str]
    action_type: str = field(default="describe_tables", init=False, repr=False)

    @classmethod
    def get_action_description(cls) -> str:
        return """
## describe_tables
* Signature: describe_tables <table1>[,<table2>...]
* Description: Return table descriptions from the shared JYDB catalog.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        tables = parse_describe_tables_action(text)
        return cls(tables=tables) if tables is not None else None

    def __repr__(self) -> str:
        return "describe_tables " + ",".join(self.tables)


@dataclass
class SearchTables(Action):
    keywords: list[str]
    action_type: str = field(default="search_tables", init=False, repr=False)

    @classmethod
    def get_action_description(cls) -> str:
        return """
## search_tables
* Signature: search_tables <keyword1>[,<keyword2>...]
* Description: Search JYDB table names and descriptions.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        keywords = parse_search_tables_action(text)
        return cls(keywords=keywords) if keywords is not None else None

    def __repr__(self) -> str:
        return "search_tables " + ",".join(self.keywords)


@dataclass
class GetColumns(Action):
    table_name: str
    action_type: str = field(default="get_columns", init=False, repr=False)

    @classmethod
    def get_action_description(cls) -> str:
        return """
## get_columns
* Signature: get_columns <table_name>
* Description: List all columns and comments for one JYDB table.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        table_name = parse_get_columns_action(text)
        return cls(table_name=table_name) if table_name is not None else None

    def __repr__(self) -> str:
        return f"get_columns {self.table_name}"


@dataclass
class SearchColumns(Action):
    keywords: list[str]
    table_scope: list[str]
    action_type: str = field(default="search_columns", init=False, repr=False)

    @classmethod
    def get_action_description(cls) -> str:
        return """
## search_columns
* Signature: search_columns <keyword1>[,<keyword2>...] [in <table1>[,<table2>...]]
* Description: Search JYDB column names and comments, optionally within selected tables.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        parsed = parse_search_columns_action(text)
        return cls(keywords=parsed[0], table_scope=parsed[1]) if parsed is not None else None

    def __repr__(self) -> str:
        scope = f" in {','.join(self.table_scope)}" if self.table_scope else ""
        return "search_columns " + ",".join(self.keywords) + scope


@dataclass
class RequestSchema(Action):
    tables: list[str]
    action_type: str = field(default="request_schema", init=False, repr=False)

    @classmethod
    def get_action_description(cls) -> str:
        return """
## request_schema
* Signature: request_schema <table1>[,<table2>...]
* Description: Return the full shared schema for selected JYDB tables.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        tables = parse_schema_request_action(text)
        return cls(tables=tables) if tables is not None else None

    def __repr__(self) -> str:
        return "request_schema " + ",".join(self.tables)


@dataclass
class GetTradingDays(Action):
    start_date: str
    end_date: str
    markets: str = "83,90"
    action_type: str = field(default="get_trading_days", init=False, repr=False)

    @classmethod
    def get_action_description(cls) -> str:
        return """
## get_trading_days
* Signature: get_trading_days <YYYY-MM-DD> <YYYY-MM-DD> [market=83,90]
* Description: Query the exact JYDB trading-day calendar for the requested range.
"""

    @classmethod
    def parse_action_from_text(cls, text: str) -> Optional[Action]:
        parsed = parse_get_trading_days_action(text)
        return cls(start_date=parsed[0], end_date=parsed[1], markets=parsed[2]) if parsed is not None else None

    def __repr__(self) -> str:
        return f"get_trading_days {self.start_date} {self.end_date} market={self.markets}"


ACTION_CLASSES = [
    DescribeTables,
    SearchTables,
    GetColumns,
    SearchColumns,
    RequestSchema,
    GetTradingDays,
    RunCode,
    Submit,
]
