"""Ordered schema migrations. Migration functions must not commit transactions."""

import sqlite3
from collections.abc import Callable

from .db import StoreError, transaction


def _v1(conn: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE templates (
            template_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            doc TEXT NOT NULL CHECK (json_valid(doc)),
            name TEXT GENERATED ALWAYS AS (json_extract(doc, '$.name')) VIRTUAL,
            created_at TEXT GENERATED ALWAYS AS (json_extract(doc, '$.created_at')) VIRTUAL,
            PRIMARY KEY (template_id, version)
        )""",
        """CREATE TABLE snapshots (
            snapshot_id TEXT PRIMARY KEY NOT NULL,
            doc TEXT NOT NULL CHECK (json_valid(doc)),
            created_at TEXT GENERATED ALWAYS AS (json_extract(doc, '$.created_at')) VIRTUAL
        )""",
        """CREATE TABLE documents (
            document_id TEXT PRIMARY KEY NOT NULL,
            sha256 TEXT,
            source_file TEXT,
            path TEXT,
            snapshot_id TEXT REFERENCES snapshots(snapshot_id)
        )""",
        """CREATE TABLE batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            doc TEXT NOT NULL CHECK (json_valid(doc)),
            template_id TEXT GENERATED ALWAYS AS (json_extract(doc, '$.template.id')) VIRTUAL,
            template_version INTEGER GENERATED ALWAYS AS (json_extract(doc, '$.template.version')) VIRTUAL,
            created_at TEXT GENERATED ALWAYS AS (json_extract(doc, '$.created_at')) VIRTUAL,
            FOREIGN KEY (template_id, template_version) REFERENCES templates(template_id, version)
        )""",
        """CREATE TABLE results (
            result_id TEXT PRIMARY KEY NOT NULL,
            doc TEXT NOT NULL CHECK (json_valid(doc)),
            batch_id TEXT GENERATED ALWAYS AS (json_extract(doc, '$.batch_id')) VIRTUAL REFERENCES batches(batch_id),
            document_id TEXT GENERATED ALWAYS AS (json_extract(doc, '$.document_id')) VIRTUAL REFERENCES documents(document_id),
            status TEXT GENERATED ALWAYS AS (json_extract(doc, '$.status')) VIRTUAL,
            revision INTEGER GENERATED ALWAYS AS (json_extract(doc, '$.revision')) VIRTUAL,
            job INTEGER GENERATED ALWAYS AS (json_extract(doc, '$.job')) VIRTUAL,
            updated_at TEXT GENERATED ALWAYS AS (json_extract(doc, '$.updated_at')) VIRTUAL
        )""",
        "CREATE INDEX results_batch_id ON results(batch_id)",
        "CREATE INDEX results_status ON results(status)",
    )
    # executescript() would implicitly commit and break migration atomicity.
    for statement in statements:
        conn.execute(statement)


def _v2(conn: sqlite3.Connection) -> None:
    statements = (
        "CREATE INDEX documents_sha256 ON documents(sha256)",
        """ALTER TABLE templates ADD COLUMN version_label TEXT
            GENERATED ALWAYS AS (json_extract(doc, '$.version_label')) VIRTUAL""",
    )
    for statement in statements:
        conn.execute(statement)


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_v1, _v2]


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations, atomically advancing user_version for each one."""
    while True:
        # Read the version under the write lock, including when another connection
        # may have migrated the same database while this connection was waiting.
        with transaction(conn):
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise StoreError("Database schema is newer than supported")
            if version == len(MIGRATIONS):
                return
            MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version + 1}")
