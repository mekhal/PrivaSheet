"""Repository operations over the SQLite JSON document store."""

import sqlite3

import pytest

from privasheet.store import StoreError, dumps, loads, migrate, open_db
from privasheet.store.repo import (
    add_documents_to_batch,
    commit_worker_outcome,
    create_batch,
    delete_document,
    find_documents_by_sha256,
    get_result,
    get_snapshot,
    get_template,
    insert_snapshot,
    insert_template,
    latest_template,
    list_results,
    list_templates,
    read_transaction,
    remove_queued_document,
    update_document_path,
    update_result_review,
)


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "store.db")
    migrate(connection)
    yield connection
    connection.close()


def template_doc(template_id="invoice-a", version=1, name="Invoice A"):
    return {
        "template_id": template_id,
        "version": version,
        "version_label": f"1.{version - 1}.20260917",
        "name": name,
        "created_at": f"2026-09-17T00:00:0{version}Z",
        "fields": [],
        "tables": [],
        "match": {"min_key_label_ratio": 1},
    }


def snapshot_doc(snapshot_id="sha256:s1"):
    return {
        "snapshot_id": snapshot_id,
        "created_at": "2026-09-17T00:01:00Z",
        "pages": [{"image": "pages/s1/page-1.png", "boxes": []}],
    }


def manifest_doc(batch_id="bat_1", documents=None):
    documents = documents if documents is not None else []
    return {
        "batch_id": batch_id,
        "created_at": "2026-09-17T00:02:00Z",
        "template": {"id": "invoice-a", "version": 1},
        "documents": documents,
    }


def document_doc(document_id, result_id, source_file, snapshot_id="sha256:s1"):
    return {
        "document_id": document_id,
        "result_id": result_id,
        "sha256": f"hash-{document_id}",
        "source_file": source_file,
        "path": f"documents/{source_file}",
        "snapshot_id": snapshot_id,
    }


def result_doc(result_id, document_id, batch_id="bat_1", status="queued"):
    return {
        "result_id": result_id,
        "batch_id": batch_id,
        "document_id": document_id,
        "source_file": f"{document_id}.pdf",
        "snapshot_id": "sha256:s1",
        "template": {"id": "invoice-a", "version": 1},
        "llm": {"model": "fake", "prompt_version": 1},
        "status": status,
        "extracted": None,
        "issues": [],
        "review": None,
        "error": None,
        "revision": 1,
        "job": 1,
        "updated_at": "2026-09-17T00:03:00Z",
    }


def seed_batch(conn):
    insert_template(conn, template_doc())
    insert_snapshot(conn, snapshot_doc())
    documents = [
        document_doc("doc_1", "res_1", "scan_1.pdf"),
        document_doc("doc_2", "res_2", "scan_2.pdf"),
    ]
    manifest = manifest_doc(
        documents=[
            {"document_id": "doc_2", "result_id": "res_2", "source_file": "scan_2.pdf"},
            {"document_id": "doc_1", "result_id": "res_1", "source_file": "scan_1.pdf"},
        ]
    )
    results = [result_doc("res_1", "doc_1"), result_doc("res_2", "doc_2")]
    create_batch(conn, manifest, documents, results)
    return manifest, documents, results


def test_templates_insert_get_latest_and_list(conn):
    v1 = template_doc(version=1, name="Old")
    v2 = template_doc(version=2, name="New")
    other = template_doc(template_id="receipt-b", version=1, name="Receipt")

    insert_template(conn, v1)
    insert_template(conn, v2)
    insert_template(conn, other)

    assert get_template(conn, "invoice-a", 1) == v1
    assert get_template(conn, "missing", 1) is None
    assert latest_template(conn, "invoice-a") == v2
    assert latest_template(conn, "missing") is None
    assert list_templates(conn) == [v2, v1, other]

    with pytest.raises(sqlite3.IntegrityError):
        insert_template(conn, template_doc(version=1, name="Duplicate"))


def test_insert_snapshot_is_idempotent_only_for_identical_document(conn):
    snapshot = snapshot_doc()

    insert_snapshot(conn, snapshot)
    insert_snapshot(conn, snapshot.copy())

    assert get_snapshot(conn, "sha256:s1") == snapshot
    assert get_snapshot(conn, "missing") is None
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1

    with pytest.raises(sqlite3.IntegrityError):
        insert_snapshot(conn, dict(snapshot, pages=[]))


