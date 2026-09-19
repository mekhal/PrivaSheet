"""OCR snapshot normalization and deterministic identity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from PIL import Image

from privasheet.ingest.render import canonical_page_bytes
from privasheet.ocr.engine import OcrEngine, OcrError, RawBox


def normalize_boxes(
    page: int, width: int, height: int, raw_boxes: Iterable[RawBox]
) -> list[dict]:
    """Normalize raw pixel boxes into the snapshot box shape."""

    if width <= 0 or height <= 0:
        raise OcrError("OCR_FAILED", "OCR page dimensions must be positive.")

    boxes = []
    for raw_box in raw_boxes:
        text = raw_box.text.strip()
        if not text:
            continue
        boxes.append(
            {
                "id": f"p{page}-b{len(boxes):04d}",
                "text": text,
                "quad": [
                    [_clamp(x / width), _clamp(y / height)] for x, y in raw_box.quad_px
                ],
                "score": float(raw_box.score),
            }
        )
    return boxes


def compute_snapshot_id(page_images: Iterable[Image.Image], engine: OcrEngine) -> str:
    """Return a stable snapshot id over page pixels and OCR configuration."""

    digest = hashlib.sha256()
    for image in page_images:
        page_bytes = canonical_page_bytes(image)
        _hash_part(digest, b"page", page_bytes)

    metadata = {
        "engine": {
            "name": engine.name,
            "version": engine.version,
            "models": sorted(engine.models(), key=lambda model: model["name"]),
            "config_sha256": engine.config_sha256,
        }
    }
    _hash_part(
        digest,
        b"metadata",
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(),
    )
    return f"sha256:{digest.hexdigest()}"


def build_snapshot(
    snapshot_id: str,
    engine: OcrEngine,
    pages: list[dict],
    created_at: str,
) -> dict:
    """Build the immutable OCR snapshot document."""

    if not any(page.get("boxes") for page in pages):
        raise OcrError("OCR_FAILED", "Document contains no OCR boxes.")

    digest = snapshot_id.removeprefix("sha256:")
    return {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "engine": {
            "name": engine.name,
            "version": engine.version,
            "models": engine.models(),
            "config_sha256": engine.config_sha256,
        },
        "pages": [
            {
                "page": page["page"],
                "width": page["width"],
                "height": page["height"],
                "boxes": page["boxes"],
                "image": f"pages/{digest}/page-{page['page']}.png",
            }
            for page in pages
        ],
    }


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _hash_part(digest, label: bytes, payload: bytes) -> None:
    digest.update(len(label).to_bytes(4, "big"))
    digest.update(label)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
