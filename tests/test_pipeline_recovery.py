from privasheet.pipeline import DataDir
from privasheet.pipeline.recovery import recover
from privasheet.store import dumps, migrate, open_db
from privasheet.store.repo import (
    create_batch,
    get_document,
    get_result,
    insert_snapshot,
    insert_template,
)


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


def snapshot_doc():
    return {
        "snapshot_id": "sha256:s1",
        "created_at": "2026-09-17T00:01:00Z",
        "pages": [{"image": "pages/s1/page-1.png", "boxes": []}],
    }


def result_doc(result_id, document_id, status):
    return {
        "result_id": result_id,
        "batch_id": "bat_1",
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


def test_recover_resets_corrects_removes_orphans_and_is_idempotent(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    conn = open_db(datadir.db_path)
    migrate(conn)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    try:
        insert_template(conn, template_doc())
        insert_snapshot(conn, snapshot_doc())
        documents = [
            {
                "document_id": "doc_moved",
                "result_id": "res_moved",
                "sha256": "hash-moved",
                "source_file": "moved.pdf",
                "path": "process/moved.pdf",
                "snapshot_id": "sha256:s1",
            },
            {
                "document_id": "doc_kept",
                "result_id": "res_kept",
                "sha256": "hash-kept",
                "source_file": "kept.pdf",
                "path": "archive/kept.pdf",
                "snapshot_id": "sha256:s1",
            },
        ]
        manifest = {
            "batch_id": "bat_1",
            "created_at": "2026-09-17T00:02:00Z",
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
        create_batch(
            conn,
            manifest,
            documents,
            [
                result_doc("res_moved", "doc_moved", "processing"),
                result_doc("res_kept", "doc_kept", "queued"),
            ],
        )
        (datadir.archive / "moved.pdf").write_text("moved", encoding="utf-8")
        (datadir.archive / "kept.pdf").write_text("kept", encoding="utf-8")
        (datadir.archive / "orphan-upload.pdf").write_text("orphan", encoding="utf-8")
        (datadir.process / "leftover.tmp").write_text("tmp", encoding="utf-8")
        (datadir.pages / "s1").mkdir()
        (datadir.pages / "s1" / "page-1.png").write_text("page", encoding="utf-8")
        (datadir.pages / "orphan").mkdir()
        (datadir.pages / "orphan" / "page-1.png").write_text("orphan", encoding="utf-8")
        (datadir.process / "outside-link").symlink_to(outside)
        conn.execute(
            "UPDATE results SET doc = ? WHERE result_id = 'res_moved'",
            (dumps(dict(get_result(conn, "res_moved"), revision=4, job=7)),),
        )

        counts = recover(conn, datadir, "2026-09-17T00:20:00Z")

        assert counts == {
            "reset_results": 1,
            "corrected_paths": 1,
            "removed_files": 4,
            "removed_dirs": 1,
        }
        assert get_result(conn, "res_moved") == dict(
            result_doc("res_moved", "doc_moved", "processing"),
            status="queued",
            revision=5,
            job=7,
            updated_at="2026-09-17T00:20:00Z",
        )
        assert get_document(conn, "doc_moved")["path"] == "archive/moved.pdf"
        assert (datadir.archive / "moved.pdf").read_text(encoding="utf-8") == "moved"
        assert (datadir.archive / "kept.pdf").read_text(encoding="utf-8") == "kept"
        assert (datadir.pages / "s1" / "page-1.png").exists()
        assert not (datadir.archive / "orphan-upload.pdf").exists()
        assert not (datadir.process / "leftover.tmp").exists()
        assert not (datadir.process / "outside-link").exists()
        assert not (datadir.pages / "orphan").exists()
        assert outside.read_text(encoding="utf-8") == "outside"

        assert recover(conn, datadir, "2026-09-17T00:21:00Z") == {
            "reset_results": 0,
            "corrected_paths": 0,
            "removed_files": 0,
            "removed_dirs": 0,
        }
    finally:
        conn.close()
