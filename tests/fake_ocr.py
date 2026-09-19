"""Fake OCR engines for child-process runner tests and the end-to-end test.

The real RapidOCR engine is not available here. ``ScriptedOcrEngine`` stands in for it:
``draw_page`` draws text lines on a Pillow image and records the same lines (with their
pixel boxes) in a JSON script keyed by the page pixels, and the engine, running in the
OCR child process, looks the page up by its pixels. The fake output therefore always
comes from the text that was drawn.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from privasheet.ingest.render import canonical_page_bytes
from privasheet.ocr.engine import OcrError, RawBox

SCRIPT_ENV = "PRIVASHEET_FAKE_OCR_SCRIPT"
PAGE_SIZE = (900, 700)
FONT_SIZE = 24
SCORE = 0.98


class FakeOcrEngine:
    name = "fake-ocr"
    version = "1.0"
    config_sha256 = "fake-config"

    def models(self) -> list[dict[str, str]]:
        return [
            {"name": "det", "sha256": "fake-det"},
            {"name": "rec", "sha256": "fake-rec"},
        ]

    def recognize(self, image) -> list[RawBox]:
        width, height = image.size
        if (width, height) == (1, 1):
            return []
        return [
            RawBox(
                quad_px=(
                    (0, 0),
                    (width / 2, 0),
                    (width / 2, height / 2),
                    (0, height / 2),
                ),
                text=f"{width}x{height}",
                score=0.75,
            )
        ]


class SleepingOcrEngine(FakeOcrEngine):
    def recognize(self, image) -> list[RawBox]:
        time.sleep(30)
        return super().recognize(image)


class RaisingOcrEngine(FakeOcrEngine):
    def recognize(self, image) -> list[RawBox]:
        raise OcrError("OCR_FAILED", "synthetic failure")


class ValueErrorOcrEngine(FakeOcrEngine):
    def recognize(self, image) -> list[RawBox]:
        raise ValueError("synthetic value error")


class CrashingOcrEngine(FakeOcrEngine):
    def recognize(self, image) -> list[RawBox]:
        os._exit(1)


def make_engine() -> FakeOcrEngine:
    return FakeOcrEngine()


def make_sleeping_engine() -> SleepingOcrEngine:
    return SleepingOcrEngine()


def make_raising_engine() -> RaisingOcrEngine:
    return RaisingOcrEngine()


def make_value_error_engine() -> ValueErrorOcrEngine:
    return ValueErrorOcrEngine()


def make_crashing_engine() -> CrashingOcrEngine:
    return CrashingOcrEngine()


def page_key(image: Image.Image) -> str:
    """Identify a page by its pixels, exactly as the OCR child sees it."""
    return hashlib.sha256(canonical_page_bytes(image)).hexdigest()


def draw_page(
    lines: list[tuple[str, int, int]],
    script_path: str | os.PathLike[str],
    *,
    sleep_s: float = 0,
) -> Image.Image:
    """Draw ``(text, x, y)`` lines and record them in the OCR script.

    ``sleep_s`` makes the scripted engine stall on this page, to exercise timeouts.
    """
    image = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=FONT_SIZE)
    boxes = []
    for text, x, y in lines:
        draw.text((x, y), text, fill="black", font=font)
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        boxes.append({"text": text, "quad": [left, top, right, bottom]})

    script_path = Path(script_path)
    script = json.loads(script_path.read_text()) if script_path.exists() else {}
    script[page_key(image)] = {"boxes": boxes, "sleep_s": sleep_s}
    # Replace atomically: the OCR child may be reading the script right now.
    tmp = script_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(script))
    os.replace(tmp, script_path)
    return image


class ScriptedOcrEngine(FakeOcrEngine):
    """Returns the boxes ``draw_page`` recorded for the page it is shown."""

    def recognize(self, image) -> list[RawBox]:
        script = json.loads(Path(os.environ[SCRIPT_ENV]).read_text())
        page = script.get(page_key(image))
        if page is None:
            raise OcrError("OCR_FAILED", "page was not drawn by the test")
        if page["sleep_s"]:
            time.sleep(page["sleep_s"])
        return [
            RawBox(
                quad_px=((left, top), (right, top), (right, bottom), (left, bottom)),
                text=box["text"],
                score=SCORE,
            )
            for box in page["boxes"]
            for left, top, right, bottom in [box["quad"]]
        ]


def make_scripted_engine() -> ScriptedOcrEngine:
    return ScriptedOcrEngine()
