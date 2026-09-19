"""Worker queue repository operations."""

import sqlite3

import pytest

from privasheet.store import StoreError, dumps, migrate, open_db
from privasheet.store.repo import (
    claim_next_queued,
    commit_worker_outcome,
    count_results_by_status,
    create_batch,
    find_result_for_document,
    get_batch,
    get_document,
    get_result,
    insert_snapshot,
    insert_template,
    list_batches,
    referenced_file_paths,
    reset_processing_to_queued,
    retry_result,
    set_document_snapshot,
)


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "store.db")
    migrate(connection)
    yield connection
    connection.close()


def template_doc():
    return {
        "template_id": "invoice-a",
        "version": 1,
        "version_label": "1.0.20260917",
        "name": "Invoice A",
        "created_at": "2026-09-17T00:00:00Z",
        "fields": [],
        "tables": [],
        "match": {"min_key_label_ratio": 1},
    }


def snapshot_doc(snapshot_id="sha256:s1", image="pages/s1/page-1.png"):
    return {
        "snapshot_id": snapshot_id,
        "created_at": "2026-09-17T00:01:00Z",
        "pages": [{"image": image, "boxes": []}, {"boxes": []}],
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


def result_doc(result_id, document_id, batch_id, status="queued"):
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
        "revision": 0,
        "job": 1,
        "updated_at": "2026-09-17T00:03:00Z",
    }


def manifest_doc(batch_id, created_at, documents):
    return {
        "batch_id": batch_id,
        "created_at": created_at,
        "template": {"id": "invoice-a", "version": 1},
        "documents": [
            {
                "document_id": document["document_id"],
                "result_id": document["result_id"],
                "source_file": document["source_file"],
            }
            for document in documents
        ],
    }


def seed_batches(conn):
    insert_template(conn, template_doc())
    insert_snapshot(conn, snapshot_doc())
    first_docs = [
        document_doc("doc_b1", "res_b1", "b1.pdf"),
        document_doc("doc_b2", "res_b2", "b2.pdf"),
    ]
    second_docs = [document_doc("doc_a1", "res_a1", "a1.pdf")]
    first_manifest = manifest_doc(
        "bat_b",
        "2026-09-17T00:02:00Z",
        [first_docs[1], first_docs[0]],
    )
    second_manifest = manifest_doc(
        "bat_a",
        "2026-09-17T00:05:00Z",
        second_docs,
    )
    first_results = [
        result_doc("res_b1", "doc_b1", "bat_b"),
        result_doc("res_b2", "doc_b2", "bat_b"),
    ]
    second_results = [result_doc("res_a1", "doc_a1", "bat_a")]
    create_batch(conn, first_manifest, first_docs, first_results)
    create_batch(conn, second_manifest, second_docs, second_results)
    return {
        "documents": first_docs + second_docs,
        "manifests": [first_manifest, second_manifest],
        "results": first_results + second_results,
    }


@pytest.mark.parametrize(
    ("operation", "present_id", "missing_id", "expected_key"),
    [
        (get_document, "doc_b1", "missing", "document_id"),
        (get_batch, "bat_b", "missing", "batch_id"),
        (find_result_for_document, "doc_b1", "missing", "document_id"),
    ],
)
def test_getters_return_documents_or_none(
    conn, operation, present_id, missing_id, expected_key
):
    seed = seed_batches(conn)

    assert operation(conn, present_id)[expected_key] == present_id
    assert operation(conn, missing_id) is None
    assert operation(conn, present_id) is not seed


def test_list_batches_orders_by_created_at_then_batch_id(conn):
    seed = seed_batches(conn)
    doc = document_doc("doc_c1", "res_c1", "c1.pdf")
    manifest = manifest_doc("bat_c", "2026-09-17T00:02:00Z", [doc])
    create_batch(conn, manifest, [doc], [result_doc("res_c1", "doc_c1", "bat_c")])

    assert [batch["batch_id"] for batch in list_batches(conn)] == [
        "bat_b",
        "bat_c",
        "bat_a",
    ]
    assert list_batches(conn)[0] == seed["manifests"][0]


def test_claim_next_queued_uses_batch_and_manifest_order(conn):
    seed_batches(conn)

    first = claim_next_queued(conn, "2026-09-17T00:10:00Z")
    second = claim_next_queued(conn, "2026-09-17T00:11:00Z")
    third = claim_next_queued(conn, "2026-09-17T00:12:00Z")

    assert [first["result_id"], second["result_id"], third["result_id"]] == [
        "res_b2",
        "res_b1",
        "res_a1",
    ]
    assert first["result_id"] != second["result_id"]
    assert first["status"] == "processing"
    assert first["revision"] == 1
    assert first["job"] == 1
    assert first["updated_at"] == "2026-09-17T00:10:00Z"
    assert claim_next_queued(conn, "2026-09-17T00:13:00Z") is None


def test_claim_next_queued_ignores_non_queued_results(conn):
    seed_batches(conn)
    for result in ("res_b1", "res_b2", "res_a1"):
        doc = dict(get_result(conn, result), status="failed")
        conn.execute(
            "UPDATE results SET doc = ? WHERE result_id = ?", (dumps(doc), result)
        )

    assert claim_next_queued(conn, "2026-09-17T00:10:00Z") is None


