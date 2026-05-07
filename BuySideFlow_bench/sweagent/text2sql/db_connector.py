from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import random
import re
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
from pymysql.cursors import DictCursor


# MySQL 8.0 reserved words that frequently appear as unquoted aliases in generated SQL
_MYSQL_RESERVED_WORDS = frozenset([
    "accessible", "add", "all", "alter", "analyze", "and", "as", "asc",
    "between", "bigint", "binary", "both", "by", "call", "cascade", "case",
    "change", "char", "character", "check", "collate", "column", "condition",
    "constraint", "continue", "convert", "create", "cross", "cube",
    "cume_dist", "cursor", "database", "databases", "dec", "decimal",
    "declare", "default", "delayed", "delete", "dense_rank", "desc",
    "describe", "deterministic", "distinct", "distinctrow", "div", "double",
    "drop", "dual", "each", "else", "elseif", "empty", "enclosed", "escaped",
    "except", "exists", "exit", "explain", "false", "fetch", "first_value",
    "float", "for", "force", "foreign", "from", "fulltext", "function",
    "generated", "get", "grant", "group", "grouping", "groups", "having",
    "if", "ignore", "in", "index", "infile", "inner", "inout", "insert",
    "int", "integer", "intersect", "interval", "into", "is", "iterate",
    "join", "json_table", "key", "keys", "kill", "lag", "last_value",
    "lateral", "lead", "leading", "leave", "left", "like", "limit",
    "linear", "lines", "load", "lock", "long", "loop", "match", "maxvalue",
    "mediumint", "mod", "modifies", "natural", "not", "nth_value", "ntile",
    "null", "numeric", "of", "on", "optimize", "option", "optionally",
    "or", "order", "out", "outer", "outfile", "over", "partition",
    "percent_rank", "precision", "primary", "procedure", "purge", "range",
    "rank", "read", "reads", "real", "recursive", "references", "regexp",
    "release", "rename", "repeat", "replace", "require", "restrict",
    "return", "revoke", "right", "rlike", "row", "rows", "row_number",
    "schema", "schemas", "select", "sensitive", "separator", "set", "show",
    "signal", "smallint", "specific", "sql", "sqlexception", "sqlstate",
    "sqlwarning", "ssl", "starting", "stored", "straight_join", "system",
    "table", "then", "tinyint", "to", "trailing", "trigger", "true",
    "undo", "union", "unique", "unlock", "unsigned", "update", "usage",
    "use", "using", "values", "varbinary", "varchar", "varying", "virtual",
    "when", "where", "while", "window", "with", "write", "xor", "zerofill",
])

_MYSQL_TYPE_NAMES = frozenset([
    "bigint", "binary", "blob", "char", "date", "datetime", "dec", "decimal",
    "double", "float", "int", "integer", "json", "longtext", "mediumint",
    "numeric", "real", "signed", "text", "time", "timestamp", "tinyint",
    "unsigned", "varchar",
])

# SQL statement keywords that should never be backtick-quoted even though they
# appear in _MYSQL_RESERVED_WORDS.  These can follow AS in DDL/DML syntax
# (e.g. CREATE TABLE … AS SELECT) and must not be treated as aliases.
_SQL_STATEMENT_KEYWORDS = frozenset([
    "select", "from", "where", "join", "inner", "outer", "left", "right",
    "cross", "on", "and", "or", "not", "in", "is", "null", "between",
    "exists", "case", "when", "then", "else", "end", "having", "group",
    "order", "by", "limit", "union", "all", "insert", "into", "update",
    "delete", "set", "values", "create", "drop", "alter", "table", "index",
    "if", "with", "as", "like", "distinct", "true", "false", "asc", "desc",
])

_TRANSIENT_MYSQL_ERROR_CODES = frozenset([1040, 1129, 2002, 2003, 2006, 2013])
_SHARED_CONNECTION_STATE = threading.local()


def _close_connection_quietly(connection) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except Exception:
        pass


def _current_shared_connection_holder() -> dict[str, Any] | None:
    stack = getattr(_SHARED_CONNECTION_STATE, "stack", None)
    if not stack:
        return None
    return stack[-1]


