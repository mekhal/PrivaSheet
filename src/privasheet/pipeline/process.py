"""Process one claimed pipeline document into a worker outcome."""

from __future__ import annotations

import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from privasheet import extractor, llm
from privasheet.ingest.checks import detect_type
from privasheet.ingest.render import save_png_atomic
from privasheet.ocr import snapshot as ocr_snapshot
from privasheet.ocr.engine import OcrError
from privasheet.ocr.runner import OcrCancelled, OcrTimeout, run_ocr_job
from privasheet.store import repo
from privasheet.validate import checks

DEFAULT_OCR_ENGINE = "privasheet.ocr.engine:make_rapidocr_engine"
PROMPT_VERSION = 1


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def process_document(
    conn,
    datadir,
    result,
    template,
    *,
    llm_client,
    model,
    limits,
    timeout_s,
    ocr_engine=None,
    clock=time.monotonic,
    now=_now_iso,
    cancel=None,
) -> dict:
    """Process one already-claimed result and return the complete outcome doc."""

    started_at = clock()
    document = repo.get_document(conn, result["document_id"])
    if document is None:
        raise ValueError(f"document {result['document_id']} not found")

    duplicate = _duplicate_issue(conn, document)
    if duplicate is not None:
        return _outcome(
            result,
            status="needs_review",
            snapshot_id=result.get("snapshot_id"),
            extracted=None,
            issues=[duplicate],
            error=None,
            model=model,
            updated_at=now(),
        )

    snapshot_id = result.get("snapshot_id") or document.get("snapshot_id")
    snapshot = repo.get_snapshot(conn, snapshot_id) if snapshot_id else None

    try:
        if snapshot is None:
            snapshot = _ocr_snapshot(
                conn,
                Path(datadir),
                document,
                limits,
                _remaining_budget(started_at, timeout_s, clock),
                ocr_engine or DEFAULT_OCR_ENGINE,
                now(),
                cancel,
            )
            snapshot_id = snapshot["snapshot_id"]

        deadline = started_at + timeout_s
        if clock() >= deadline:
            return _timeout_outcome(
                result, model, snapshot_id, "LLM", None, started_at, clock, now
            )

        extracted = extractor.extract(template, snapshot, llm_client, deadline)
        values, issues = checks.check_extraction(template, snapshot, extracted)
        extracted = _with_canonical_values(extracted, values)
        issue_dicts = [asdict(issue) for issue in issues]
        return _outcome(
            result,
            status="passed" if not issue_dicts else "needs_review",
            snapshot_id=snapshot_id,
            extracted=extracted,
            issues=issue_dicts,
            error=None,
            model=model,
            updated_at=now(),
        )
    except OcrCancelled:
        raise
    except OcrTimeout as exc:
        return _timeout_outcome(
            result, model, snapshot_id, "OCR", exc, started_at, clock, now
        )
    except llm.LlmTimeout as exc:
        if clock() >= started_at + timeout_s:
            return _timeout_outcome(
                result, model, snapshot_id, "LLM", None, started_at, clock, now
            )
        return _failed_outcome(
            result, model, snapshot_id, "LLM_UNAVAILABLE", str(exc), now
        )
    except llm.LlmUnavailable as exc:
        return _failed_outcome(
            result, model, snapshot_id, "LLM_UNAVAILABLE", str(exc), now
        )
    except (extractor.ExtractionFailed, llm.LlmInvalidResponse) as exc:
        detail = getattr(exc, "detail", str(exc))
        return _failed_outcome(
            result, model, snapshot_id, "LLM_INVALID_RESPONSE", detail, now
        )
    except OcrError as exc:
        return _failed_outcome(result, model, snapshot_id, exc.code, exc.detail, now)


def _duplicate_issue(conn, document: dict) -> dict | None:
    sha256 = document.get("sha256")
    if not sha256:
        return None
    matches = repo.find_documents_by_sha256(
        conn, sha256, before_document_id=document["document_id"]
    )
    if not matches:
        return None
    earlier = matches[0]
    earlier_result = repo.find_result_for_document(conn, earlier["document_id"]) or {}
    return {
        "code": checks.DUPLICATE_DOCUMENT,
        "target": "document",
        "detail": (
            "Duplicate of document in batch "
            f"{earlier_result.get('batch_id', 'unknown')}: "
            f"{earlier.get('source_file') or 'unknown file'}."
        ),
        "document_id": earlier["document_id"],
    }


