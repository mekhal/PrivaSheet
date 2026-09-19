"""Batch intake pipeline API."""

from __future__ import annotations

import hashlib
import sqlite3
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from privasheet.ingest.checks import Limits
from privasheet.pipeline.intake import Upload, add_files, create_batch, remove_file
from privasheet.store import StoreError, dumps, migrate, open_db
from privasheet.store.repo import (
    get_batch,
    get_document,
    get_result,
    insert_template,
    list_results,
)


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "store.db")
    migrate(connection)
    insert_template(connection, template_doc())
    yield connection
    connection.close()


def template_doc(template_id="invoice-a", version=1):
    return {
        "template_id": template_id,
        "version": version,
        "version_label": "1.0.20260919",
        "name": "Invoice A",
        "created_at": "2026-09-19T00:00:00Z",
        "fields": [],
        "tables": [],
        "match": {"min_key_label_ratio": 1},
    }


def ids(*values):
    iterator = iter(values)
    return lambda prefix: f"{prefix}_{next(iterator)}"


def image_bytes(fmt: str) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (2, 3), color=(20, 120, 200)).save(stream, format=fmt)
    return stream.getvalue()


def pdf_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(stream, format="PDF")
    return stream.getvalue()


def upload(name: str, data: bytes) -> Upload:
    return Upload(source_name=name, stream=BytesIO(data))


def read_process_file(datadir: Path, document_id: str) -> Path:
    matches = list((datadir / "process").glob(f"{document_id}.*"))
    assert len(matches) == 1
    return matches[0]