@contextmanager
def shared_connection_session():
    stack = getattr(_SHARED_CONNECTION_STATE, "stack", None)
    if stack is None:
        stack = []
        _SHARED_CONNECTION_STATE.stack = stack
    holder: dict[str, Any] = {"connection": None, "socket_timeout_seconds": 0}
    stack.append(holder)
    try:
        yield
    finally:
        stack.pop()
        _close_connection_quietly(holder.get("connection"))
        if not stack:
            try:
                delattr(_SHARED_CONNECTION_STATE, "stack")
            except AttributeError:
                pass


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _db_connect_min_interval_seconds() -> float:
    default = _get_float_env("TEXT2SQL_DB_CONNECT_MIN_INTERVAL_SEC", 0.0)
    return max(_get_float_env("DB_CONNECT_MIN_INTERVAL_SEC", default), 0.0)


def _db_connect_error_cooldown_seconds() -> float:
    return max(_get_float_env("DB_CONNECT_ERROR_COOLDOWN_SEC", 5.0), 0.0)


def _db_connect_burst_limit() -> int:
    return max(_get_int_env("DB_CONNECT_BURST_LIMIT", 30), 0)


def _db_connect_burst_window_seconds() -> float:
    return max(_get_float_env("DB_CONNECT_BURST_WINDOW_SEC", 10.0), 0.0)


def _db_connect_rate_limit_file() -> Path:
    explicit = os.getenv("DB_CONNECT_RATE_LIMIT_FILE", "").strip()
    if explicit:
        return Path(explicit)
    identity = "|".join(
        [
            os.getenv("DB_HOST", "localhost"),
            os.getenv("DB_PORT", "3306"),
            os.getenv("DB_NAME", "jydb"),
            os.getenv("DB_USER", "root"),
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8", errors="replace")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"text2sql_db_connect_{digest}.lock"


def _read_connection_state(handle) -> dict[str, Any]:
    try:
        handle.seek(0)
        text = handle.read().strip()
    except Exception:
        return {"next_allowed": 0.0, "recent": []}
    if not text:
        return {"next_allowed": 0.0, "recent": []}
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return {
                "next_allowed": float(payload.get("next_allowed") or 0.0),
                "recent": [float(item) for item in payload.get("recent", []) if isinstance(item, (int, float))],
            }
    except Exception:
        pass
    try:
        return {"next_allowed": float(text), "recent": []}
    except ValueError:
        return {"next_allowed": 0.0, "recent": []}


def _write_connection_state(handle, state: dict[str, Any]) -> None:
    payload = {
        "next_allowed": float(state.get("next_allowed") or 0.0),
        "recent": [float(item) for item in state.get("recent", [])],
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
    handle.flush()
    try:
        os.fsync(handle.fileno())
    except OSError:
        pass


def _read_next_connection_time(handle) -> float:
    return _read_connection_state(handle)["next_allowed"]


def _write_next_connection_time(handle, timestamp: float) -> None:
    state = _read_connection_state(handle)
    state["next_allowed"] = timestamp
    _write_connection_state(handle, state)


@contextmanager
def _connection_rate_limit_lock():
    path = _db_connect_rate_limit_file()
    lock_timeout = max(_get_float_env("DB_CONNECT_RATE_LIMIT_LOCK_TIMEOUT_SEC", 30.0), 0.0)
    handle = None
    locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
        try:
            if path.stat().st_size == 0:
                handle.write("0\n")
                handle.flush()
        except OSError:
            pass

        started = time.monotonic()
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() - started >= lock_timeout:
                    yield None
                    return
                time.sleep(0.05)

        yield handle
    except OSError:
        yield None
    finally:
        if handle is not None:
            if locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()


def _throttle_connection_attempt() -> None:
    interval = _db_connect_min_interval_seconds()
    burst_limit = _db_connect_burst_limit()
    burst_window = _db_connect_burst_window_seconds()
    if interval <= 0 and (burst_limit <= 0 or burst_window <= 0):
        return
    with _connection_rate_limit_lock() as handle:
        if handle is None:
            return
        now = time.time()
        state = _read_connection_state(handle)
        recent = [item for item in state.get("recent", []) if now - item <= burst_window]
        wait_until = float(state.get("next_allowed") or 0.0)
        if burst_limit > 0 and burst_window > 0 and len(recent) >= burst_limit:
            wait_until = max(wait_until, min(recent) + burst_window)
        wait_seconds = wait_until - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)
            now = time.time()
            recent = [item for item in recent if now - item <= burst_window]
        recent.append(now)
        state["recent"] = recent[-max(burst_limit, 1):] if burst_limit > 0 else []
        state["next_allowed"] = now + interval if interval > 0 else 0.0
        _write_connection_state(handle, state)


def _record_connection_cooldown(seconds: float | None = None) -> None:
    cooldown = _db_connect_error_cooldown_seconds() if seconds is None else max(seconds, 0.0)
    if cooldown <= 0:
        return
    with _connection_rate_limit_lock() as handle:
        if handle is None:
            return
        state = _read_connection_state(handle)
        state["next_allowed"] = max(float(state.get("next_allowed") or 0.0), time.time() + cooldown)
        _write_connection_state(handle, state)


def _resolve_socket_timeout_seconds(explicit_timeout: int | None = None) -> int:
    timeout_candidates = [
        _get_int_env("DB_READ_TIMEOUT_SEC", 60),
        _get_int_env("TEXT2SQL_EVAL_DB_TIMEOUT_SEC", 300) + 30,
    ]
    try:
        if explicit_timeout is not None and int(explicit_timeout) > 0:
            timeout_candidates.append(int(explicit_timeout) + 30)
    except (TypeError, ValueError):
        pass
    return max(timeout_candidates)


def _build_connection_config(timeout_seconds: int | None = None) -> dict[str, Any]:
    socket_timeout = _resolve_socket_timeout_seconds(timeout_seconds)
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "jydb"),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
        "connect_timeout": _get_int_env("DB_CONNECT_TIMEOUT_SEC", 60),
        "read_timeout": socket_timeout,
        "write_timeout": socket_timeout,
    }


