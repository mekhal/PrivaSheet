"""OCR engine adapters."""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

Point = tuple[float, float]
Quad = tuple[Point, Point, Point, Point]


class OcrError(Exception):
    """OCR unit failure with a stable machine-readable code."""

    def __init__(self, code: str = "OCR_FAILED", detail: str = ""):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RawBox:
    """Raw OCR box in source image pixels."""

    quad_px: Quad
    text: str
    score: float


class OcrEngine(Protocol):
    """OCR engine interface used by the snapshot builder."""

    name: str
    version: str
    config_sha256: str

    def models(self) -> list[dict[str, str]]:
        """Return model names and SHA-256 digests."""

    def recognize(self, image: Image.Image) -> list[RawBox]:
        """Recognize text in an in-memory page image."""


def rapidocr_result_to_raw_boxes(result) -> list[RawBox]:
    """Convert RapidOCR's documented boxes/txts/scores result shape."""

    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []

    txts = getattr(result, "txts", [])
    scores = getattr(result, "scores", [])
    raw_boxes = []
    for quad, text, score in zip(boxes, txts, scores, strict=False):
        raw_boxes.append(
            RawBox(
                quad_px=tuple((float(x), float(y)) for x, y in quad),
                text=str(text),
                score=float(score),
            )
        )
    return raw_boxes


class RapidOcrEngine:
    """RapidOCR adapter that only accepts in-memory images."""

    name = "rapidocr"

    def __init__(self):
        try:
            rapidocr = importlib.import_module("rapidocr")
            self._numpy = importlib.import_module("numpy")
        except Exception as exc:
            package = "rapidocr" if "rapidocr" in str(exc).lower() else "numpy"
            raise OcrError(
                "OCR_FAILED", f"Missing OCR package {package}: {exc}."
            ) from exc

        self.version = str(getattr(rapidocr, "__version__", "unknown"))
        try:
            engine_class = rapidocr.RapidOCR
            self._engine = engine_class()
            self._models = _loaded_model_digests(self._engine, rapidocr)
            if not self._models:
                raise OcrError(
                    "OCR_FAILED", "RapidOCR did not report any loaded model files."
                )
            self.config_sha256 = _config_sha256(self._models)
        except OcrError:
            raise
        except Exception as exc:
            raise OcrError(
                "OCR_FAILED", f"Could not initialize RapidOCR: {exc}."
            ) from exc

    def models(self) -> list[dict[str, str]]:
        return list(self._models)

    def recognize(self, image: Image.Image) -> list[RawBox]:
        try:
            array = self._numpy.asarray(image.convert("RGB"))
            result = self._engine(array)
            return rapidocr_result_to_raw_boxes(result)
        except OcrError:
            raise
        except Exception as exc:
            raise OcrError(
                "OCR_FAILED", f"RapidOCR recognition failed: {exc}."
            ) from exc


def make_rapidocr_engine() -> RapidOcrEngine:
    """Default OCR engine factory."""

    return RapidOcrEngine()


def _loaded_model_digests(engine, rapidocr_module) -> list[dict[str, str]]:
    model_paths = sorted(_iter_model_paths(engine, rapidocr_module))
    return [{"name": path.name, "sha256": _file_sha256(path)} for path in model_paths]


def _iter_model_paths(engine, rapidocr_module) -> set[Path]:
    package_roots = _allowed_model_roots(rapidocr_module)
    paths = set()
    _collect_paths(engine, paths, set())
    return {
        path
        for path in paths
        if path.is_file()
        and path.suffix.lower() in {".onnx", ".bin", ".param", ".pdiparams", ".pth"}
        and _is_relative_to_any(path.resolve(), package_roots)
    }


def _allowed_model_roots(rapidocr_module) -> tuple[Path, ...]:
    roots = []
    module_file = getattr(rapidocr_module, "__file__", None)
    if module_file:
        roots.append(Path(module_file).resolve().parent)
    return tuple(dict.fromkeys(roots))


def _collect_paths(value, paths: set[Path], seen: set[int]) -> None:
    value_id = id(value)
    if value_id in seen:
        return
    seen.add(value_id)

    if isinstance(value, str | Path):
        paths.add(Path(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_paths(item, paths, seen)
        return
    if isinstance(value, list | tuple | set):
        for item in value:
            _collect_paths(item, paths, seen)
        return
    if hasattr(value, "__dict__"):
        for item in vars(value).values():
            _collect_paths(item, paths, seen)


def _is_relative_to_any(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_sha256(models: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for model in models:
        digest.update(model["name"].encode())
        digest.update(b"\0")
        digest.update(model["sha256"].encode())
        digest.update(b"\0")
    return digest.hexdigest()
