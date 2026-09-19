"""OCR engine adapter tests."""

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from privasheet.ocr.engine import OcrError, RapidOcrEngine, rapidocr_result_to_raw_boxes


def _fake_numpy_module():
    class FakeArray:
        pass

    def asarray(value):
        array = FakeArray()
        array.source = value
        return array

    return SimpleNamespace(asarray=asarray, ndarray=FakeArray)


def _write_models(root: Path, *, rec: bool = True, cls: bool = True) -> dict[str, Path]:
    model_dir = root / "models"
    model_dir.mkdir(parents=True)
    paths = {
        "det": model_dir / "fake_det.onnx",
        "rec": model_dir / "fake_rec.onnx",
        "cls": model_dir / "fake_cls.onnx",
    }
    paths["det"].write_bytes(b"det model")
    if rec:
        paths["rec"].write_bytes(b"rec model")
    if cls:
        paths["cls"].write_bytes(b"cls model")
    return paths


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rapidocr_module(package_dir: Path, rapidocr_class):
    return SimpleNamespace(
        __file__=str(package_dir / "__init__.py"),
        __version__="fake",
        RapidOCR=rapidocr_class,
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
    assert (
        rapidocr_result_to_raw_boxes(
            SimpleNamespace(boxes=None, txts=None, scores=None)
        )
        == []
    )


def test_rapidocr_result_to_raw_boxes_rejects_length_mismatch():
    result = SimpleNamespace(
        boxes=[[[0, 0], [1, 0], [1, 1], [0, 1]]],
        txts=[],
        scores=[0.99],
    )

    with pytest.raises(OcrError) as excinfo:
        rapidocr_result_to_raw_boxes(result)

    assert excinfo.value.code == "OCR_FAILED"
    assert "length mismatch" in excinfo.value.detail


def test_rapidocr_engine_import_failure_is_ocr_failed(monkeypatch):
    monkeypatch.setitem(sys.modules, "rapidocr", None)

    with pytest.raises(OcrError) as excinfo:
        RapidOcrEngine()

    assert excinfo.value.code == "OCR_FAILED"
    assert "rapidocr" in excinfo.value.detail


def test_rapidocr_engine_uses_local_models_and_numpy_array(monkeypatch, tmp_path):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    paths = _write_models(package_dir)
    calls = []

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            params = kwargs["params"]
            assert params["Det.model_path"] == str(paths["det"])
            assert params["Rec.model_path"] == str(paths["rec"])
            assert params["Cls.model_path"] == str(paths["cls"])

        def __call__(self, image):
            assert isinstance(image, sys.modules["numpy"].ndarray)
            return SimpleNamespace(
                boxes=[[[0, 0], [1, 0], [1, 1], [0, 1]]],
                txts=["ok"],
                scores=[0.99],
            )

    monkeypatch.setitem(
        sys.modules, "rapidocr", _rapidocr_module(package_dir, FakeRapidOCR)
    )
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    engine = RapidOcrEngine(config={"det_limit_side_len": 960})
    first_config_hash = engine.config_sha256

    assert calls[0]["params"]["det_limit_side_len"] == 960
    assert engine.recognize(Image.new("RGB", (1, 1), "white"))[0].text == "ok"
    assert calls and engine.models() == [
        {"name": "fake_cls.onnx", "sha256": _sha256(paths["cls"])},
        {"name": "fake_det.onnx", "sha256": _sha256(paths["det"])},
        {"name": "fake_rec.onnx", "sha256": _sha256(paths["rec"])},
    ]
    assert (
        RapidOcrEngine(config={"det_limit_side_len": 1280}).config_sha256
        != first_config_hash
    )


def test_rapidocr_engine_finds_data_dir_models_and_disables_missing_cls(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    data_dir = tmp_path / "data"
    paths = _write_models(data_dir, cls=False)

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            params = kwargs["params"]
            assert params["Det.model_path"] == str(paths["det"])
            assert params["Rec.model_path"] == str(paths["rec"])
            assert "Cls.model_path" not in params
            assert params["use_cls"] is False

    monkeypatch.setitem(
        sys.modules, "rapidocr", _rapidocr_module(package_dir, FakeRapidOCR)
    )
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    assert RapidOcrEngine(data_dir=data_dir).models() == [
        {"name": "fake_det.onnx", "sha256": _sha256(paths["det"])},
        {"name": "fake_rec.onnx", "sha256": _sha256(paths["rec"])},
    ]


def test_rapidocr_engine_missing_numpy_and_models_are_ocr_failed(monkeypatch, tmp_path):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    _write_models(package_dir)
    monkeypatch.setitem(sys.modules, "rapidocr", _rapidocr_module(package_dir, object))
    monkeypatch.setitem(sys.modules, "numpy", None)

    with pytest.raises(OcrError) as excinfo:
        RapidOcrEngine()

    assert excinfo.value.code == "OCR_FAILED"
    assert "numpy" in excinfo.value.detail

    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())
    package_dir = tmp_path / "rapidocr_no_rec"
    package_dir.mkdir()
    _write_models(package_dir, rec=False)
    constructed = False

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            nonlocal constructed
            constructed = True

    monkeypatch.setitem(
        sys.modules, "rapidocr", _rapidocr_module(package_dir, FakeRapidOCR)
    )

    with pytest.raises(OcrError) as excinfo:
        RapidOcrEngine()

    assert excinfo.value.code == "OCR_FAILED"
    assert "Missing OCR model" in excinfo.value.detail
    assert constructed is False


def test_rapidocr_engine_recognition_exception_is_ocr_failed(monkeypatch, tmp_path):
    package_dir = tmp_path / "rapidocr"
    package_dir.mkdir()
    _write_models(package_dir)

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            pass

        def __call__(self, image):
            raise RuntimeError("boom")

    monkeypatch.setitem(
        sys.modules, "rapidocr", _rapidocr_module(package_dir, FakeRapidOCR)
    )
    monkeypatch.setitem(sys.modules, "numpy", _fake_numpy_module())

    with pytest.raises(OcrError) as excinfo:
        RapidOcrEngine().recognize(Image.new("RGB", (1, 1), "white"))

    assert excinfo.value.code == "OCR_FAILED"
    assert "recognition failed" in excinfo.value.detail
    assert "RuntimeError" in excinfo.value.detail
