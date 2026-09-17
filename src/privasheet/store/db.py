"""SQLite connections and explicit transaction boundaries."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class StoreError(RuntimeError):
    """The database cannot be used by this application."""


def dumps(doc: Any) -> str:
    """Serialize a document as Unicode JSON, rejecting non-finite numbers."""
    return json.dumps(doc, ensure_ascii=False, allow_nan=False)


def loads(text: str) -> Any:
    """Parse a stored JSON document."""
    return json.loads(text)


def open_db(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open a connection owned by the calling thread; migrations are explicit."""
    if tuple(map(int, sqlite3.sqlite_version.split("."))) < (3, 38, 0):
        raise StoreError("SQLite 3.38 or newer is required")
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT json_valid('{}'), json_extract('{\"value\":1}', '$.value')"
            ).fetchone()
            if tuple(row) != (1, 1):
                raise StoreError("SQLite JSON functions are unavailable")
        except sqlite3.DatabaseError as exc:
            raise StoreError("SQLite JSON functions are unavailable") from exc
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one explicit write transaction; nested transactions are unsupported."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
