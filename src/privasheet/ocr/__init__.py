"""OCR engine and snapshot helpers."""

from privasheet.ocr.engine import (
    OcrEngine,
    OcrError,
    RapidOcrEngine,
    RawBox,
    make_rapidocr_engine,
)
from privasheet.ocr.snapshot import (
    build_snapshot,
    compute_snapshot_id,
    normalize_boxes,
)

__all__ = [
    "OcrEngine",
    "OcrError",
    "RapidOcrEngine",
    "RawBox",
    "build_snapshot",
    "compute_snapshot_id",
    "make_rapidocr_engine",
    "normalize_boxes",
]
