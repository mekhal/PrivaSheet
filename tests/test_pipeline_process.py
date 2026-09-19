from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from privasheet.ingest.checks import Limits
from privasheet.llm import LlmInvalidResponse, LlmTimeout, LlmUnavailable
from privasheet.ocr.engine import RawBox
from privasheet.ocr.runner import OcrCancelled
from privasheet.pipeline.datadir import DataDir
from privasheet.pipeline.intake import Upload, create_batch
from privasheet.pipeline.process import process_document
from privasheet.store import migrate, open_db
from privasheet.store.repo import (
    claim_next_queued,
    commit_worker_outcome,
    get_document,
    get_snapshot,
    insert_template,
)
from privasheet.validate.checks import (
    AI_UNCERTAIN,
    DUPLICATE_DOCUMENT,
    PROCESSING_TIMEOUT,
)

OCR_ENGINE = "tests.test_pipeline_process:make_engine"
RAISING_OCR_ENGINE = "tests.fake_ocr:make_raising_engine"
SLEEPING_OCR_ENGINE = "tests.fake_ocr:make_sleeping_engine"


@pytest.fixture
def store(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    conn = open_db(datadir.db_path)
    migrate(conn)
    insert_template(conn, template_doc())
    try:
        yield conn, datadir
    finally:
        conn.close()


class FakeLlmClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, timeout=None):
        self.calls.append({"messages": messages, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class StepClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class AdvancingTimeoutLlmClient(FakeLlmClient):
    def __init__(self, clock, seconds):
        super().__init__()
        self.clock = clock
        self.seconds = seconds

    def chat_json(self, messages, timeout=None):
        self.calls.append({"messages": messages, "timeout": timeout})
        self.clock.advance(self.seconds)
        raise LlmTimeout("upstream timeout")


class HighConfidenceOcrEngine:
    name = "fake-ocr"
    version = "1.0"
    config_sha256 = "fake-config"

    def models(self):
        return [
            {"name": "det", "sha256": "fake-det"},
            {"name": "rec", "sha256": "fake-rec"},
        ]

    def recognize(self, image):
        width, height = image.size
        return [
            RawBox(
                quad_px=(
                    (0, 0),
                    (width / 2, 0),
                    (width / 2, height / 2),
                    (0, height / 2),
                ),
                text=f"{width}x{height}",
                score=0.99,
            )
        ]


def make_engine():
    return HighConfidenceOcrEngine()


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
            },
            {
                "key": "amount",
                "type": "decimal",
                "required": False,
                "description": "Optional amount.",
                "hint": {"labels": ["Amount"]},
            },
        ],
        "tables": [],
        "match": {"min_key_label_ratio": 0},
    }


def valid_response():
    return {
        "fields": {
            "size": {"box_ids": ["p1-b0000"], "span": "10x12"},
            "amount": {"missing": True},
        },
        "tables": {},
    }


def make_png(path: Path, size=(10, 12), color=(80, 120, 160), comment=None):
    metadata = None
    if comment is not None:
        metadata = PngInfo()
        metadata.add_text("Comment", comment)
    Image.new("RGB", size, color).save(path, format="PNG", pnginfo=metadata)


def claim_upload(
    conn, datadir, source_file="invoice.png", *, size=(10, 12), comment=None
):
    upload = datadir.process / source_file
    make_png(upload, size=size, comment=comment)
    with upload.open("rb") as stream:
        create_batch(
            conn,
            datadir.root,
            "invoice-a",
            1,
            [Upload(source_file, stream)],
            now=lambda: "2026-09-19T00:00:00Z",
        )
    return claim_next_queued(conn, "2026-09-19T00:01:00Z")


def run_process(
    conn,
    datadir,
    result,
    client,
    *,
    engine=OCR_ENGINE,
    timeout_s=30,
    now=lambda: "2026-09-19T00:02:00Z",
):
    return process_document(
        conn,
        datadir.root,
        result,
        template_doc(),
        llm_client=client,
        model="fake-model",
        limits=Limits(),
        timeout_s=timeout_s,
        ocr_engine=engine,
        now=now,
    )


