"""Child-process OCR job execution."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from PIL import UnidentifiedImageError

from privasheet.ingest.checks import IngestError
from privasheet.ingest.render import render_pages, save_png_atomic
from privasheet.ocr.engine import OcrError
from privasheet.ocr.snapshot import compute_snapshot_id, normalize_boxes

DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"
OCR_FAILED = "OCR_FAILED"


def run_child(conn, path, kind, limits, out_dir, engine: str) -> None:
    """Run OCR work and send small status/result messages to the parent."""

    try:
        result = _run_job(path, kind, limits, out_dir, engine, conn)
        conn.send(("result", result))
    except IngestError as exc:
        conn.send(("error", DOCUMENT_UNREADABLE, _safe_document_detail(exc)))
    except _DocumentDecodeError as exc:
        conn.send(("error", DOCUMENT_UNREADABLE, _safe_decode_detail(exc)))
    except OcrError as exc:
        conn.send(("error", OCR_FAILED, _safe_ocr_detail(exc)))
    except Exception as exc:  # noqa: BLE001 - child boundary turns crashes into OCR failures.
        conn.send(("error", OCR_FAILED, f"OCR child failed ({type(exc).__name__})."))
    finally:
        conn.close()


def _run_job(path, kind, limits, out_dir, engine_ref: str, conn) -> dict[str, Any]:
    pages = _render_pages(path, kind, limits)
    total = len(pages)
    for page_number in range(1, total + 1):
        conn.send(("progress", "render", page_number, total))

    engine = _load_engine(engine_ref)
    normalized_pages = []
    any_boxes = False
    output_dir = Path(out_dir)

    for page_number, image in enumerate(pages, start=1):
        conn.send(("progress", "ocr", page_number, total))
        width, height = image.size
        boxes = normalize_boxes(page_number, width, height, engine.recognize(image))
        any_boxes = any_boxes or bool(boxes)
        tmp_image = output_dir / f"page-{page_number}.png"
        save_png_atomic(image, tmp_image)
        normalized_pages.append(
            {
                "page": page_number,
                "width": width,
                "height": height,
                "boxes": boxes,
                "tmp_image": str(tmp_image),
            }
        )

    if not any_boxes:
        raise OcrError(OCR_FAILED, "Document contains no OCR boxes.")

    snapshot_id = compute_snapshot_id(pages, engine)
    return {
        "snapshot_id": snapshot_id,
        "engine": _engine_info(engine),
        "pages": normalized_pages,
    }


class _DocumentDecodeError(Exception):
    """Decode failure raised only while rendering the source document."""


def _render_pages(path, kind, limits):
    try:
        return render_pages(path, kind, limits)
    except IngestError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _DocumentDecodeError(type(exc).__name__) from exc


def _load_engine(engine_ref: str):
    module_name, separator, callable_name = engine_ref.partition(":")
    if not separator or not module_name or not callable_name:
        raise OcrError(OCR_FAILED, "OCR engine must be specified as module:callable.")

    try:
        module = importlib.import_module(module_name)
        factory = module
        for attr in callable_name.split("."):
            factory = getattr(factory, attr)
        return factory()
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError(
            OCR_FAILED,
            f"Could not initialize OCR engine ({type(exc).__name__}).",
        ) from exc


def _engine_info(engine) -> dict[str, Any]:
    return {
        "name": engine.name,
        "version": engine.version,
        "models": engine.models(),
        "config_sha256": engine.config_sha256,
    }


def _safe_document_detail(exc: IngestError) -> str:
    return f"{exc.code}: {exc.detail}"


def _safe_decode_detail(exc: Exception) -> str:
    return f"Could not decode document ({type(exc).__name__})."


def _safe_ocr_detail(exc: OcrError) -> str:
    return f"OCR failed ({exc.code})."
