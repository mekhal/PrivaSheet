"""Parent-side OCR child process runner."""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from privasheet.ocr.child import OCR_FAILED, run_child
from privasheet.ocr.engine import OcrError


@dataclass(frozen=True)
class OcrJobResult:
    """OCR child result with temporary page image paths."""

    snapshot_id: str
    engine: dict
    pages: list[dict]


class OcrTimeout(Exception):
    """OCR job exceeded its whole-job time budget."""

    def __init__(
        self,
        stage: str | None,
        page: int | None,
        total: int | None,
        elapsed_s: float,
    ):
        super().__init__("OCR job timed out.")
        self.stage = stage
        self.page = page
        self.total = total
        self.elapsed_s = elapsed_s


class OcrCancelled(Exception):
    """OCR job was cancelled by the caller."""


ProgressCallback = Callable[[str, int, int], None]


def run_ocr_job(
    path: str | Path,
    kind,
    limits,
    out_dir: str | Path,
    timeout_s: float,
    engine: str = "privasheet.ocr.engine:make_rapidocr_engine",
    on_progress: ProgressCallback | None = None,
    cancel=None,
) -> OcrJobResult:
    """Run rendering and OCR inside a spawned child process."""

    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=run_child,
        args=(child_conn, str(path), kind, limits, str(out_dir), engine),
        name="privasheet-ocr-child",
    )
    started_at = time.monotonic()
    last_progress: tuple[str | None, int | None, int | None] = (None, None, None)
    started = False

    try:
        process.start()
        started = True
        child_conn.close()
        while True:
            elapsed_s = time.monotonic() - started_at
            if cancel is not None and cancel.is_set():
                _stop_child(process)
                raise OcrCancelled()

            if elapsed_s >= timeout_s:
                _stop_child(process)
                stage, page, total = last_progress
                raise OcrTimeout(stage, page, total, elapsed_s)

            remaining_s = max(0.0, timeout_s - elapsed_s)
            if parent_conn.poll(min(0.05, remaining_s)):
                try:
                    message = parent_conn.recv()
                except EOFError as exc:
                    _join_child(process)
                    raise OcrError(
                        OCR_FAILED, "OCR child exited without a result."
                    ) from exc
                tag = message[0]
                if tag == "progress":
                    _, stage, page, total = message
                    last_progress = (stage, page, total)
                    if on_progress is not None:
                        on_progress(stage, page, total)
                elif tag == "result":
                    _, payload = message
                    _join_child(process)
                    return OcrJobResult(
                        snapshot_id=payload["snapshot_id"],
                        engine=payload["engine"],
                        pages=payload["pages"],
                    )
                elif tag == "error":
                    _, code, detail = message
                    _join_child(process)
                    raise OcrError(code, detail)
                else:
                    _stop_child(process)
                    raise OcrError(OCR_FAILED, "OCR child sent an unknown message.")

            if process.exitcode is not None:
                if parent_conn.poll():
                    continue
                _join_child(process)
                raise OcrError(OCR_FAILED, "OCR child exited without a result.")
    except (OcrCancelled, OcrTimeout, OcrError):
        raise
    except Exception:
        _stop_child(process)
        raise
    finally:
        parent_conn.close()
        child_conn.close()
        if started:
            if process.is_alive():
                _stop_child(process)
            else:
                process.join(timeout=0)


def _join_child(process) -> None:
    process.join(timeout=1)
    if process.is_alive():
        _stop_child(process)


def _stop_child(process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(timeout=0.2)
    if process.is_alive():
        process.kill()
        process.join(timeout=1)
    else:
        process.join(timeout=0)