def test_good_invoice_passes_and_writes_snapshot_images_and_document_link(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    client = FakeLlmClient(valid_response())

    outcome = run_process(conn, datadir, result, client)

    assert outcome["status"] == "passed"
    assert outcome["snapshot_id"].startswith("sha256:")
    assert outcome["llm"] == {"model": "fake-model", "prompt_version": 1}
    assert outcome["error"] is None
    assert outcome["issues"] == []
    assert outcome["extracted"]["fields"]["size"] == {
        "box_ids": ["p1-b0000"],
        "span": "10x12",
        "raw": "10x12",
        "value": "10x12",
    }
    snapshot = get_snapshot(conn, outcome["snapshot_id"])
    assert snapshot is not None
    image_path = datadir.root / snapshot["pages"][0]["image"]
    assert image_path.is_file()
    assert (
        get_document(conn, result["document_id"])["snapshot_id"]
        == outcome["snapshot_id"]
    )


def test_uncertain_field_needs_review_with_ai_uncertain(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    response = valid_response()
    response["fields"]["amount"] = {"uncertain": True, "reason": "faint amount"}

    outcome = run_process(conn, datadir, result, FakeLlmClient(response))

    assert outcome["status"] == "needs_review"
    assert outcome["issues"][0]["code"] == AI_UNCERTAIN


def test_missing_required_field_needs_review_with_required_missing(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    response = valid_response()
    response["fields"]["size"] = {"missing": True}

    outcome = run_process(conn, datadir, result, FakeLlmClient(response))

    assert outcome["status"] == "needs_review"
    assert outcome["issues"][0]["code"] == "REQUIRED_MISSING"


def test_duplicate_document_needs_review_without_ocr_or_llm(store):
    conn, datadir = store
    first = claim_upload(conn, datadir, "first.png")
    first_outcome = run_process(conn, datadir, first, FakeLlmClient(valid_response()))
    assert commit_worker_outcome(
        conn, first_outcome["result_id"], first["revision"], first["job"], first_outcome
    )
    second = claim_upload(conn, datadir, "second.png")
    client = FakeLlmClient(valid_response())

    outcome = run_process(
        conn,
        datadir,
        second,
        client,
        engine="tests.fake_ocr:make_raising_engine",
    )

    assert outcome["status"] == "needs_review"
    assert outcome["extracted"] is None
    assert outcome["error"] is None
    assert outcome["issues"] == [
        {
            "code": DUPLICATE_DOCUMENT,
            "target": "document",
            "detail": f"Duplicate of document in batch {first['batch_id']}: first.png.",
            "document_id": first["document_id"],
        }
    ]
    assert client.calls == []


@pytest.mark.parametrize(
    "response,code",
    [
        (LlmUnavailable("offline"), "LLM_UNAVAILABLE"),
        ({}, "LLM_INVALID_RESPONSE"),
        (LlmInvalidResponse("not json"), "LLM_INVALID_RESPONSE"),
    ],
)
def test_llm_failures_map_to_failed_codes(store, response, code):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    responses = [response, response] if response == {} else [response]

    outcome = run_process(conn, datadir, result, FakeLlmClient(*responses))

    assert outcome["status"] == "failed"
    assert outcome["extracted"] is None
    assert outcome["issues"] == []
    assert outcome["error"]["code"] == code
    assert "10x12" not in outcome["error"]["detail"]


def test_llm_timeout_before_budget_maps_to_unavailable(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)

    outcome = run_process(conn, datadir, result, FakeLlmClient(LlmTimeout("upstream")))

    assert outcome["status"] == "failed"
    assert outcome["error"]["code"] == "LLM_UNAVAILABLE"


def test_ocr_error_maps_to_ocr_failed(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)

    outcome = run_process(
        conn,
        datadir,
        result,
        FakeLlmClient(valid_response()),
        engine=RAISING_OCR_ENGINE,
    )

    assert outcome["status"] == "failed"
    assert outcome["error"]["code"] == "OCR_FAILED"
    assert "10x12" not in outcome["error"]["detail"]


def test_ocr_timeout_returns_processing_timeout_and_cleans_tempdir(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)

    outcome = run_process(
        conn,
        datadir,
        result,
        FakeLlmClient(valid_response()),
        engine=SLEEPING_OCR_ENGINE,
        timeout_s=1,
    )

    assert outcome["status"] == "needs_review"
    assert outcome["snapshot_id"] is None
    assert outcome["extracted"] is None
    assert outcome["error"] is None
    assert len(outcome["issues"]) == 1
    issue = outcome["issues"][0]
    assert issue["code"] == PROCESSING_TIMEOUT
    assert "OCR" in issue["detail"]
    assert "1 s" in issue["detail"]
    assert_no_tempdirs(datadir)


def test_identical_page_pixels_reuse_existing_snapshot_without_second_row(store):
    conn, datadir = store
    first = claim_upload(conn, datadir, "first.png")
    first_outcome = run_process(conn, datadir, first, FakeLlmClient(valid_response()))
    assert commit_worker_outcome(
        conn, first_outcome["result_id"], first["revision"], first["job"], first_outcome
    )
    second = claim_upload(conn, datadir, "second.png", comment="different bytes")

    outcome = run_process(conn, datadir, second, FakeLlmClient(valid_response()))

    assert outcome["status"] == "passed"
    assert outcome["snapshot_id"] == first_outcome["snapshot_id"]
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1


def test_budget_spent_before_llm_returns_timeout_with_snapshot_kept(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    clock = StepClock(100.0, 100.0, 131.0)
    client = FakeLlmClient(valid_response())

    outcome = process_document(
        conn,
        datadir.root,
        result,
        template_doc(),
        llm_client=client,
        model="fake-model",
        limits=Limits(),
        timeout_s=30,
        ocr_engine=OCR_ENGINE,
        now=lambda: "2026-09-19T00:02:00Z",
        clock=clock,
    )

    assert outcome["status"] == "needs_review"
    assert outcome["snapshot_id"] is not None
    assert client.calls == []
    assert outcome["issues"][0]["code"] == PROCESSING_TIMEOUT
    assert "LLM" in outcome["issues"][0]["detail"]


def test_llm_deadline_uses_real_monotonic_remaining_budget(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    client = FakeLlmClient(valid_response())

    outcome = process_document(
        conn,
        datadir.root,
        result,
        template_doc(),
        llm_client=client,
        model="fake-model",
        limits=Limits(),
        timeout_s=30,
        ocr_engine=OCR_ENGINE,
        now=lambda: "2026-09-19T00:02:00Z",
        clock=StepClock(100.0, 100.0, 105.0),
    )

    assert outcome["status"] == "passed"
    assert 20 <= client.calls[0]["timeout"] <= 30


def test_llm_timeout_after_budget_spent_returns_processing_timeout(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    clock = FakeClock(100.0)
    client = AdvancingTimeoutLlmClient(clock, 31.0)

    outcome = process_document(
        conn,
        datadir.root,
        result,
        template_doc(),
        llm_client=client,
        model="fake-model",
        limits=Limits(),
        timeout_s=30,
        ocr_engine=OCR_ENGINE,
        now=lambda: "2026-09-19T00:02:00Z",
        clock=clock,
    )

    assert outcome["status"] == "needs_review"
    assert outcome["snapshot_id"] is not None
    assert outcome["extracted"] is None
    assert outcome["error"] is None
    assert outcome["issues"] == [
        {
            "code": PROCESSING_TIMEOUT,
            "target": "document",
            "detail": outcome["issues"][0]["detail"],
        }
    ]
    assert "LLM" in outcome["issues"][0]["detail"]


def test_cancelled_ocr_propagates_to_caller(store):
    conn, datadir = store
    result = claim_upload(conn, datadir)
    cancel = Event()
    cancel.set()

    with pytest.raises(OcrCancelled):
        process_document(
            conn,
            datadir.root,
            result,
            template_doc(),
            llm_client=FakeLlmClient(valid_response()),
            model="fake-model",
            limits=Limits(),
            timeout_s=30,
            ocr_engine=SLEEPING_OCR_ENGINE,
            now=lambda: "2026-09-19T00:02:00Z",
            cancel=cancel,
        )

    assert_no_tempdirs(datadir)


def test_success_duplicate_and_failure_leave_no_tempdirs(store):
    conn, datadir = store
    first = claim_upload(conn, datadir, "first.png")
    first_outcome = run_process(conn, datadir, first, FakeLlmClient(valid_response()))
    assert_no_tempdirs(datadir)
    assert commit_worker_outcome(
        conn, first_outcome["result_id"], first["revision"], first["job"], first_outcome
    )

    duplicate = claim_upload(conn, datadir, "duplicate.png")
    duplicate_outcome = run_process(
        conn,
        datadir,
        duplicate,
        FakeLlmClient(valid_response()),
        engine=RAISING_OCR_ENGINE,
    )
    assert duplicate_outcome["issues"][0]["code"] == DUPLICATE_DOCUMENT
    assert_no_tempdirs(datadir)

    failure = claim_upload(
        conn, datadir, "failure.png", size=(11, 13), comment="unique"
    )
    failed_outcome = run_process(
        conn,
        datadir,
        failure,
        FakeLlmClient(valid_response()),
        engine=RAISING_OCR_ENGINE,
    )
    assert failed_outcome["status"] == "failed"
    assert_no_tempdirs(datadir)


def assert_no_tempdirs(datadir):
    tmp_root = datadir.root / "tmp" / "ocr"
    if tmp_root.exists():
        assert list(tmp_root.iterdir()) == []
