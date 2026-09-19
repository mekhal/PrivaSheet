"""OCR engine adapter tests."""

import sys
from types import SimpleNamespace

import pytest

from privasheet.ocr.engine import (
    OcrError,
    RapidOcrEngine,
    rapidocr_result_to_raw_boxes,
)


def test_rapidocr_result_to_raw_boxes_converts_documented_shape():
    result = SimpleNamespace(
        boxes=[
            [[1, 2], [3, 4], [5, 6], [7, 8]],
            [[10.1, 11.2], [12.3, 13.4], [14.5, 15.6], [16.7, 17.8]],
        ],
        txts=["Invoice", "Total"],
        scores=[0.95, 0.5],
    )

    boxes = rapidocr_result_to_raw_boxes(result)

    assert [box.text for box in boxes] == ["Invoice", "Total"]
    assert boxes[0].quad_px == ((1, 2), (3, 4), (5, 6), (7, 8))
    assert boxes[1].quad_px == (
        (10.1, 11.2),
        (12.3, 13.4),
        (14.5, 15.6),
        (16.7, 17.8),
    )
    assert [box.score for box in boxes] == [0.95, 0.5]


def test_rapidocr_result_to_raw_boxes_handles_no_text_result():
    result = SimpleNamespace(boxes=None, txts=None, scores=None)

    assert rapidocr_result_to_raw_boxes(result) == []


def test_rapidocr_engine_import_failure_is_ocr_failed(monkeypatch):
    monkeypatch.setitem(sys.modules, "rapidocr", None)

    with pytest.raises(OcrError) as excinfo:
        RapidOcrEngine()

    assert excinfo.value.code == "OCR_FAILED"
    assert "rapidocr" in excinfo.value.detail
