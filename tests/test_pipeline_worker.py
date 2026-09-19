import logging
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from PIL import Image

import privasheet.pipeline.worker as worker_module
from privasheet.ingest.checks import Limits
from privasheet.ocr.engine import RawBox
from privasheet.pipeline.datadir import DataDir
from privasheet.pipeline.intake import Upload, create_batch
from privasheet.pipeline.worker import PROCESSING_ERROR, Worker
from privasheet.store import dumps, migrate, open_db
from privasheet.store.repo import (
    get_document,
    get_result,
    insert_template,
    list_results,
    retry_result,
    update_result_review,
)
from privasheet.validate.checks import DUPLICATE_DOCUMENT, PROCESSING_TIMEOUT

OCR_ENGINE = "tests.test_pipeline_worker:make_engine"
VALUE_ERROR_ENGINE = "tests.fake_ocr:make_value_error_engine"
SLEEPING_OCR_ENGINE = "tests.fake_ocr:make_sleeping_engine"


class FakeOcrEngine:
    name = "fake-ocr"
    version = "1.0"
    config_sha256 = "fake-config"

    def models(self):
        return [{"name": "det", "sha256": "fake-det"}]

    def recognize(self, image):
        width, height = image.size
        return [
            RawBox(
                quad_px=((0, 0), (width, 0), (width, height), (0, height)),
                text=f"{width}x{height}",
                score=0.99,
            )
        ]


def make_engine():
    return FakeOcrEngine()


class FakeLlmClient:
    def __init__(self, response=None, *, block=None):
        self.response = response or valid_response()
        self.block = block
        self.calls = []

    def chat_json(self, messages, timeout=None):
        self.calls.append({"messages": messages, "timeout": timeout})
        if self.block is not None:
            self.block.entered.set()
            self.block.release.wait(5)
        return self.response


class BlockingLlm:
    def __init__(self):
        self.entered = Event()
        self.release = Event()


