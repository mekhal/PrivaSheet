"""End-to-end acceptance test for the pipeline (spec section 3, readiness finding 1).

Upload -> ingest -> ocr -> layout -> extractor -> validate -> store -> export, on
synthetic invoices, through the same ``PipelineRuntime`` the web app starts.

Two collaborators are replaced by fakes because neither the real RapidOCR engine nor a
real LLM is available in this environment:

* OCR: ``tests.fake_ocr.ScriptedOcrEngine`` runs in the real OCR child process. Its boxes
  are the very lines ``draw_page`` drew on the invoice image, so the scenario stays honest.
* LLM: ``FakeLlm`` answers by reading the ``ocr_lines`` of the prompt it receives and
  citing the box ids it finds there, so a wrong box id would fail grounding.

Everything else (intake, worker, child-process OCR, snapshot, layout, extractor, validation,
store, recovery, export) is the production code.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from privasheet import export, templates
from privasheet.llm import LlmUnavailable
from privasheet.pipeline import intake
from privasheet.pipeline.datadir import AlreadyRunning, DataDir
from privasheet.pipeline.runtime import PipelineRuntime
from privasheet.store import StoreError, open_db, repo
from privasheet.validate.checks import DUPLICATE_DOCUMENT, PROCESSING_TIMEOUT
from privasheet.web.settings import Settings
from tests.fake_ocr import SCRIPT_ENV, draw_page

OCR_ENGINE = "tests.fake_ocr:make_scripted_engine"
WAIT_S = 60
POLL_S = 0.05


# --- synthetic data ------------------------------------------------------------------


@dataclass(frozen=True)
class Invoice:
    number: str
    date_text: str  # as printed, DD/MM/YYYY
    date_iso: str
    total_text: str  # as printed, with a thousands separator
    total: str  # plain decimal
    items: tuple[tuple[str, str, str], ...]  # description, printed amount, plain amount

    def lines(self) -> list[tuple[str, int, int]]:
        lines = [
            ("Invoice No:", 40, 40),
            (self.number, 240, 40),
            ("Date:", 40, 100),
            (self.date_text, 240, 100),
            ("Description", 40, 200),
            ("Amount", 500, 200),
        ]
        for index, (description, printed, _) in enumerate(self.items):
            lines.append((description, 40, 250 + 45 * index))
            lines.append((printed, 500, 250 + 45 * index))
        lines += [("Total:", 40, 550), (self.total_text, 240, 550)]
        return lines

    def expected_fields(self) -> dict:
        return {
            "invoice_no": self.number,
            "invoice_date": self.date_iso,
            "total": self.total,
        }

    def expected_tables(self) -> dict:
        return {
            "line_items": [
                {"description": description, "amount": amount}
                for description, _, amount in self.items
            ]
        }


INVOICES = (
    Invoice(
        "INV-1001",
        "05/03/2026",
        "2026-03-05",
        "1,234.50",
        "1234.50",
        (("Widget", "1,000.00", "1000.00"), ("Gadget", "234.50", "234.50")),
    ),
    Invoice(
        "INV-2002",
        "17/11/2025",
        "2025-11-17",
        "99.90",
        "99.90",
        (("Sprocket", "99.90", "99.90"),),
    ),
    Invoice(
        "INV-3003",
        "01/01/2026",
        "2026-01-01",
        "45,000.00",
        "45000.00",
        (
            ("Turbine", "40,000.00", "40000.00"),
            ("Bolt", "3,500.25", "3500.25"),
            ("Washer", "1,499.75", "1499.75"),
        ),
    ),
)


def template_doc() -> dict:
    doc = {
        "template_id": "invoice",
        "version": 1,
        "version_label": "1.0.20260919",
        "name": "Invoice",
        "created_at": "2026-09-19T00:00:00Z",
        "fields": [
            {
                "key": "invoice_no",
                "type": "text",
                "required": True,
                "key_label": True,
                "description": "The invoice number.",
                "hint": {"labels": ["Invoice No"]},
            },
            {
                "key": "invoice_date",
                "type": "date",
                "format": "DD/MM/YYYY",
                "required": False,
                "description": "The invoice date.",
                "hint": {"labels": ["Date"]},
            },
            {
                "key": "total",
                "type": "decimal",
                "required": True,
                "key_label": True,
                "description": "The invoice total.",
                "hint": {"labels": ["Total"]},
            },
        ],
        "tables": [
            {
                "key": "line_items",
                "description": "The invoice line items.",
                "required": False,
                "columns": [
                    {"key": "description", "type": "text", "description": "Item."},
                    {"key": "amount", "type": "decimal", "description": "Amount."},
                ],
            }
        ],
    }
    templates.ensure_valid(doc)
    return doc


def png_bytes(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --- fake LLM ------------------------------------------------------------------------


class FakeLlm:
    """Answers the extraction prompt from the OCR lines it contains."""

    def __init__(self) -> None:
        self.available = True
        self.overrides: dict[str, dict] = {}  # invoice number -> field key -> entry
        self.calls: list[str] = []  # invoice number of each answered prompt
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def chat_json(self, messages, timeout=None):
        self.entered.set()
        assert self.release.wait(WAIT_S)
        if not self.available:
            raise LlmUnavailable("fake LLM is down")
        payload = json.loads(messages[1]["content"])
        lines = [line.split(" | ", 3) for line in payload["ocr_lines"]]
        boxes = [(line[0], line[3]) for line in lines]
        texts = [text for _, text in boxes]

        def grounded(box):
            return {"box_ids": [box[0]], "span": box[1]}

        def after(label):
            if label not in texts:
                return {"missing": True}
            return grounded(boxes[texts.index(label) + 1])

        start, end = texts.index("Amount") + 1, texts.index("Total:")
        fields = {
            "invoice_no": after("Invoice No:"),
            "invoice_date": after("Date:"),
            "total": after("Total:"),
        }
        number = boxes[texts.index("Invoice No:") + 1][1]
        fields.update(self.overrides.get(number, {}))
        self.calls.append(number)
        rows = [
            {"description": grounded(boxes[i]), "amount": grounded(boxes[i + 1])}
            for i in range(start, end, 2)
        ]
        return {"fields": fields, "tables": {"line_items": rows}}


# --- harness -------------------------------------------------------------------------


class Env:
    def __init__(self, tmp_path, llm: FakeLlm, document_timeout_s: int) -> None:
        self.tmp_path = tmp_path
        self.llm = llm
        self.script = tmp_path / "ocr-script.json"
        self.settings = Settings(
            host="127.0.0.1",
            port=8765,
            allowed_hosts=("testserver",),
            data_dir=tmp_path / "data",
            base_url="http://127.0.0.1:11434",
            model="fake-model",
            document_timeout_s=document_timeout_s,
        )
        self.datadir = DataDir(self.settings.data_dir)
        self.runtimes: list[PipelineRuntime] = []

    def runtime(self) -> PipelineRuntime:
        runtime = PipelineRuntime(
            self.settings, ocr_engine=OCR_ENGINE, llm_client=self.llm
        )
        self.runtimes.append(runtime)
        return runtime

    def start(self) -> PipelineRuntime:
        runtime = self.runtime()
        runtime.start()
        with self.db() as conn:
            if repo.get_template(conn, "invoice", 1) is None:
                repo.insert_template(conn, template_doc())
        return runtime

    def db(self):
        return _Conn(self.datadir.db_path)

    def draw(self, invoice: Invoice, *, sleep_s: float = 0) -> bytes:
        return png_bytes(draw_page(invoice.lines(), self.script, sleep_s=sleep_s))

    def upload(self, *files: tuple[str, bytes], batch_id: str | None = None):
        uploads = [intake.Upload(name, io.BytesIO(data)) for name, data in files]
        with self.db() as conn:
            if batch_id is None:
                return intake.create_batch(
                    conn, self.datadir.root, "invoice", 1, uploads
                )
            return intake.add_files(conn, self.datadir.root, batch_id, uploads)

    def wait_idle(self, expected_results: int) -> None:
        """Wait, bounded, until every expected result has left the queue."""
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            with self.db() as conn:
                counts = repo.count_results_by_status(conn)
            if sum(counts.values()) == expected_results and not (
                counts.get("queued") or counts.get("processing")
            ):
                return
            time.sleep(POLL_S)
        raise AssertionError(f"queue did not drain: {counts}")

    def results(self, batch_id: str) -> dict[str, dict]:
        """Results keyed by source file name."""
        with self.db() as conn:
            return {r["source_file"]: r for r in repo.list_results(conn, batch_id)}

    def export_snapshot(self, batch_id: str):
        with self.db() as conn, repo.read_transaction(conn):
            manifest = repo.get_batch(conn, batch_id)
            results = {r["result_id"]: r for r in repo.list_results(conn, batch_id)}
            template = repo.get_template(
                conn, manifest["template"]["id"], manifest["template"]["version"]
            )
        return manifest, results, template


class _Conn:
    """Short-lived connection usable as a context manager."""

    def __init__(self, path) -> None:
        self.conn = open_db(path)

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc) -> None:
        self.conn.close()


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    monkeypatch.setenv(SCRIPT_ENV, str(tmp_path / "ocr-script.json"))
    envs = []

    def factory(document_timeout_s: int = 60, llm: FakeLlm | None = None) -> Env:
        env = Env(tmp_path, llm or FakeLlm(), document_timeout_s)
        envs.append(env)
        return env

    yield factory
    for env in envs:
        env.llm.release.set()
        for runtime in env.runtimes:
            runtime.stop()


@pytest.fixture
def env(make_env) -> Env:
    return make_env()


def expected_line(manifest, document_id, source_file, invoice, *, reviewed=False):
    line = {
        "schema_version": 1,
        "batch_id": manifest["batch_id"],
        "document_id": document_id,
        "source_file": source_file,
        "template": {"id": "invoice", "version": 1},
        "human_reviewed": reviewed,
        "fields": invoice.expected_fields(),
        "tables": invoice.expected_tables(),
    }
    return json.dumps(line, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def issue_codes(result) -> list[str]:
    return [issue["code"] for issue in result["issues"]]


# --- scenarios -----------------------------------------------------------------------


def test_happy_path_three_invoices_upload_to_jsonl(env):
    runtime = env.start()
    files = [(f"inv-{i}.png", env.draw(inv)) for i, inv in enumerate(INVOICES, 1)]
    intake_result = env.upload(*files)
    assert len(intake_result.accepted) == 3 and not intake_result.rejected
    batch_id = intake_result.batch_id

    env.wait_idle(3)
    runtime.stop()  # joins the worker, so post-commit archiving has finished

    results = env.results(batch_id)
    assert {name: r["status"] for name, r in results.items()} == {
        "inv-1.png": "passed",
        "inv-2.png": "passed",
        "inv-3.png": "passed",
    }
    assert all(r["issues"] == [] and r["error"] is None for r in results.values())
    assert env.llm.calls == [inv.number for inv in INVOICES]

    with env.db() as conn:
        stored_paths = repo.referenced_file_paths(conn)
        for result in results.values():
            document = repo.get_document(conn, result["document_id"])
            assert document["snapshot_id"] == result["snapshot_id"] is not None
            assert document["path"].startswith("archive/")
            snapshot = repo.get_snapshot(conn, result["snapshot_id"])
            assert snapshot["engine"]["name"] == "fake-ocr"
            for page in snapshot["pages"]:
                assert page["boxes"]
                assert env.datadir.resolve(page["image"]).is_file()
    assert len(stored_paths) == 3 + 3  # three archived uploads, three page images
    for stored in stored_paths:
        assert env.datadir.resolve(stored).is_file()  # raises if it leaves the data dir
    assert list(env.datadir.process.iterdir()) == []

    manifest, by_result_id, template = env.export_snapshot(batch_id)
    selected = export.selectable_documents(manifest, by_result_id)
    assert selected == [entry["document_id"] for entry in manifest["documents"]]
    text = export.build_jsonl(manifest, by_result_id, template, selected)
    path = env.datadir.exports / export.export_filename(
        batch_id, datetime(2026, 9, 19, tzinfo=UTC)
    )
    export.write_atomic(path, text)

    expected = "".join(
        expected_line(manifest, results[name]["document_id"], name, invoice) + "\n"
        for name, invoice in zip(
            ("inv-1.png", "inv-2.png", "inv-3.png"), INVOICES, strict=True
        )
    )
    assert path.read_text(encoding="utf-8") == expected
    assert '"total":"1234.50"' in expected and '"invoice_date":"2026-03-05"' in expected


def test_bad_uploads_are_rejected_and_duplicate_needs_review(env):
    runtime = env.start()

    rejected = env.upload(
        ("corrupt.png", b"\x89PNG\r\n\x1a\n" + b"not really a png"),
        ("notes.txt", b"just some text"),
    )
    assert rejected.batch_id is None and rejected.accepted == []
    assert {r.source_file for r in rejected.rejected} == {"corrupt.png", "notes.txt"}
    with env.db() as conn:
        for table in ("documents", "results", "batches"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert list(env.datadir.process.iterdir()) == []

    data = env.draw(INVOICES[0])
    batch_id = env.upload(("first.png", data), ("second.png", data)).batch_id
    env.wait_idle(2)
    runtime.stop()

    results = env.results(batch_id)
    first, second = results["first.png"], results["second.png"]
    assert first["status"] == "passed"
    assert second["status"] == "needs_review"
    assert issue_codes(second) == [DUPLICATE_DOCUMENT]
    assert second["issues"][0]["document_id"] == first["document_id"]
    assert env.llm.calls == [INVOICES[0].number]  # the duplicate never reaches the LLM

    manifest, by_result_id, template = env.export_snapshot(batch_id)
    assert export.selectable_documents(manifest, by_result_id) == [first["document_id"]]
    with pytest.raises(export.ExportNotAllowed):
        export.build_jsonl(manifest, by_result_id, template, [second["document_id"]])


def test_uncertain_and_missing_answers_need_review_until_reviewed(env):
    env.llm.overrides = {
        INVOICES[0].number: {"invoice_no": {"uncertain": True, "reason": "smudged"}},
        INVOICES[1].number: {"total": {"missing": True}},
    }
    runtime = env.start()
    files = [(f"inv-{i}.png", env.draw(inv)) for i, inv in enumerate(INVOICES, 1)]
    batch_id = env.upload(*files).batch_id
    env.wait_idle(3)
    runtime.stop()

    results = env.results(batch_id)
    uncertain, missing, clean = (results[f"inv-{i}.png"] for i in (1, 2, 3))
    assert uncertain["status"] == missing["status"] == "needs_review"
    assert issue_codes(uncertain) == ["AI_UNCERTAIN"]
    assert issue_codes(missing) == ["REQUIRED_MISSING"]
    assert clean["status"] == "passed"

    manifest, by_result_id, template = env.export_snapshot(batch_id)
    assert export.selectable_documents(manifest, by_result_id) == [clean["document_id"]]
    for result in (uncertain, missing):
        with pytest.raises(export.ExportNotAllowed):
            export.build_jsonl(
                manifest, by_result_id, template, [result["document_id"]]
            )

    # A saved review makes the same document exportable, with the reviewed values.
    invoice = INVOICES[0]
    review = {"fields": invoice.expected_fields(), "tables": invoice.expected_tables()}
    reviewed = {**uncertain, "status": "reviewed", "review": review}
    with env.db() as conn:
        assert repo.update_result_review(
            conn, uncertain["result_id"], uncertain["revision"], reviewed
        )
    manifest, by_result_id, template = env.export_snapshot(batch_id)
    text = export.build_jsonl(
        manifest, by_result_id, template, [uncertain["document_id"]]
    )
    assert text == (
        expected_line(
            manifest, uncertain["document_id"], "inv-1.png", invoice, reviewed=True
        )
        + "\n"
    )
    with pytest.raises(export.ExportNotAllowed):  # the other one still has no review
        export.build_jsonl(manifest, by_result_id, template, [missing["document_id"]])


def test_llm_outage_fails_document_and_retry_completes_it(env):
    env.llm.available = False
    runtime = env.start()
    batch_id = env.upload(("inv.png", env.draw(INVOICES[0]))).batch_id
    env.wait_idle(1)

    failed = env.results(batch_id)["inv.png"]
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "LLM_UNAVAILABLE"
    assert failed["job"] == 1
    manifest, by_result_id, _ = env.export_snapshot(batch_id)
    assert export.selectable_documents(manifest, by_result_id) == []

    env.llm.available = True
    with env.db() as conn:
        assert repo.retry_result(
            conn, failed["result_id"], failed["revision"], "2026-09-19T00:00:00Z"
        )
    env.wait_idle(1)
    runtime.stop()

    done = env.results(batch_id)["inv.png"]
    assert done["status"] == "passed" and done["error"] is None and done["issues"] == []
    assert done["job"] == 2 and done["result_id"] == failed["result_id"]
    with env.db() as conn:
        assert conn.execute("SELECT count(*) FROM results").fetchone()[0] == 1
    assert env.llm.calls == [INVOICES[0].number]


def test_ocr_timeout_needs_review_and_next_document_is_processed(make_env):
    env = make_env(document_timeout_s=1)
    runtime = env.start()
    slow = env.draw(INVOICES[0], sleep_s=30)
    fast = env.draw(INVOICES[1])
    started = time.monotonic()
    batch_id = env.upload(("slow.png", slow), ("fast.png", fast)).batch_id
    env.wait_idle(2)
    runtime.stop()

    assert time.monotonic() - started < 25  # the 30 s sleep was cut off
    results = env.results(batch_id)
    assert results["slow.png"]["status"] == "needs_review"
    assert issue_codes(results["slow.png"]) == [PROCESSING_TIMEOUT]
    assert results["fast.png"]["status"] == "passed"
    assert env.llm.calls == [INVOICES[1].number]


def test_restart_recovers_interrupted_work_and_removes_orphans(env):
    first = env.start()
    first.stop()

    # Crash state: a claimed (processing) result whose file was already moved to
    # archive/ but whose path was never recorded, plus an upload nothing refers to.
    batch_id = env.upload(("inv.png", env.draw(INVOICES[0]))).batch_id
    with env.db() as conn:
        claimed = repo.claim_next_queued(conn, "2026-09-19T00:00:00Z")
        assert claimed["status"] == "processing"
        document = repo.get_document(conn, claimed["document_id"])
    moved = env.datadir.archive / os.path.basename(document["path"])
    os.replace(env.datadir.resolve(document["path"]), moved)
    orphan = env.datadir.process / "doc_orphan.png"
    orphan.write_bytes(b"\x89PNG\r\n\x1a\n orphan")

    second = env.runtime()
    second.start()
    with pytest.raises(AlreadyRunning):
        env.runtime().start()
    env.wait_idle(1)
    second.stop()

    result = env.results(batch_id)["inv.png"]
    assert result["status"] == "passed" and result["job"] == 1
    assert not orphan.exists()
    with env.db() as conn:
        document = repo.get_document(conn, result["document_id"])
    assert env.datadir.resolve(document["path"]) == moved.resolve() and moved.is_file()
    assert env.llm.calls == [INVOICES[0].number]  # processed exactly once


def test_batch_editing_removed_document_never_runs_and_added_one_does(env):
    env.llm.release.clear()  # hold the worker inside the first document
    runtime = env.start()
    files = [(f"inv-{i}.png", env.draw(inv)) for i, inv in enumerate(INVOICES, 1)]
    batch_id = env.upload(*files).batch_id
    assert env.llm.entered.wait(WAIT_S)

    by_name = env.results(batch_id)
    assert by_name["inv-1.png"]["status"] == "processing"
    with env.db() as conn:
        with pytest.raises(StoreError):  # the running document is locked
            intake.remove_file(
                conn, env.datadir.root, batch_id, by_name["inv-1.png"]["document_id"]
            )
        removed = by_name["inv-3.png"]
        removed_path = env.datadir.resolve(
            repo.get_document(conn, removed["document_id"])["path"]
        )
        assert removed_path.is_file()
        intake.remove_file(conn, env.datadir.root, batch_id, removed["document_id"])
        assert repo.get_document(conn, removed["document_id"]) is None
    assert not removed_path.exists()

    extra = replace(INVOICES[2], number="INV-4004")
    added = env.upload(("inv-4.png", env.draw(extra)), batch_id=batch_id)
    assert len(added.accepted) == 1
    env.llm.release.set()
    env.wait_idle(3)
    runtime.stop()

    results = env.results(batch_id)
    assert list(results) == ["inv-1.png", "inv-2.png", "inv-4.png"]
    assert {r["status"] for r in results.values()} == {"passed"}
    assert env.llm.calls == ["INV-1001", "INV-2002", "INV-4004"]
