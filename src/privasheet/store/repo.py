"""Repository operations for stored JSON documents."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .db import StoreError, dumps, loads, transaction


def _load_one(row: sqlite3.Row | None) -> Any | None:
    return None if row is None else loads(row["doc"])


def _with_next_revision(doc: dict, expected_revision: int) -> dict:
    return {**doc, "revision": expected_revision + 1}


def _document_row(row: sqlite3.Row) -> dict:
    return {
        "document_id": row["document_id"],
        "sha256": row["sha256"],
        "source_file": row["source_file"],
        "path": row["path"],
        "snapshot_id": row["snapshot_id"],
    }


def _manifest_entry(document: dict, result_by_document_id: dict[str, dict]) -> dict:
    result = result_by_document_id[document["document_id"]]
    return {
        "document_id": document["document_id"],
        "result_id": result["result_id"],
        "source_file": document.get("source_file"),
    }


def _batch_manifest(conn: sqlite3.Connection, batch_id: str) -> dict:
    row = conn.execute(
        "SELECT doc FROM batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise StoreError("batch not found")
    return loads(row["doc"])


def _result_for_document(
    conn: sqlite3.Connection, batch_id: str, document_id: str
) -> dict | None:
    row = conn.execute(
        "SELECT doc FROM results WHERE batch_id = ? AND document_id = ?",
        (batch_id, document_id),
    ).fetchone()
    return _load_one(row)


def _template_uses_sample_document(conn: sqlite3.Connection, document_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM templates "
            "WHERE json_extract(doc, '$.sample_document_id') = ? LIMIT 1",
            (document_id,),
        ).fetchone()
        is not None
    )


def _snapshot_image_paths(snapshot: dict | None) -> list[str]:
    if snapshot is None:
        return []
    return [
        page["image"]
        for page in snapshot.get("pages", [])
        if isinstance(page, dict) and page.get("image")
    ]


def _snapshot_is_referenced(conn: sqlite3.Connection, snapshot_id: str) -> bool:
    checks = (
        (
            "SELECT 1 FROM documents WHERE snapshot_id = ? LIMIT 1",
            (snapshot_id,),
        ),
        (
            (
                "SELECT 1 FROM results "
                "WHERE json_extract(doc, '$.snapshot_id') = ? LIMIT 1"
            ),
            (snapshot_id,),
        ),
        (
            (
                "SELECT 1 FROM templates "
                "WHERE json_extract(doc, '$.sample_snapshot_id') = ? LIMIT 1"
            ),
            (snapshot_id,),
        ),
    )
    return any(
        conn.execute(sql, params).fetchone() is not None for sql, params in checks
    )


def _remove_from_manifest(manifest: dict, document_id: str) -> dict:
    return {
        **manifest,
        "documents": [
            doc
            for doc in manifest.get("documents", [])
            if doc.get("document_id") != document_id
        ],
    }


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


def find_documents_by_sha256(
    conn: sqlite3.Connection, sha256: str, before_document_id: str | None = None
) -> list[dict]:
    """Return documents with a matching hash, optionally before a document id."""
    if before_document_id is None:
        rows = conn.execute(
            "SELECT document_id, sha256, source_file, path, snapshot_id "
            "FROM documents WHERE sha256 = ? ORDER BY document_id ASC",
            (sha256,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT document_id, sha256, source_file, path, snapshot_id "
            "FROM documents WHERE sha256 = ? AND document_id < ? "
            "ORDER BY document_id ASC",
            (sha256, before_document_id),
        ).fetchall()
    return [_document_row(row) for row in rows]


def add_documents_to_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    documents: list[dict],
    results: list[dict],
) -> None:
    """Append document rows, manifest entries, and queued results atomically."""
    result_by_document_id = {result["document_id"]: result for result in results}
    entries = [
        _manifest_entry(document, result_by_document_id) for document in documents
    ]
    with transaction(conn):
        manifest = _batch_manifest(conn, batch_id)
        manifest = {
            **manifest,
            "documents": [*manifest.get("documents", []), *entries],
        }
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
            "UPDATE batches SET doc = ? WHERE batch_id = ?",
            (dumps(manifest), batch_id),
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
        "UPDATE results SET doc = ? WHERE result_id = ? AND revision = ? AND job = ?",
        (
            dumps(_with_next_revision(doc, expected_revision)),
            result_id,
            expected_revision,
            expected_job,
        ),
    )
    return cursor.rowcount == 1


def update_document_path(conn: sqlite3.Connection, document_id: str, path: str) -> None:
    """Update the stored path after moving a document file."""
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE documents SET path = ? WHERE document_id = ?",
            (path, document_id),
        )
        if cursor.rowcount != 1:
            raise StoreError("document not found")


def remove_queued_document(
    conn: sqlite3.Connection, batch_id: str, document_id: str
) -> str | None:
    """Remove one queued document from a batch and return its file path."""
    with transaction(conn):
        manifest = _batch_manifest(conn, batch_id)
        result = _result_for_document(conn, batch_id, document_id)
        if result is None:
            raise StoreError("result not found")
        if result.get("status") != "queued":
            raise StoreError("document is locked")
        document = conn.execute(
            "SELECT document_id, sha256, source_file, path, snapshot_id "
            "FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if document is None:
            raise StoreError("document not found")
        conn.execute("DELETE FROM results WHERE result_id = ?", (result["result_id"],))
        conn.execute(
            "UPDATE batches SET doc = ? WHERE batch_id = ?",
            (dumps(_remove_from_manifest(manifest, document_id)), batch_id),
        )
        conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        return document["path"]


def delete_document(conn: sqlite3.Connection, document_id: str) -> list[str]:
    """Delete one document and unreferenced files, refusing active processing."""
    paths: list[str] = []
    with transaction(conn):
        row = conn.execute(
            "SELECT document_id, sha256, source_file, path, snapshot_id "
            "FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            raise StoreError("document not found")
        document = _document_row(row)
        result_row = conn.execute(
            "SELECT result_id, doc FROM results WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        result = _load_one(result_row)
        if result is not None and result.get("status") == "processing":
            raise StoreError("document is processing")

        if result is not None:
            manifest = _batch_manifest(conn, result["batch_id"])
            conn.execute(
                "DELETE FROM results WHERE result_id = ?", (result["result_id"],)
            )
            conn.execute(
                "UPDATE batches SET doc = ? WHERE batch_id = ?",
                (
                    dumps(_remove_from_manifest(manifest, document_id)),
                    result["batch_id"],
                ),
            )

        if not _template_uses_sample_document(conn, document_id):
            conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
            if document["path"]:
                paths.append(document["path"])

        snapshot_id = document.get("snapshot_id")
        if snapshot_id and not _snapshot_is_referenced(conn, snapshot_id):
            snapshot = get_snapshot(conn, snapshot_id)
            conn.execute("DELETE FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
            paths.extend(_snapshot_image_paths(snapshot))
    return paths


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