@pytest.fixture
def store(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    conn = open_db(datadir.db_path)
    migrate(conn)
    insert_template(conn, template_doc())
    lock = datadir.acquire_lock()
    try:
        yield conn, datadir
    finally:
        lock.release()
        conn.close()


def settings(timeout_s=30):
    return SimpleNamespace(model="fake-model", document_timeout_s=timeout_s)


def template_doc():
    return {
        "template_id": "invoice-a",
        "version": 1,
        "version_label": "1.0.20260917",
        "name": "Invoice A",
        "created_at": "2026-09-17T00:00:00Z",
        "fields": [
            {
                "key": "size",
                "type": "text",
                "required": True,
                "description": "The page size text.",
                "hint": {"labels": ["10x12"]},
            }
        ],
        "tables": [],
        "match": {"min_key_label_ratio": 0},
    }


def valid_response():
    return {
        "fields": {"size": {"box_ids": ["p1-b0000"], "span": "10x12"}},
        "tables": {},
    }


def make_png(path: Path, size=(10, 12), color=(80, 120, 160)):
    Image.new("RGB", size, color).save(path, format="PNG")


def _color_for_name(name):
    seed = sum(name.encode("utf-8"))
    return (40 + seed % 180, 40 + (seed * 3) % 180, 40 + (seed * 7) % 180)


def enqueue(conn, datadir, *names, now="2026-09-19T00:00:00Z", color=None):
    uploads = []
    streams = []
    for name in names:
        path = datadir.process / name
        make_png(path, color=color or _color_for_name(name))
        stream = path.open("rb")
        streams.append(stream)
        uploads.append(Upload(name, stream))
    try:
        result = create_batch(
            conn,
            datadir.root,
            "invoice-a",
            1,
            uploads,
            limits=Limits(),
            now=lambda: now,
        )
    finally:
        for stream in streams:
            stream.close()
    return result.batch_id


def start_worker(
    datadir, client, *, timeout_s=30, engine=OCR_ENGINE, poll_interval=0.01
):
    worker = Worker(
        datadir.root,
        datadir.db_path,
        settings(timeout_s),
        client,
        ocr_engine=engine,
        poll_interval_s=poll_interval,
    )
    worker.start()
    return worker


def wait_for(conn, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def statuses(conn, batch_id):
    return [result["status"] for result in list_results(conn, batch_id)]


def test_three_queued_documents_are_processed_in_order_and_archived(store, caplog):
    conn, datadir = store
    caplog.set_level(logging.INFO, logger="privasheet.pipeline.worker")
    batch_id = enqueue(conn, datadir, "one.png", "two.png", "three.png")
    worker = start_worker(datadir, FakeLlmClient())
    try:
        wait_for(conn, lambda: statuses(conn, batch_id) == ["passed"] * 3)
    finally:
        worker.stop(1)

    results = list_results(conn, batch_id)
    assert [result["source_file"] for result in results] == [
        "one.png",
        "two.png",
        "three.png",
    ]
    for result in results:
        document = get_document(conn, result["document_id"])
        assert document["path"].startswith("archive/")
        assert (datadir.root / document["path"]).is_file()
    for result, record in zip(results, caplog.records, strict=True):
        assert f"result_id={result['result_id']}" in record.getMessage()
        assert "status=passed" in record.getMessage()
    assert all("10x12" not in record.getMessage() for record in caplog.records)


def test_worker_idles_then_processes_batch_when_woken(store):
    conn, datadir = store
    worker = start_worker(datadir, FakeLlmClient(), poll_interval=30)
    try:
        batch_id = enqueue(conn, datadir, "late.png")
        worker.wake()
        wait_for(conn, lambda: statuses(conn, batch_id) == ["passed"])
    finally:
        worker.stop(1)


def test_two_batches_are_drained_in_batch_order(store, caplog):
    conn, datadir = store
    caplog.set_level(logging.INFO, logger="privasheet.pipeline.worker")
    first_id = enqueue(conn, datadir, "first-a.png", "first-b.png")
    second_id = enqueue(conn, datadir, "second-a.png")
    worker = start_worker(datadir, FakeLlmClient())
    try:
        wait_for(
            conn,
            lambda: (
                statuses(conn, first_id) == ["passed", "passed"]
                and statuses(conn, second_id) == ["passed"]
            ),
        )
    finally:
        worker.stop(1)

    processed = [
        record.getMessage().split("source_file=")[1].split()[0]
        for record in caplog.records
    ]
    assert processed == ["first-a.png", "first-b.png", "second-a.png"]


def test_job_bump_discards_outcome_and_retried_result_commits(store):
    conn, datadir = store
    batch_id = enqueue(conn, datadir, "retry.png")
    block = BlockingLlm()
    worker = start_worker(datadir, FakeLlmClient(block=block))
    try:
        assert block.entered.wait(5)
        result = list_results(conn, batch_id)[0]
        failed = dict(
            result,
            status="failed",
            error={"code": "TEST", "detail": "synthetic"},
            updated_at="2026-09-19T00:01:00Z",
        )
        conn.execute(
            "UPDATE results SET doc = ? WHERE result_id = ?",
            (dumps(failed), result["result_id"]),
        )
        assert retry_result(
            conn,
            result["result_id"],
            result["revision"],
            "2026-09-19T00:02:00Z",
        )
        block.release.set()
        wait_for(conn, lambda: statuses(conn, batch_id) == ["passed"])
    finally:
        block.release.set()
        worker.stop(1)

    result = list_results(conn, batch_id)[0]
    assert result["job"] == 2
    assert result["revision"] > 2


def test_revision_bump_discards_outcome_and_requeued_result_commits(store):
    conn, datadir = store
    batch_id = enqueue(conn, datadir, "reviewed.png")
    block = BlockingLlm()
    worker = start_worker(datadir, FakeLlmClient(block=block))
    try:
        assert block.entered.wait(5)
        result = list_results(conn, batch_id)[0]
        assert update_result_review(
            conn,
            result["result_id"],
            result["revision"],
            dict(result, review={"accepted": True}),
        )
        reviewed = get_result(conn, result["result_id"])
        conn.execute(
            "UPDATE results SET doc = ? WHERE result_id = ?",
            (
                dumps(
                    dict(
                        reviewed,
                        status="queued",
                        extracted=None,
                        issues=[],
                        error=None,
                    )
                ),
                result["result_id"],
            ),
        )
        block.release.set()
        wait_for(conn, lambda: statuses(conn, batch_id) == ["passed"])
    finally:
        block.release.set()
        worker.stop(1)

    assert list_results(conn, batch_id)[0]["review"] == {"accepted": True}


def test_unexpected_exception_fails_document_and_worker_continues_without_text_in_logs(
    store, caplog, monkeypatch
):
    conn, datadir = store
    caplog.set_level(logging.INFO, logger="privasheet.pipeline.worker")
    batch_id = enqueue(conn, datadir, "bad.png", "good.png")
    real_process_document = worker_module.process_document

    def process_or_raise(conn, datadir, result, template, **kwargs):
        if result["source_file"] == "bad.png":
            raise RuntimeError("10x12 document text should stay private")
        return real_process_document(conn, datadir, result, template, **kwargs)

    monkeypatch.setattr(worker_module, "process_document", process_or_raise)
    worker = start_worker(datadir, FakeLlmClient())
    try:
        wait_for(conn, lambda: statuses(conn, batch_id) == ["failed", "passed"])
    finally:
        worker.stop(1)

    result = list_results(conn, batch_id)[0]
    assert result["error"] == {"code": PROCESSING_ERROR, "detail": "RuntimeError"}
    assert any("RuntimeError" in record.getMessage() for record in caplog.records)
    assert all("10x12" not in record.getMessage() for record in caplog.records)


def test_duplicate_upload_needs_review_and_is_archived(store):
    conn, datadir = store
    first_id = enqueue(conn, datadir, "first.png")
    worker = start_worker(datadir, FakeLlmClient())
    try:
        wait_for(conn, lambda: statuses(conn, first_id) == ["passed"])
        duplicate_id = enqueue(
            conn, datadir, "duplicate.png", color=_color_for_name("first.png")
        )
        worker.wake()
        wait_for(conn, lambda: statuses(conn, duplicate_id) == ["needs_review"])
    finally:
        worker.stop(1)

    result = list_results(conn, duplicate_id)[0]
    assert result["issues"][0]["code"] == DUPLICATE_DOCUMENT
    document = get_document(conn, result["document_id"])
    assert document["path"].startswith("archive/")


def test_timeout_document_needs_review_and_worker_continues(store):
    conn, datadir = store
    batch_id = enqueue(conn, datadir, "slow.png", "next.png")
    worker = start_worker(
        datadir,
        FakeLlmClient(),
        timeout_s=1,
        engine=SLEEPING_OCR_ENGINE,
    )
    try:
        wait_for(
            conn,
            lambda: statuses(conn, batch_id) == ["needs_review", "needs_review"],
            8,
        )
    finally:
        worker.stop(1)

    results = list_results(conn, batch_id)
    assert results[0]["issues"][0]["code"] == PROCESSING_TIMEOUT
    assert results[1]["issues"][0]["code"] == PROCESSING_TIMEOUT


def test_stop_during_slow_ocr_returns_and_leaves_result_processing(store):
    conn, datadir = store
    batch_id = enqueue(conn, datadir, "slow.png")
    worker = start_worker(
        datadir,
        FakeLlmClient(),
        timeout_s=30,
        engine=SLEEPING_OCR_ENGINE,
    )
    wait_for(conn, lambda: statuses(conn, batch_id) == ["processing"])

    worker.stop(timeout=0.5)

    assert not worker.is_running()
    assert statuses(conn, batch_id) == ["processing"]


def test_worker_without_datadir_lock_refuses_to_start(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    conn = open_db(datadir.db_path)
    migrate(conn)
    conn.close()
    worker = Worker(datadir.root, datadir.db_path, settings(), FakeLlmClient())

    with pytest.raises(RuntimeError, match="lock"):
        worker.start()


def test_start_twice_is_an_error(store):
    _conn, datadir = store
    worker = start_worker(datadir, FakeLlmClient())
    try:
        with pytest.raises(RuntimeError, match="already started"):
            worker.start()
    finally:
        worker.stop(1)