def _shared_connection_for_query(timeout_seconds: int | None = None):
    holder = _current_shared_connection_holder()
    if holder is None:
        return None

    required_timeout = _resolve_socket_timeout_seconds(timeout_seconds)
    connection = holder.get("connection")
    current_timeout = int(holder.get("socket_timeout_seconds") or 0)
    needs_reconnect = connection is None or current_timeout < required_timeout

    if not needs_reconnect and hasattr(connection, "ping"):
        try:
            connection.ping(reconnect=False)
        except Exception:
            needs_reconnect = True

    if needs_reconnect:
        _close_connection_quietly(connection)
        connection = _get_connection(timeout_seconds=timeout_seconds)
        holder["connection"] = connection
        holder["socket_timeout_seconds"] = required_timeout

    return connection


def _is_retryable_mysql_error(exc: BaseException) -> bool:
    if not isinstance(exc, pymysql.MySQLError):
        return False
    args = getattr(exc, "args", ())
    code = args[0] if args else None
    return code in _TRANSIENT_MYSQL_ERROR_CODES


def _replace_interval_literal(match: re.Match) -> str:
    amount = match.group(1)
    unit = match.group(2).upper()
    if unit.endswith("S"):
        unit = unit[:-1]
    return f"INTERVAL {amount} {unit}"


def _replace_make_date(sql: str) -> str:
    """Replace make_date(...) calls with STR_TO_DATE(...), handling nested parens."""
    pattern = re.compile(r"\bmake_date\s*\(", re.IGNORECASE)
    result = []
    last_end = 0
    for match in pattern.finditer(sql):
        start = match.start()
        result.append(sql[last_end:start])
        i = match.end()  # position right after '('
        depth = 1
        while i < len(sql) and depth > 0:
            if sql[i] == '(':
                depth += 1
            elif sql[i] == ')':
                depth -= 1
            i += 1
        # i now points after the closing ')'
        args_str = sql[match.end():i - 1]
        args = []
        arg_start = 0
        depth = 0
        for idx, ch in enumerate(args_str):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                args.append(args_str[arg_start:idx].strip())
                arg_start = idx + 1
        args.append(args_str[arg_start:].strip())
        if len(args) == 3:
            replacement = (
                f"STR_TO_DATE(CONCAT({args[0]}, '-', LPAD({args[1]}, 2, '0'), '-', "
                f"LPAD({args[2]}, 2, '0')), '%Y-%m-%d')"
            )
            result.append(replacement)
        else:
            # Unrecognized argument count — leave as-is to avoid corrupting SQL
            result.append(sql[match.start():i])
        last_end = i
    result.append(sql[last_end:])
    return "".join(result)


