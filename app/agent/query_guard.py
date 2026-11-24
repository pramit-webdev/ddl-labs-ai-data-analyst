"""Defense-in-depth guard for the LLM's read-only SQL tool."""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

from app.db import QUERYABLE_OBJECTS, Database, db as default_db


class SafeQueryError(Exception):
    pass


FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|"
    r"REINDEX|ANALYZE|TRANSACTION|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|TRIGGER)\b",
    re.IGNORECASE,
)
BLOCKED_REFS = re.compile(r"\b(meta|pipeline_runs|sqlite_master|sqlite_schema|sqlite_)\b", re.IGNORECASE)
DANGEROUS_FUNCTIONS = {"load_extension", "writefile", "readfile", "edit"}


def validate_sql(sql: str) -> str:
    raw = (sql or "").strip()
    if not raw:
        raise SafeQueryError("SQL query is empty.")
    if "--" in raw or "/*" in raw or "*/" in raw:
        raise SafeQueryError("SQL comments are not allowed.")
    if ";" in raw:
        raise SafeQueryError("Only a single SQL statement is allowed.")
    first = raw.split(None, 1)[0].upper()
    if first not in {"SELECT", "WITH"}:
        raise SafeQueryError("Only SELECT / WITH queries are allowed.")
    if FORBIDDEN.search(raw):
        raise SafeQueryError("Query contains a forbidden keyword.")
    if BLOCKED_REFS.search(raw):
        raise SafeQueryError("Query references a restricted object.")
    if not re.search(r"\bLIMIT\s+\d+", raw, re.IGNORECASE):
        raw = f"{raw} LIMIT 100"
    return raw


def _authorizer(action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None) -> int:
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        if (arg2 or "").lower() in DANGEROUS_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        if arg1 in QUERYABLE_OBJECTS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def run_safe_query(sql: str, database: Database | None = None) -> dict[str, Any]:
    database = database or default_db
    validated = validate_sql(sql)
    deadline = time.monotonic() + 2.0

    def progress() -> int:
        return 1 if time.monotonic() > deadline else 0

    try:
        with database.connection(readonly=True) as conn:
            conn.set_authorizer(_authorizer)
            conn.set_progress_handler(progress, 1000)
            cursor = conn.execute(validated)
            columns = [d[0] for d in (cursor.description or [])]
            rows = cursor.fetchmany(200)
            return {
                "columns": columns,
                "rows": [list(row) for row in rows],
                "row_count": len(rows),
                "truncated": len(rows) >= 200,
                "sql": validated,
            }
    except sqlite3.DatabaseError as exc:
        message = str(exc)
        if "not authorized" in message:
            raise SafeQueryError("Query touches an object that is not available to the analyst.") from exc
        if "interrupted" in message:
            raise SafeQueryError("Query exceeded the 2 second execution limit.") from exc
        raise SafeQueryError(f"SQL error: {message}") from exc
    except sqlite3.Error as exc:
        raise SafeQueryError(f"SQL error: {exc}") from exc
