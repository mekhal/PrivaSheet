"""Fake OCR engines for child-process runner tests."""

from __future__ import annotations

import time

from privasheet.ocr.engine import OcrError, RawBox


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


def make_engine() -> FakeOcrEngine:
    return FakeOcrEngine()


def make_sleeping_engine() -> SleepingOcrEngine:
    return SleepingOcrEngine()


def make_raising_engine() -> RaisingOcrEngine:
    return RaisingOcrEngine()