def _normalize_sql_for_mysql(sql: str) -> str:
    """Best-effort compatibility rewrites for reference SQL written in PostgreSQL style."""

    # DATE '2025-02-28' -> DATE('2025-02-28')
    sql = re.sub(r"\bDATE\s+'(\d{4}-\d{2}-\d{2})'", r"DATE('\1')", sql, flags=re.IGNORECASE)

    # to_char(date_expr, 'MM-DD') -> DATE_FORMAT(date_expr, '%m-%d')
    sql = re.sub(
        r"\bto_char\s*\(\s*([^,]+?)\s*,\s*'MM-DD'\s*\)",
        r"DATE_FORMAT(\1, '%m-%d')",
        sql,
        flags=re.IGNORECASE,
    )

    # INTERVAL '3 months' -> INTERVAL 3 MONTH
    sql = re.sub(
        r"\bINTERVAL\s*'(\d+)\s+(day|days|month|months|year|years)'",
        _replace_interval_literal,
        sql,
        flags=re.IGNORECASE,
    )

    # Common PostgreSQL casts used in benchmark reference SQL.
    cast_replacements = [
        (r"\bNULL\s*::\s*numeric\b", "CAST(NULL AS DECIMAL(38, 10))"),
        (r"\bNULL\s*::\s*date\b", "CAST(NULL AS DATE)"),
        (r"(EXTRACT\([^()]+\))\s*::\s*int\b", r"CAST(\1 AS SIGNED)"),
        (r"(EXTRACT\([^()]+\))\s*::\s*integer\b", r"CAST(\1 AS SIGNED)"),
        (r"(\b[A-Za-z_][\w\.]*\b)\s*::\s*date\b", r"CAST(\1 AS DATE)"),
        (r"(\b[A-Za-z_][\w\.]*\b)\s*::\s*int\b", r"CAST(\1 AS SIGNED)"),
        (r"(\b[A-Za-z_][\w\.]*\b)\s*::\s*integer\b", r"CAST(\1 AS SIGNED)"),
        (r"(\b[A-Za-z_][\w\.]*\b)\s*::\s*numeric\b", r"CAST(\1 AS DECIMAL(38, 10))"),
        (r"\(([^()]+)\)\s*::\s*date\b", r"CAST((\1) AS DATE)"),
        (r"\(([^()]+)\)\s*::\s*numeric\b", r"CAST((\1) AS DECIMAL(38, 10))"),
        (r"\(([^()]+)\)\s*::\s*int\b", r"CAST((\1) AS SIGNED)"),
    ]
    for pattern, replacement in cast_replacements:
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

    # make_date(year_expr, month_expr, day_expr) -> STR_TO_DATE(...)
    sql = _replace_make_date(sql)
    return sql


def _patch_sql_aliases(sql: str) -> str:
    """Backtick-quote reserved words used as identifiers to prevent MySQL syntax errors.

    Handles three patterns:
    1. Column/table aliases:  SELECT x AS of  ->  SELECT x AS `of`
    2. CTE names:             WITH of AS (    ->  WITH `of` AS (
                              , of AS (       ->  , `of` AS (
    3. Qualified references:  of.INNERCODE    ->  `of`.INNERCODE
    """

    def _q(word: str) -> str:
        lower_word = word.lower()
        if lower_word in _MYSQL_TYPE_NAMES or lower_word in _SQL_STATEMENT_KEYWORDS:
            return word
        return f"`{word}`" if lower_word in _MYSQL_RESERVED_WORDS else word

    def _alias(match: re.Match) -> str:
        return f"AS {_q(match.group(1))}"

    def _cte(match: re.Match) -> str:
        prefix, name, suffix = match.group(1), match.group(2), match.group(3)
        return f"{prefix}{_q(name)}{suffix}"

    def _ref(match: re.Match) -> str:
        name, dot = match.group(1), match.group(2)
        return f"{_q(name)}{dot}"

    sql = re.sub(r"\bAS\s+([a-zA-Z_]\w*)\b(?!\s*\()", _alias, sql, flags=re.IGNORECASE)
    sql = re.sub(r"((?:WITH|,)\s+)([a-zA-Z_]\w*)(\s+AS\s*\()", _cte, sql, flags=re.IGNORECASE)
    sql = re.sub(r"\b([a-zA-Z_]\w*)(\s*\.)", _ref, sql, flags=re.IGNORECASE)
    return sql


