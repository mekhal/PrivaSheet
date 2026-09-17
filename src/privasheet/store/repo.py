"""Repository operations for stored JSON documents."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .db import dumps, loads, transaction


def _load_one(row: sqlite3.Row | None) -> Any | None:
    return None if row is None else loads(row["doc"])


def _with_next_revision(doc: dict, expected_revision: int) -> dict:
    return {**doc, "revision": expected_revision + 1}


def insert_template(conn: sqlite3.Connection, doc: dict) -> None:
    """Insert a template version. Duplicate (template_id, version) raises."""
    with transaction(conn):
        conn.execute(
            "INSERT INTO templates(template_id, version, doc) VALUES (?, ?, ?)",
            (doc["template_id"], doc["version"], dumps(doc)),
        )


def get_template(
    conn: sqlite3.Connection, template_id: str, version: int
) -> dict | None:
    """Return one template version, or None when absent."""
    row = conn.execute(
        "SELECT doc FROM templates WHERE template_id = ? AND version = ?",
        (template_id, version),
    ).fetchone()
    return _load_one(row)


def latest_template(conn: sqlite3.Connection, template_id: str) -> dict | None:
    """Return the highest version for a template id, or None when absent."""
    row = conn.execute(
        "SELECT doc FROM templates WHERE template_id = ? ORDER BY version DESC LIMIT 1",
        (template_id,),
    ).fetchone()
    return _load_one(row)


def list_templates(conn: sqlite3.Connection) -> list[dict]:
    """Return every stored template, grouped by id and newest version first."""
    rows = conn.execute(
        "SELECT doc FROM templates ORDER BY template_id ASC, version DESC"
    ).fetchall()
    return [loads(row["doc"]) for row in rows]


def insert_snapshot(conn: sqlite3.Connection, doc: dict) -> None:
    """Insert an immutable OCR snapshot, ignoring an existing identical row."""
    encoded = dumps(doc)
    with transaction(conn):
        cursor = conn.execute(
            "INSERT OR IGNORE INTO snapshots(snapshot_id, doc) VALUES (?, ?)",
            (doc["snapshot_id"], encoded),
        )
        if cursor.rowcount:
            return
        row = conn.execute(
            "SELECT doc FROM snapshots WHERE snapshot_id = ?", (doc["snapshot_id"],)
        ).fetchone()
        if row is None or loads(row["doc"]) != doc:
            raise sqlite3.IntegrityError(
                "snapshot_id already exists with different document"
            )


def get_snapshot(conn: sqlite3.Connection, snapshot_id: str) -> dict | None:
    """Return one OCR snapshot, or None when absent."""
    row = conn.execute(
        "SELECT doc FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()
    return _load_one(row)


def create_batch(
    conn: sqlite3.Connection,
    manifest: dict,
    documents: list[dict],
    results: list[dict],
) -> None:
    """Insert document rows, queued results, and the batch manifest atomically."""
    with transaction(conn):
        conn.executemany(
            "INSERT INTO documents("
            "document_id, sha256, source_file, path, snapshot_id"
            ") VALUES (?, ?, ?, ?, ?)",
            [
                (
                    doc["document_id"],
                    doc.get("sha256"),
                    doc.get("source_file"),
                    doc.get("path"),
                    doc.get("snapshot_id"),
                )
                for doc in documents
            ],
        )
        conn.execute(
            "INSERT INTO batches(batch_id, doc) VALUES (?, ?)",
            (manifest["batch_id"], dumps(manifest)),
        )
        conn.executemany(
            "INSERT INTO results(result_id, doc) VALUES (?, ?)",
            [(doc["result_id"], dumps(doc)) for doc in results],
        )


def get_result(conn: sqlite3.Connection, result_id: str) -> dict | None:
    """Return one result document, or None when absent."""
    row = conn.execute(
        "SELECT doc FROM results WHERE result_id = ?", (result_id,)
    ).fetchone()
    return _load_one(row)


def list_results(conn: sqlite3.Connection, batch_id: str) -> list[dict]:
    """Return batch results in the manifest's document order."""
    row = conn.execute(
        "SELECT doc FROM batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        return []
    manifest = loads(row["doc"])
    result_ids = [doc["result_id"] for doc in manifest.get("documents", [])]
    if not result_ids:
        return []
    placeholders = ",".join("?" for _ in result_ids)
    rows = conn.execute(
        f"SELECT result_id, doc FROM results WHERE result_id IN ({placeholders})",
        result_ids,
    ).fetchall()
    by_id = {row["result_id"]: loads(row["doc"]) for row in rows}
    return [by_id[result_id] for result_id in result_ids]


def update_result_review(
    conn: sqlite3.Connection,
    result_id: str,
    expected_revision: int,
    doc: dict,
) -> bool:
    """Replace a result from a review save when the expected revision matches."""
    cursor = conn.execute(
        "UPDATE results SET doc = ? WHERE result_id = ? AND revision = ?",
        (
            dumps(_with_next_revision(doc, expected_revision)),
            result_id,
            expected_revision,
        ),
    )
    return cursor.rowcount == 1


def commit_worker_outcome(
    conn: sqlite3.Connection,
    result_id: str,
    expected_revision: int,
    expected_job: int,
    doc: dict,
) -> bool:
    """Replace a result from a worker outcome when revision and job match."""
    cursor = conn.execute(
        "UPDATE results SET doc = ? "
        "WHERE result_id = ? AND revision = ? AND job = ?",
        (
            dumps(_with_next_revision(doc, expected_revision)),
            result_id,
            expected_revision,
            expected_job,
        ),
    )
    return cursor.rowcount == 1


@contextmanager
def read_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a group of reads against one consistent SQLite snapshot."""
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