def test_create_batch_inserts_documents_results_and_batch_atomically(conn):
    manifest, documents, results = seed_batch(conn)

    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM batches").fetchone()[0] == 1
    assert get_result(conn, "res_1") == results[0]
    assert get_result(conn, "missing") is None
    assert list_results(conn, "bat_1") == [results[1], results[0]]

    document_row = conn.execute(
        "SELECT document_id, sha256, source_file, path, snapshot_id "
        "FROM documents WHERE document_id = 'doc_1'"
    ).fetchone()
    assert dict(document_row) == {
        key: documents[0][key]
        for key in ("document_id", "sha256", "source_file", "path", "snapshot_id")
    }
    assert conn.execute(
        "SELECT doc FROM batches WHERE batch_id = ?", (manifest["batch_id"],)
    ).fetchone()


def test_find_documents_by_sha256_returns_prior_matches(conn):
    seed_batch(conn)
    conn.execute("UPDATE documents SET sha256 = 'same'")

    assert find_documents_by_sha256(conn, "same") == [
        {
            "document_id": "doc_1",
            "sha256": "same",
            "source_file": "scan_1.pdf",
            "path": "documents/scan_1.pdf",
            "snapshot_id": "sha256:s1",
        },
        {
            "document_id": "doc_2",
            "sha256": "same",
            "source_file": "scan_2.pdf",
            "path": "documents/scan_2.pdf",
            "snapshot_id": "sha256:s1",
        },
    ]
    assert find_documents_by_sha256(conn, "same", before_document_id="doc_2") == [
        {
            "document_id": "doc_1",
            "sha256": "same",
            "source_file": "scan_1.pdf",
            "path": "documents/scan_1.pdf",
            "snapshot_id": "sha256:s1",
        }
    ]


def test_add_documents_to_batch_appends_manifest_documents_and_results(conn):
    seed_batch(conn)
    new_document = document_doc("doc_3", "res_3", "scan_3.pdf")
    new_result = result_doc("res_3", "doc_3")

    add_documents_to_batch(conn, "bat_1", [new_document], [new_result])

    manifest = conn.execute(
        "SELECT doc FROM batches WHERE batch_id = 'bat_1'"
    ).fetchone()[0]
    assert [doc["document_id"] for doc in list_results(conn, "bat_1")] == [
        "doc_2",
        "doc_1",
        "doc_3",
    ]
    manifest_doc = loads(manifest)
    assert manifest_doc["documents"][-1] == {
        "document_id": "doc_3",
        "result_id": "res_3",
        "source_file": "scan_3.pdf",
    }
    assert get_result(conn, "res_3") == new_result


def test_add_documents_to_batch_rolls_back_on_failure(conn):
    seed_batch(conn)
    duplicate = document_doc("doc_3", "res_1", "scan_3.pdf")

    with pytest.raises(sqlite3.IntegrityError):
        add_documents_to_batch(
            conn,
            "bat_1",
            [duplicate],
            [result_doc("res_1", "doc_3")],
        )

    assert (
        conn.execute(
            "SELECT count(*) FROM documents WHERE document_id = 'doc_3'"
        ).fetchone()[0]
        == 0
    )
    assert [doc["document_id"] for doc in list_results(conn, "bat_1")] == [
        "doc_2",
        "doc_1",
    ]