def test_create_batch_accepts_files_in_upload_order_and_queues_results(conn, tmp_path):
    png = image_bytes("PNG")
    jpeg = image_bytes("JPEG")
    pdf = pdf_bytes()

    result = create_batch(
        conn,
        tmp_path,
        "invoice-a",
        1,
        [
            upload("scan.png", png),
            upload("photo.jpg", jpeg),
            upload("doc.pdf", pdf),
        ],
        now=lambda: "2026-09-19T01:02:03Z",
        new_id=ids("b1", "d1", "r1", "d2", "r2", "d3", "r3"),
    )

    assert result.batch_id == "bat_b1"
    assert result.rejected == []
    assert result.accepted == [
        ("doc_d1", "res_r1", "scan.png"),
        ("doc_d2", "res_r2", "photo.jpg"),
        ("doc_d3", "res_r3", "doc.pdf"),
    ]
    assert [item["document_id"] for item in list_results(conn, "bat_b1")] == [
        "doc_d1",
        "doc_d2",
        "doc_d3",
    ]

    for document_id, expected_ext, expected_bytes in [
        ("doc_d1", "png", png),
        ("doc_d2", "jpg", jpeg),
        ("doc_d3", "pdf", pdf),
    ]:
        path = read_process_file(tmp_path, document_id)
        document = get_document(conn, document_id)
        assert path.name == f"{document_id}.{expected_ext}"
        assert document["path"] == f"process/{document_id}.{expected_ext}"
        assert document["snapshot_id"] is None
        assert document["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
        assert path.read_bytes() == expected_bytes

    assert list_results(conn, "bat_b1")[0] == {
        "result_id": "res_r1",
        "batch_id": "bat_b1",
        "document_id": "doc_d1",
        "source_file": "scan.png",
        "snapshot_id": None,
        "template": {"id": "invoice-a", "version": 1},
        "llm": {"model": None, "prompt_version": 1},
        "status": "queued",
        "extracted": None,
        "issues": [],
        "review": None,
        "error": None,
        "revision": 0,
        "job": 1,
        "updated_at": "2026-09-19T01:02:03Z",
    }


def test_file_signature_controls_extension_not_source_name(conn, tmp_path):
    result = create_batch(
        conn,
        tmp_path,
        "invoice-a",
        1,
        [upload("misnamed.pdf", image_bytes("PNG"))],
        new_id=ids("b1", "d1", "r1"),
    )

    assert result.rejected == []
    assert get_document(conn, "doc_d1")["path"] == "process/doc_d1.png"
    assert (tmp_path / "process" / "doc_d1.png").exists()


def test_invalid_files_are_rejected_without_rows_or_files(conn, tmp_path):
    result = create_batch(
        conn,
        tmp_path,
        "invoice-a",
        1,
        [
            upload("too-big.png", image_bytes("PNG")),
            upload("notes.txt", b"hello"),
            upload("corrupt.png", b"\x89PNG\r\n\x1a\nx"),
        ],
        limits=Limits(max_bytes=32),
        new_id=ids("b1", "d1", "r1", "d2", "r2", "d3", "r3"),
    )

    assert result.batch_id is None
    assert result.accepted == []
    assert [(item.source_file, item.code) for item in result.rejected] == [
        ("too-big.png", "FILE_TOO_LARGE"),
        ("notes.txt", "UNSUPPORTED_TYPE"),
        ("corrupt.png", "INSPECT_FAILED"),
    ]
    assert get_batch(conn, "bat_b1") is None
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM results").fetchone()[0] == 0
    assert list((tmp_path / "process").glob("*")) == []


def test_missing_template_is_caller_error(conn, tmp_path):
    with pytest.raises(ValueError, match="template missing version 1 does not exist"):
        create_batch(
            conn,
            tmp_path,
            "missing",
            1,
            [upload("scan.png", image_bytes("PNG"))],
        )


def test_database_failure_removes_saved_files(conn, tmp_path):
    with pytest.raises(sqlite3.IntegrityError):
        create_batch(
            conn,
            tmp_path,
            "invoice-a",
            1,
            [upload("a.png", image_bytes("PNG")), upload("b.png", image_bytes("PNG"))],
            new_id=ids("same", "same", "same", "same", "same"),
        )

    assert list((tmp_path / "process").glob("*")) == []
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM results").fetchone()[0] == 0


def test_add_files_appends_to_manifest_order(conn, tmp_path):
    create_batch(
        conn,
        tmp_path,
        "invoice-a",
        1,
        [upload("first.png", image_bytes("PNG"))],
        new_id=ids("b1", "d1", "r1"),
    )

    result = add_files(
        conn,
        tmp_path,
        "bat_b1",
        [
            upload("second.png", image_bytes("PNG")),
            upload("third.jpg", image_bytes("JPEG")),
        ],
        new_id=ids("d2", "r2", "d3", "r3"),
    )

    assert result.batch_id == "bat_b1"
    assert result.accepted == [
        ("doc_d2", "res_r2", "second.png"),
        ("doc_d3", "res_r3", "third.jpg"),
    ]
    assert [doc["source_file"] for doc in get_batch(conn, "bat_b1")["documents"]] == [
        "first.png",
        "second.png",
        "third.jpg",
    ]
    assert [doc["source_file"] for doc in list_results(conn, "bat_b1")] == [
        "first.png",
        "second.png",
        "third.jpg",
    ]


def test_remove_file_deletes_queued_file_after_commit(conn, tmp_path):
    create_batch(
        conn,
        tmp_path,
        "invoice-a",
        1,
        [upload("scan.png", image_bytes("PNG"))],
        new_id=ids("b1", "d1", "r1"),
    )
    path = tmp_path / "process" / "doc_d1.png"

    remove_file(conn, tmp_path, "bat_b1", "doc_d1")

    assert not path.exists()
    assert get_document(conn, "doc_d1") is None
    assert get_result(conn, "res_r1") is None
    assert list_results(conn, "bat_b1") == []


def test_remove_file_refuses_processing_document_and_keeps_file(conn, tmp_path):
    create_batch(
        conn,
        tmp_path,
        "invoice-a",
        1,
        [upload("scan.png", image_bytes("PNG"))],
        new_id=ids("b1", "d1", "r1"),
    )
    result = dict(get_result(conn, "res_r1"), status="processing")
    conn.execute(
        "UPDATE results SET doc = ? WHERE result_id = ?",
        (dumps(result), "res_r1"),
    )

    with pytest.raises(StoreError, match="document is locked"):
        remove_file(conn, tmp_path, "bat_b1", "doc_d1")

    assert (tmp_path / "process" / "doc_d1.png").exists()
    assert get_document(conn, "doc_d1") is not None