def _ocr_snapshot(
    conn,
    datadir: Path,
    document: dict,
    limits,
    timeout_s: float,
    engine: str,
    created_at: str,
    cancel,
) -> dict:
    path = datadir / document["path"]
    kind = _kind(path)
    with tempfile.TemporaryDirectory(dir=datadir) as tmpdir:
        job = run_ocr_job(
            path,
            kind,
            limits,
            tmpdir,
            timeout_s,
            engine=engine,
            cancel=cancel,
        )
        existing = repo.get_snapshot(conn, job.snapshot_id)
        if existing is not None:
            repo.set_document_snapshot(conn, document["document_id"], job.snapshot_id)
            return existing

        snapshot = ocr_snapshot.build_snapshot(
            job.snapshot_id,
            _engine_from_metadata(job.engine),
            job.pages,
            created_at,
        )
        _store_page_images(datadir, job.pages, snapshot)
        repo.insert_snapshot(conn, snapshot)
        repo.set_document_snapshot(conn, document["document_id"], job.snapshot_id)
        return snapshot


def _kind(path: Path) -> str:
    with path.open("rb") as stream:
        kind = detect_type(stream.read(16))
    if kind is None:
        raise OcrError("DOCUMENT_UNREADABLE", "Unsupported stored document type.")
    return kind


def _engine_from_metadata(metadata: dict):
    return SimpleNamespace(
        name=metadata["name"],
        version=metadata["version"],
        config_sha256=metadata["config_sha256"],
        models=lambda: metadata["models"],
    )


def _store_page_images(datadir: Path, pages: list[dict], snapshot: dict) -> None:
    for page, stored_page in zip(pages, snapshot["pages"], strict=True):
        with Image.open(page["tmp_image"]) as image:
            save_png_atomic(image, datadir / stored_page["image"])


def _with_canonical_values(extracted: dict, values: dict) -> dict:
    fields = {
        key: _with_value(evidence, values.get("fields", {}).get(key))
        for key, evidence in extracted.get("fields", {}).items()
    }
    tables = {}
    for table_key, rows in extracted.get("tables", {}).items():
        value_rows = values.get("tables", {}).get(table_key, [])
        tables[table_key] = [
            {
                key: _with_value(evidence, value_rows[index].get(key))
                for key, evidence in row.items()
            }
            for index, row in enumerate(rows)
        ]
    return {**extracted, "fields": fields, "tables": tables}


def _with_value(evidence, value):
    if isinstance(evidence, dict) and evidence.get("box_ids"):
        return {**evidence, "value": value}
    return evidence


def _remaining_budget(started_at, timeout_s, clock) -> float:
    return max(0.0, started_at + timeout_s - clock())


def _timeout_outcome(result, model, snapshot_id, stage, exc, started_at, clock, now):
    elapsed_s = getattr(exc, "elapsed_s", None)
    if elapsed_s is None:
        elapsed_s = max(0.0, clock() - started_at)
    progress = ""
    page = getattr(exc, "page", None)
    total = getattr(exc, "total", None)
    if page is not None and total is not None:
        progress = f" (page {page} of {total}, {_format_seconds(elapsed_s)})"
    else:
        progress = f" ({_format_seconds(elapsed_s)})"
    detail = (
        f"{stage} did not finish within the processing budget{progress}. "
        "Enter values by hand or retry."
    )
    return _outcome(
        result,
        status="needs_review",
        snapshot_id=snapshot_id,
        extracted=None,
        issues=[
            {
                "code": checks.PROCESSING_TIMEOUT,
                "target": "document",
                "detail": detail,
            }
        ],
        error=None,
        model=model,
        updated_at=now(),
    )


def _format_seconds(seconds: float) -> str:
    return f"{round(seconds)} s"


def _failed_outcome(result, model, snapshot_id, code, detail, now):
    return _outcome(
        result,
        status="failed",
        snapshot_id=snapshot_id,
        extracted=None,
        issues=[],
        error={"code": code, "detail": detail},
        model=model,
        updated_at=now(),
    )


def _outcome(
    result,
    *,
    status,
    snapshot_id,
    extracted,
    issues,
    error,
    model,
    updated_at,
):
    return {
        **result,
        "status": status,
        "snapshot_id": snapshot_id,
        "extracted": extracted,
        "issues": issues,
        "error": error,
        "llm": {"model": model, "prompt_version": PROMPT_VERSION},
        "updated_at": updated_at,
    }