def test_claimed_revision_and_job_are_required_for_worker_commit(conn):
    seed_batches(conn)

    claimed = claim_next_queued(conn, "2026-09-17T00:10:00Z")
    passed = dict(
        claimed,
        status="passed",
        extracted={"fields": {}, "tables": {}},
        updated_at="2026-09-17T00:11:00Z",
    )

    assert not commit_worker_outcome(
        conn,
        claimed["result_id"],
        claimed["revision"],
        claimed["job"] + 1,
        dict(passed, job=claimed["job"] + 1),
    )
    assert commit_worker_outcome(
        conn,
        claimed["result_id"],
        claimed["revision"],
        claimed["job"],
        passed,
    )
    stored = get_result(conn, claimed["result_id"])
    assert stored == dict(passed, revision=claimed["revision"] + 1)
    assert not commit_worker_outcome(
        conn,
        claimed["result_id"],
        claimed["revision"],
        claimed["job"],
        passed,
    )


def test_reset_processing_to_queued_only_resets_processing(conn):
    seed_batches(conn)
    statuses = {
        "res_b2": "processing",
        "res_b1": "failed",
        "res_a1": "needs_review",
    }
    for result_id, status in statuses.items():
        doc = dict(get_result(conn, result_id), status=status, revision=3, job=7)
        conn.execute(
            "UPDATE results SET doc = ? WHERE result_id = ?", (dumps(doc), result_id)
        )

    assert reset_processing_to_queued(conn, "2026-09-17T00:20:00Z") == ["res_b2"]
    assert get_result(conn, "res_b2") == dict(
        result_doc("res_b2", "doc_b2", "bat_b"),
        status="queued",
        revision=4,
        job=7,
        updated_at="2026-09-17T00:20:00Z",
    )
    assert get_result(conn, "res_b1")["status"] == "failed"
    assert get_result(conn, "res_a1")["status"] == "needs_review"


@pytest.mark.parametrize("status", ["failed", "needs_review"])
def test_retry_result_requeues_retryable_statuses(conn, status):
    seed_batches(conn)
    failed = dict(
        get_result(conn, "res_b1"),
        status=status,
        extracted={"fields": {}},
        issues=[{"code": "missing"}],
        error={"message": "boom"},
        revision=4,
        job=2,
    )
    conn.execute(
        "UPDATE results SET doc = ? WHERE result_id = 'res_b1'", (dumps(failed),)
    )

    assert retry_result(conn, "res_b1", expected_revision=4, now="2026-09-17T00:30:00Z")
    assert get_result(conn, "res_b1") == dict(
        failed,
        status="queued",
        extracted=None,
        issues=[],
        error=None,
        revision=5,
        job=3,
        updated_at="2026-09-17T00:30:00Z",
    )


@pytest.mark.parametrize(
    ("status", "review", "expected_revision"),
    [
        ("needs_review", {"fields": {}, "tables": {}}, 0),
        ("failed", None, 99),
        ("queued", None, 0),
        ("processing", None, 0),
    ],
)
def test_retry_result_refuses_non_retryable_cases(
    conn, status, review, expected_revision
):
    seed_batches(conn)
    doc = dict(get_result(conn, "res_b1"), status=status, review=review)
    conn.execute("UPDATE results SET doc = ? WHERE result_id = 'res_b1'", (dumps(doc),))

    assert not retry_result(conn, "res_b1", expected_revision, "2026-09-17T00:30:00Z")
    assert get_result(conn, "res_b1") == doc


def test_set_document_snapshot_updates_existing_document(conn):
    seed_batches(conn)
    insert_snapshot(conn, snapshot_doc("sha256:s2", "pages/s2/page-1.png"))

    set_document_snapshot(conn, "doc_b1", "sha256:s2")

    assert get_document(conn, "doc_b1")["snapshot_id"] == "sha256:s2"
    with pytest.raises(StoreError, match="document not found"):
        set_document_snapshot(conn, "missing", "sha256:s2")
    with pytest.raises(sqlite3.IntegrityError):
        set_document_snapshot(conn, "doc_b1", "sha256:missing")


def test_referenced_file_paths_covers_document_paths_and_snapshot_images(conn):
    seed_batches(conn)
    insert_snapshot(conn, snapshot_doc("sha256:s2", "pages/s2/page-1.png"))
    conn.execute("UPDATE documents SET path = NULL WHERE document_id = 'doc_b1'")

    assert referenced_file_paths(conn) == {
        "documents/b2.pdf",
        "documents/a1.pdf",
        "pages/s1/page-1.png",
        "pages/s2/page-1.png",
    }


def test_count_results_by_status(conn):
    seed_batches(conn)
    statuses = {
        "res_b2": "processing",
        "res_b1": "failed",
        "res_a1": "failed",
    }
    for result_id, status in statuses.items():
        doc = dict(get_result(conn, result_id), status=status)
        conn.execute(
            "UPDATE results SET doc = ? WHERE result_id = ?", (dumps(doc), result_id)
        )

    assert count_results_by_status(conn) == {"failed": 2, "processing": 1}
    conn.execute("DELETE FROM results")
    assert count_results_by_status(conn) == {}
