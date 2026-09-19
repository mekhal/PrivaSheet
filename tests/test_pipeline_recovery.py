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
            {
                "document_id": "doc_process",
                "result_id": "res_process",
                "sha256": "hash-process",
                "source_file": "process-kept.pdf",
                "path": "process/process-kept.pdf",
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
                result_doc("res_process", "doc_process", "queued"),
            ],
        )
        (datadir.archive / "moved.pdf").write_text("moved", encoding="utf-8")
        (datadir.archive / "kept.pdf").write_text("kept", encoding="utf-8")
        (datadir.process / "process-kept.pdf").write_text(
            "process kept", encoding="utf-8"
        )
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
        assert get_document(conn, "doc_process")["path"] == "process/process-kept.pdf"
        assert (datadir.archive / "moved.pdf").read_text(encoding="utf-8") == "moved"
        assert (datadir.archive / "kept.pdf").read_text(encoding="utf-8") == "kept"
        assert (datadir.process / "process-kept.pdf").read_text(
            encoding="utf-8"
        ) == "process kept"
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


def test_recover_removes_orphans_with_symlinked_datadir_root(tmp_path):
    real_root = tmp_path / "real-data"
    linked_root = tmp_path / "linked-data"
    real_root.mkdir()
    linked_root.symlink_to(real_root, target_is_directory=True)
    datadir = DataDir(linked_root)
    datadir.prepare()
    conn = open_db(datadir.db_path)
    migrate(conn)

    try:
        insert_template(conn, template_doc())
        insert_snapshot(conn, snapshot_doc())
        documents = [
            {
                "document_id": "doc_process",
                "result_id": "res_process",
                "sha256": "hash-process",
                "source_file": "process-kept.pdf",
                "path": "process/process-kept.pdf",
                "snapshot_id": "sha256:s1",
            },
            {
                "document_id": "doc_bad_path",
                "result_id": "res_bad_path",
                "sha256": "hash-bad-path",
                "source_file": "bad-path.pdf",
                "path": "../outside.pdf",
                "snapshot_id": "sha256:s1",
            },
        ]
        create_batch(
            conn,
            {
                "batch_id": "bat_1",
                "created_at": "2026-09-17T00:02:00Z",
                "template": {"id": "invoice-a", "version": 1},
                "documents": [
                    {
                        "document_id": "doc_process",
                        "result_id": "res_process",
                        "source_file": "process-kept.pdf",
                    },
                    {
                        "document_id": "doc_bad_path",
                        "result_id": "res_bad_path",
                        "source_file": "bad-path.pdf",
                    },
                ],
            },
            documents,
            [
                result_doc("res_process", "doc_process", "queued"),
                result_doc("res_bad_path", "doc_bad_path", "queued"),
            ],
        )
        kept = real_root / "process" / "process-kept.pdf"
        orphan = real_root / "process" / "orphan.tmp"
        kept.write_text("process kept", encoding="utf-8")
        orphan.write_text("orphan", encoding="utf-8")

        counts = recover(conn, datadir, "2026-09-17T00:20:00Z")

        assert counts["removed_files"] == 1
        assert get_document(conn, "doc_bad_path")["path"] == "../outside.pdf"
        assert kept.read_text(encoding="utf-8") == "process kept"
        assert not orphan.exists()
    finally:
        conn.close()


def test_recover_does_not_walk_managed_directory_symlink(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    outside = tmp_path / "outside-archive"
    outside.mkdir()
    outside_file = outside / "outside.pdf"
    outside_file.write_text("outside", encoding="utf-8")
    datadir.archive.rmdir()
    datadir.archive.symlink_to(outside, target_is_directory=True)
    conn = open_db(datadir.db_path)
    migrate(conn)

    try:
        counts = recover(conn, datadir, "2026-09-17T00:20:00Z")

        assert counts["removed_files"] == 0
        assert outside_file.read_text(encoding="utf-8") == "outside"
    finally:
        conn.close()


def test_recover_keeps_referenced_symlink(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    outside = tmp_path / "outside.pdf"
    outside.write_text("outside", encoding="utf-8")
    conn = open_db(datadir.db_path)
    migrate(conn)

    try:
        insert_template(conn, template_doc())
        insert_snapshot(conn, snapshot_doc())
        create_batch(
            conn,
            {
                "batch_id": "bat_1",
                "created_at": "2026-09-17T00:02:00Z",
                "template": {"id": "invoice-a", "version": 1},
                "documents": [
                    {
                        "document_id": "doc_link",
                        "result_id": "res_link",
                        "source_file": "linked.pdf",
                    },
                ],
            },
            [
                {
                    "document_id": "doc_link",
                    "result_id": "res_link",
                    "sha256": "hash-link",
                    "source_file": "linked.pdf",
                    "path": "process/linked.pdf",
                    "snapshot_id": "sha256:s1",
                },
            ],
            [result_doc("res_link", "doc_link", "queued")],
        )
        link = datadir.process / "linked.pdf"
        link.symlink_to(outside)

        counts = recover(conn, datadir, "2026-09-17T00:20:00Z")

        assert counts["removed_files"] == 0
        assert link.is_symlink()
        assert link.read_text(encoding="utf-8") == "outside"
    finally:
        conn.close()


def test_recover_skips_orphan_file_when_unlink_fails(tmp_path, monkeypatch):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    stuck = datadir.process / "stuck.tmp"
    stuck.write_text("stuck", encoding="utf-8")
    conn = open_db(datadir.db_path)
    migrate(conn)
    original_unlink = type(stuck).unlink

    def unlink(path, *args, **kwargs):
        if path == stuck:
            raise PermissionError("locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(stuck), "unlink", unlink)

    try:
        counts = recover(conn, datadir, "2026-09-17T00:20:00Z")

        assert counts["removed_files"] == 0
        assert stuck.read_text(encoding="utf-8") == "stuck"
    finally:
        conn.close()