def test_create_batch_rolls_back_when_one_insert_fails(conn):
    insert_template(conn, template_doc())
    insert_snapshot(conn, snapshot_doc())
    documents = [
        document_doc("doc_1", "res_1", "scan_1.pdf"),
        document_doc("doc_2", "res_2", "scan_2.pdf", snapshot_id="sha256:missing"),
    ]
    manifest = manifest_doc(
        documents=[
            {"document_id": "doc_1", "result_id": "res_1", "source_file": "scan_1.pdf"},
            {"document_id": "doc_2", "result_id": "res_2", "source_file": "scan_2.pdf"},
        ]
    )
    results = [result_doc("res_1", "doc_1"), result_doc("res_2", "doc_2")]

    with pytest.raises(sqlite3.IntegrityError):
        create_batch(conn, manifest, documents, results)

    for table in ("documents", "batches", "results"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_update_result_review_checks_revision_and_increments(conn):
    seed_batch(conn)
    reviewed = dict(
        result_doc("res_1", "doc_1"),
        status="reviewed",
        review={
            "fields": {},
            "tables": {},
            "acknowledged": {"revision": 1, "issues": []},
        },
    )

    assert update_result_review(conn, "res_1", expected_revision=1, doc=reviewed)
    stored = get_result(conn, "res_1")
    assert stored == dict(reviewed, revision=2)

    stale = dict(stored, status="needs_review")
    assert not update_result_review(conn, "res_1", expected_revision=1, doc=stale)
    assert get_result(conn, "res_1") == stored


def test_result_status_can_be_rejected(conn):
    seed_batch(conn)
    rejected = dict(
        result_doc("res_1", "doc_1"),
        status="rejected",
        review={
            "fields": {},
            "tables": {},
            "acknowledged": {"revision": 1, "issues": []},
        },
    )

    assert update_result_review(conn, "res_1", expected_revision=1, doc=rejected)
    assert get_result(conn, "res_1") == dict(rejected, revision=2)


def test_commit_worker_outcome_checks_revision_and_job_then_increments(conn):
    seed_batch(conn)
    passed = dict(
        result_doc("res_1", "doc_1", status="passed"),
        extracted={"fields": {}, "tables": {}},
        updated_at="2026-09-17T00:04:00Z",
    )

    assert commit_worker_outcome(
        conn, "res_1", expected_revision=1, expected_job=1, doc=passed
    )
    stored = get_result(conn, "res_1")
    assert stored == dict(passed, revision=2)

    assert not commit_worker_outcome(
        conn, "res_1", expected_revision=2, expected_job=99, doc=dict(stored, job=99)
    )
    assert not commit_worker_outcome(
        conn, "res_1", expected_revision=1, expected_job=1, doc=dict(stored, job=1)
    )
    assert get_result(conn, "res_1") == stored


def test_update_document_path(conn):
    seed_batch(conn)

    update_document_path(conn, "doc_1", "archive/scan_1.pdf")

    assert (
        conn.execute(
            "SELECT path FROM documents WHERE document_id = 'doc_1'"
        ).fetchone()[0]
        == "archive/scan_1.pdf"
    )


def test_remove_queued_document_deletes_batch_entry_result_and_document(conn):
    seed_batch(conn)

    assert remove_queued_document(conn, "bat_1", "doc_2") == "documents/scan_2.pdf"

    assert get_result(conn, "res_2") is None
    assert (
        conn.execute(
            "SELECT count(*) FROM documents WHERE document_id = 'doc_2'"
        ).fetchone()[0]
        == 0
    )
    assert [doc["document_id"] for doc in list_results(conn, "bat_1")] == ["doc_1"]


def test_remove_queued_document_refuses_locked_status(conn):
    seed_batch(conn)
    processing = dict(result_doc("res_1", "doc_1"), status="processing")
    conn.execute(
        "UPDATE results SET doc = ? WHERE result_id = 'res_1'", (dumps(processing),)
    )

    with pytest.raises(StoreError, match="locked"):
        remove_queued_document(conn, "bat_1", "doc_1")

    assert get_result(conn, "res_1") == processing
    assert (
        conn.execute(
            "SELECT count(*) FROM documents WHERE document_id = 'doc_1'"
        ).fetchone()[0]
        == 1
    )


def test_delete_document_removes_rows_and_unreferenced_snapshot(conn):
    seed_batch(conn)

    assert delete_document(conn, "doc_1") == ["documents/scan_1.pdf"]

    assert get_result(conn, "res_1") is None
    assert (
        conn.execute(
            "SELECT count(*) FROM documents WHERE document_id = 'doc_1'"
        ).fetchone()[0]
        == 0
    )
    assert get_snapshot(conn, "sha256:s1") is not None
    assert [doc["document_id"] for doc in list_results(conn, "bat_1")] == ["doc_2"]

    assert delete_document(conn, "doc_2") == [
        "documents/scan_2.pdf",
        "pages/s1/page-1.png",
    ]
    assert get_snapshot(conn, "sha256:s1") is None


def test_delete_document_refuses_processing(conn):
    seed_batch(conn)
    processing = dict(result_doc("res_1", "doc_1"), status="processing")
    conn.execute(
        "UPDATE results SET doc = ? WHERE result_id = 'res_1'", (dumps(processing),)
    )

    with pytest.raises(StoreError, match="processing"):
        delete_document(conn, "doc_1")

    assert get_result(conn, "res_1") == processing
    assert (
        conn.execute(
            "SELECT count(*) FROM documents WHERE document_id = 'doc_1'"
        ).fetchone()[0]
        == 1
    )


def test_delete_document_keeps_template_sample_document_and_snapshot(conn):
    seed_batch(conn)
    sample_template = dict(
        template_doc(template_id="sample-template"),
        sample_document_id="doc_1",
        sample_snapshot_id="sha256:s1",
    )
    insert_template(conn, sample_template)

    assert delete_document(conn, "doc_1") == []

    assert get_result(conn, "res_1") is None
    assert (
        conn.execute(
            "SELECT count(*) FROM documents WHERE document_id = 'doc_1'"
        ).fetchone()[0]
        == 1
    )
    assert get_snapshot(conn, "sha256:s1") is not None
    assert [doc["document_id"] for doc in list_results(conn, "bat_1")] == ["doc_2"]


def test_read_transaction_uses_one_consistent_read_transaction(conn):
    statements = []
    conn.set_trace_callback(statements.append)

    with read_transaction(conn):
        conn.execute("SELECT 1").fetchone()
        conn.execute("SELECT 2").fetchone()

    assert "BEGIN" in statements
    assert "COMMIT" in statements
    assert not conn.in_transaction
