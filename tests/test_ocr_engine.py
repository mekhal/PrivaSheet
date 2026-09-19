"""OCR engine adapter tests."""

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from privasheet.ocr.engine import (
    OcrError,
    RapidOcrEngine,
    rapidocr_result_to_raw_boxes,
)


def _fake_numpy_module():
    class FakeArray:
        pass

    def asarray(value):
        array = FakeArray()
        array.source = value
        return array

    return SimpleNamespace(asarray=asarray, ndarray=FakeArray)


def _write_fake_models(root: Path, *, include_rec: bool = True) -> dict[str, Path]:
    model_dir = root / "models"
    model_dir.mkdir(parents=True)
    paths = {
        "det": model_dir / "fake_det.onnx",
        "rec": model_dir / "fake_rec.onnx",
        "cls": model_dir / "fake_cls.onnx",
    }
    paths["det"].write_bytes(b"det model")
    if include_rec:
        paths["rec"].write_bytes(b"rec model")
    paths["cls"].write_bytes(b"cls model")
    return paths


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def test_rapidocr_engine_missing_numpy_reports_numpy(monkeypatch, tmp_path):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    _write_fake_models(package_dir)
    rapidocr_module = SimpleNamespace(
        __file__=str(package_dir / "__init__.py"),
        __version__="fake",
        RapidOCR=lambda **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr_module)
    monkeypatch.setitem(sys.modules, "numpy", None)

    with pytest.raises(OcrError) as excinfo:
        RapidOcrEngine()

    assert excinfo.value.code == "OCR_FAILED"
    assert "numpy" in excinfo.value.detail


def test_rapidocr_engine_missing_model_fails_before_engine_construction(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    _write_fake_models(package_dir, include_rec=False)
    constructed = False

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            nonlocal constructed
            constructed = True

    rapidocr_module = SimpleNamespace(
        __file__=str(package_dir / "__init__.py"),
        __version__="fake",
        RapidOCR=FakeRapidOCR,
    )
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr_module)
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    with pytest.raises(OcrError) as excinfo:
        RapidOcrEngine()

    assert excinfo.value.code == "OCR_FAILED"
    assert "Missing OCR model" in excinfo.value.detail
    assert constructed is False


def test_rapidocr_engine_uses_explicit_local_models_and_numpy_array(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    paths = _write_fake_models(package_dir)
    calls = []

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
            assert kwargs["det_model_path"] == str(paths["det"])
            assert kwargs["rec_model_path"] == str(paths["rec"])
            assert kwargs["cls_model_path"] == str(paths["cls"])
            assert kwargs["download"] is False

        def __call__(self, image):
            calls.append(("recognize", image))
            assert isinstance(image, sys.modules["numpy"].ndarray)
            return SimpleNamespace(
                boxes=[[[0, 0], [1, 0], [1, 1], [0, 1]]],
                txts=["ok"],
                scores=[0.99],
            )

    rapidocr_module = SimpleNamespace(
        __file__=str(package_dir / "__init__.py"),
        __version__="fake",
        RapidOCR=FakeRapidOCR,
    )
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr_module)
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    engine = RapidOcrEngine(config={"det_limit_side_len": 960})
    boxes = engine.recognize(Image.new("RGB", (1, 1), "white"))

    assert boxes[0].text == "ok"
    assert engine.models() == [
        {"name": "fake_cls.onnx", "sha256": _sha256(paths["cls"])},
        {"name": "fake_det.onnx", "sha256": _sha256(paths["det"])},
        {"name": "fake_rec.onnx", "sha256": _sha256(paths["rec"])},
    ]
    assert calls[0][0] == "init"
    assert calls[1][0] == "recognize"


def test_rapidocr_engine_accepts_models_from_data_dir(monkeypatch, tmp_path):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    data_dir = tmp_path / "data"
    paths = _write_fake_models(data_dir)

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            assert kwargs["det_model_path"] == str(paths["det"])
            assert kwargs["rec_model_path"] == str(paths["rec"])
            assert kwargs["cls_model_path"] == str(paths["cls"])

        def __call__(self, image):
            return SimpleNamespace(boxes=None, txts=None, scores=None)

    rapidocr_module = SimpleNamespace(
        __file__=str(package_dir / "__init__.py"),
        __version__="fake",
        RapidOCR=FakeRapidOCR,
    )
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr_module)
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    engine = RapidOcrEngine(data_dir=data_dir)

    assert engine.models() == [
        {"name": "fake_cls.onnx", "sha256": _sha256(paths["cls"])},
        {"name": "fake_det.onnx", "sha256": _sha256(paths["det"])},
        {"name": "fake_rec.onnx", "sha256": _sha256(paths["rec"])},
    ]


def test_rapidocr_engine_recognition_exception_is_ocr_failed(monkeypatch, tmp_path):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    _write_fake_models(package_dir)

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            pass

        def __call__(self, image):
            raise RuntimeError("boom")

    rapidocr_module = SimpleNamespace(
        __file__=str(package_dir / "__init__.py"),
        __version__="fake",
        RapidOCR=FakeRapidOCR,
    )
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr_module)
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    engine = RapidOcrEngine()

    with pytest.raises(OcrError) as excinfo:
        engine.recognize(Image.new("RGB", (1, 1), "white"))

    assert excinfo.value.code == "OCR_FAILED"
    assert "recognition failed" in excinfo.value.detail


def test_rapidocr_engine_config_hash_changes_with_effective_config(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    _write_fake_models(package_dir)

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            pass

    rapidocr_module = SimpleNamespace(
        __file__=str(package_dir / "__init__.py"),
        __version__="fake",
        RapidOCR=FakeRapidOCR,
    )
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr_module)
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    first = RapidOcrEngine(config={"det_limit_side_len": 960})
    second = RapidOcrEngine(config={"det_limit_side_len": 1280})

    assert first.models() == second.models()
    assert first.config_sha256 != second.config_sha256
