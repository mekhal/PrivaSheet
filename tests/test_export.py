"""Batch JSONL export requirements from design sections 4.3–4.5."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from privasheet.export import (
    ExportNotAllowed,
    build_jsonl,
    failed_documents,
    write_atomic,
)


@pytest.fixture
def batch():
    template = {
        "template_id": "abc-layout-1",
        "version": 2,
        "fields": [{"key": key} for key in ("invoice_no", "date", "total", "po_no")],
        "tables": [
            {
                "key": "line_items",
                "columns": [
                    {"key": key}
                    for key in ("description", "qty", "unit_price", "amount")
                ],
            }
        ],
    }
    manifest = {
        "batch_id": "bat_01J…",
        "template": {"id": "abc-layout-1", "version": 2},
        "documents": [
            {
                "document_id": "doc_01J…",
                "result_id": "res_01J…",
                "source_file": "scan_001.jpg",
            }
        ],
    }
    review = {
        "fields": {
            "invoice_no": "INV-0042",
            "date": "2026-09-01",
            "total": "1284.00",
            "po_no": None,
        },
        "tables": {
            "line_items": [
                {
                    "description": "Paper A4",
                    "qty": "2",
                    "unit_price": "600.00",
                    "amount": "1200.00",
                }
            ]
        },
    }
    extracted = {
        "fields": {
            key: {"value": value, "raw": "different OCR text"}
            for key, value in review["fields"].items()
        },
        "tables": {
            "line_items": [
                {
                    key: {"value": value, "box_ids": ["p1-b0001"]}
                    for key, value in review["tables"]["line_items"][0].items()
                }
            ]
        },
    }
    result = {
        **manifest["documents"][0],
        "status": "reviewed",
        "extracted": extracted,
        "review": review,
        "issues": [],
    }
    return manifest, {"res_01J…": result}, template


def test_spec_example_exactly_and_no_mutation(batch):
    original = deepcopy(batch)
    assert build_jsonl(*batch) == (
        '{"schema_version":1,"batch_id":"bat_01J…","document_id":"doc_01J…",'
        '"source_file":"scan_001.jpg","template":{"id":"abc-layout-1","version":2},'
        '"human_reviewed":true,"fields":{"invoice_no":"INV-0042","date":"2026-09-01",'
        '"total":"1284.00","po_no":null},"tables":{"line_items":[{"description":'
        '"Paper A4","qty":"2","unit_price":"600.00","amount":"1200.00"}]}}\n'
    )
    assert batch == original


def test_extracted_canonical_values_and_missing_cells(batch):
    result = batch[1]["res_01J…"]
    result.update(status="passed", review=None)
    result["extracted"]["fields"]["po_no"] = {"missing": True}
    result["extracted"]["fields"]["extra"] = {"value": "omit"}
    row = result["extracted"]["tables"]["line_items"][0]
    del row["qty"]
    row["amount"] = {"raw": "unparseable", "value": None}
    line = json.loads(build_jsonl(*batch))
    assert line["human_reviewed"] is False
    assert line["fields"] == {
        "invoice_no": "INV-0042",
        "date": "2026-09-01",
        "total": "1284.00",
        "po_no": None,
    }
    assert line["tables"] == {
        "line_items": [
            {
                "description": "Paper A4",
                "qty": None,
                "unit_price": "600.00",
                "amount": None,
            }
        ]
    }


@pytest.mark.parametrize("status", ["passed", "reviewed"])
def test_review_is_complete_effective_result(batch, status):
    result = batch[1]["res_01J…"]
    result.update(
        status=status,
        review={
            "fields": {"total": None},
            "tables": {
                "line_items": [{"description": "แก้ไข", "extra": "omit"}, {"qty": "0"}]
            },
        },
    )
    line = json.loads(build_jsonl(*batch))
    assert line["human_reviewed"] is (status == "reviewed")
    assert line["fields"] == dict.fromkeys(("invoice_no", "date", "total", "po_no"))
    assert line["tables"]["line_items"] == [
        {"description": "แก้ไข", "qty": None, "unit_price": None, "amount": None},
        {"description": None, "qty": "0", "unit_price": None, "amount": None},
    ]
    result["review"] = {"fields": {}, "tables": {}}
    assert json.loads(build_jsonl(*batch))["tables"] == {"line_items": []}


def test_manifest_order_and_failed_documents(batch):
    manifest, results, template = batch
    for index, status in enumerate(("failed", "passed", "failed")):
        document = {
            "document_id": f"doc_{index}",
            "result_id": f"res_{index}",
            "source_file": f"scan_{index}.jpg",
        }
        manifest["documents"].insert(0, document)
        results[document["result_id"]] = {
            **document,
            "status": status,
            "review": None,
            "extracted": {"fields": {}, "tables": {}},
        }
    results["outside_batch"] = {"status": "queued"}
    text = build_jsonl(manifest, results, template)
    assert text.endswith("\n")
    assert [json.loads(line)["document_id"] for line in text.splitlines()] == [
        "doc_1",
        "doc_01J…",
    ]
    assert failed_documents(manifest, results) == ["scan_2.jpg", "scan_0.jpg"]


@pytest.mark.parametrize("status", ["queued", "processing", "needs_review"])
def test_incomplete_batch_cannot_export(batch, status):
    manifest, results, _ = batch
    manifest["documents"].append({"result_id": "pending"})
    results["pending"] = {"status": status}
    with pytest.raises(ExportNotAllowed):
        build_jsonl(*batch)
    assert issubclass(ExportNotAllowed, ValueError)


def test_empty_and_all_failed_batches(batch):
    manifest, results, template = batch
    results["res_01J…"] = {"status": "failed", "extracted": None}
    assert build_jsonl(*batch) == ""
    assert failed_documents(manifest, results) == ["scan_001.jpg"]
    manifest["documents"] = []
    assert build_jsonl(manifest, results, template) == ""
    assert failed_documents(manifest, results) == []


def test_atomic_write_flushes_syncs_and_replaces(tmp_path, monkeypatch):
    target = tmp_path / "batch.jsonl"
    target.write_text("old", encoding="utf-8")
    text = ' {"value":"日本語"}\n'
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        assert target.read_text(encoding="utf-8") == "old"
        (temporary,) = [p for p in tmp_path.iterdir() if p != target]
        assert temporary.read_bytes() == text.encode("utf-8")
        events.append("fsync")
        real_fsync(fd)

    def replace(source, destination):
        assert Path(source).parent == tmp_path
        assert events == ["fsync"]
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    write_atomic(target, text)
    assert events == ["fsync", "replace"]
    assert target.read_bytes() == text.encode("utf-8")
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_atomic_write_failure_preserves_target_and_cleans_temp(
    tmp_path, monkeypatch, operation
):
    target = tmp_path / "batch.jsonl"
    target.write_text("old", encoding="utf-8")

    def fail(*args):
        raise OSError("synthetic failure")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError, match="synthetic failure"):
        write_atomic(str(target), "new\n")
    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_creates_new_empty_file(tmp_path):
    target = tmp_path / "empty.jsonl"
    write_atomic(target, "")
    assert target.read_bytes() == b""
    assert list(tmp_path.iterdir()) == [target]