def _convert_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def get_connection():
    return _get_connection()


def _get_connection(timeout_seconds: int | None = None):
    _throttle_connection_attempt()
    return pymysql.connect(**_build_connection_config(timeout_seconds))


def _configure_session(cursor, timeout_seconds: int | None = None) -> None:
    sql_mode = os.getenv("DB_SQL_MODE", "")
    cursor.execute("SET SESSION sql_mode=%s", (sql_mode,))

    max_execution_ms = _get_int_env("DB_MAX_EXECUTION_TIME_MS", 60000)
    if timeout_seconds is not None:
        try:
            timeout_seconds = int(timeout_seconds)
        except (TypeError, ValueError):
            timeout_seconds = None
        if timeout_seconds is not None and timeout_seconds > 0:
            max_execution_ms = timeout_seconds * 1000
    if max_execution_ms > 0:
        cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={max_execution_ms}")

    lock_wait_seconds = _get_int_env("DB_LOCK_WAIT_TIMEOUT_SEC", 15)
    if lock_wait_seconds > 0:
        cursor.execute(f"SET SESSION innodb_lock_wait_timeout={lock_wait_seconds}")


def _execute_query(
    sql: str,
    params=None,
    max_rows: int = 0,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    normalized_sql = _patch_sql_aliases(_normalize_sql_for_mysql(sql))
    connection = None
    uses_shared_connection = _current_shared_connection_holder() is not None
    snippet = normalized_sql.strip().replace("\n", " ")
    snippet = snippet[:240] + ("..." if len(snippet) > 240 else "")
    max_attempts = max(_get_int_env("DB_CONNECT_RETRIES", 4), 0) + 1
    base_delay = max(_get_float_env("DB_CONNECT_RETRY_DELAY_SEC", 1.0), 0.0)
    last_exc: pymysql.MySQLError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if uses_shared_connection:
                connection = _shared_connection_for_query(timeout_seconds=timeout)
            else:
                connection = _get_connection(timeout_seconds=timeout)
            with connection.cursor() as cursor:
                _configure_session(cursor, timeout_seconds=timeout)
                cursor.execute(normalized_sql, params)
                rows = cursor.fetchall() if max_rows == 0 else cursor.fetchmany(max_rows)
                return list(rows)
        except pymysql.MySQLError as exc:
            last_exc = exc
            if uses_shared_connection:
                holder = _current_shared_connection_holder()
                if holder is not None:
                    _close_connection_quietly(holder.get("connection"))
                    holder["connection"] = None
                    holder["socket_timeout_seconds"] = 0
            if attempt >= max_attempts or not _is_retryable_mysql_error(exc):
                raise RuntimeError(f"MySQL execution failed: {exc}. SQL snippet: {snippet}") from exc
            _record_connection_cooldown()
            # Exponential backoff with jitter to avoid thundering herd across parallel workers
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
            time.sleep(delay)
        finally:
            if connection is not None and not uses_shared_connection:
                connection.close()
                connection = None

    raise RuntimeError(f"MySQL execution failed: {last_exc}. SQL snippet: {snippet}")


def query_to_dataframe(
    sql: str,
    max_rows: int = 0,
    timeout: int | None = None,
    **_: Any,
) -> pd.DataFrame:
    rows = _execute_query(sql, max_rows=max_rows, timeout=timeout)
    normalized_rows = [{key: _convert_value(value) for key, value in row.items()} for row in rows]
    return pd.DataFrame(normalized_rows)


class _ConnectionContextManager:
    """Context manager returned by _DbConnector.connection().
    Provides a raw pymysql connection; the connector methods on _DbConnector
    are preferred, but some reference code uses 'with DB.connection() as conn:'.
    """
    def __init__(self, timeout_seconds: int | None = None):
        self._timeout_seconds = timeout_seconds
        self._conn = None
        self._owns_connection = False

    def __enter__(self):
        shared_conn = _shared_connection_for_query(self._timeout_seconds)
        if shared_conn is not None:
            self._conn = shared_conn
            self._owns_connection = False
            return self._conn
        self._conn = _get_connection(self._timeout_seconds)
        self._owns_connection = True
        return self._conn

    def __exit__(self, *args):
        if self._owns_connection:
            _close_connection_quietly(self._conn)


class _DbConnector:
    """Compatibility shim for reference code that uses get_db_connector()."""

    def execute_sql_to_dataframe(
        self,
        sql: str,
        params=None,
        max_rows: int = 0,
        timeout: int | None = None,
        **_: Any,
    ) -> pd.DataFrame:
        rows = _execute_query(sql, params=params, max_rows=max_rows, timeout=timeout)
        normalized_rows = [{key: _convert_value(value) for key, value in row.items()} for row in rows]
        return pd.DataFrame(normalized_rows)

    def query(
        self,
        sql: str,
        params=None,
        max_rows: int = 0,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        return self.execute_sql_to_dataframe(sql, params=params, max_rows=max_rows, timeout=timeout, **kwargs)

    def execute(
        self,
        sql: str,
        params=None,
        max_rows: int = 0,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        return self.execute_sql_to_dataframe(sql, params=params, max_rows=max_rows, timeout=timeout, **kwargs)

    def get_dataframe(
        self,
        sql: str,
        params=None,
        max_rows: int = 0,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        return self.execute_sql_to_dataframe(sql, params=params, max_rows=max_rows, timeout=timeout, **kwargs)

    def fetchall(self, sql: str, params=None) -> list:
        df = self.execute_sql_to_dataframe(sql, params=params)
        return df.to_dict("records")

    def connection(self):
        """Return a raw pymysql connection context manager."""
        return _ConnectionContextManager()

    def _resolve_conn_sql(self, conn_or_sql, sql_or_none):
        """Resolve (conn, sql) or (sql, ...) call signatures."""
        if isinstance(conn_or_sql, str):
            return None, conn_or_sql
        else:
            return conn_or_sql, (sql_or_none or "")

    def _exec_on_conn(self, conn, sql: str, max_rows: int = 0, timeout: int | None = None) -> pd.DataFrame:
        """Execute SQL on an existing connection, respecting temporary tables."""
        if conn is None:
            return self.execute_sql_to_dataframe(sql, max_rows=max_rows, timeout=timeout)
        normalized_sql = _patch_sql_aliases(_normalize_sql_for_mysql(sql))
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                _configure_session(cursor, timeout_seconds=timeout)
                cursor.execute(normalized_sql)
                if cursor.description is None:
                    return pd.DataFrame()
                rows = cursor.fetchall() if max_rows == 0 else cursor.fetchmany(max_rows)
                normalized = [{k: _convert_value(v) for k, v in row.items()} for row in rows]
                return pd.DataFrame(normalized)
        except Exception as exc:
            raise RuntimeError(f"MySQL execution failed: {exc}") from exc

    def execute_query_on_connection(
        self,
        conn_or_sql,
        sql_or_none=None,
        params=None,
        max_rows: int = 0,
        timeout: int | None = None,
        **kwargs: Any,
    ):
        conn, sql = self._resolve_conn_sql(conn_or_sql, sql_or_none)
        # Some reference code checks result["success"] — return dict in that case
        # by detecting non-SELECT statements
        stripped = sql.strip().upper()
        is_ddl = any(stripped.startswith(k) for k in ("CREATE", "DROP", "ALTER", "INSERT", "UPDATE", "DELETE"))
        try:
            df = self._exec_on_conn(conn, sql, max_rows=max_rows, timeout=timeout)
            if is_ddl:
                return {"success": True, "error": None}
            return df
        except Exception as exc:
            if is_ddl:
                return {"success": False, "error": str(exc)}
            raise

    def execute_sql_to_dataframe_on_connection(
        self,
        conn_or_sql,
        sql_or_none=None,
        params=None,
        max_rows: int = 0,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        conn, sql = self._resolve_conn_sql(conn_or_sql, sql_or_none)
        return self._exec_on_conn(conn, sql, max_rows=max_rows, timeout=timeout)


def get_db_connector() -> _DbConnector:
    """Return a database connector compatible with reference code patterns."""
    return _DbConnector()
