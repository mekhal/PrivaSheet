"""OCR engine adapters."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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

    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if txts is None or scores is None:
        raise OcrError("OCR_FAILED", "RapidOCR result length mismatch.")
    if len(boxes) != len(txts) or len(boxes) != len(scores):
        raise OcrError("OCR_FAILED", "RapidOCR result length mismatch.")

    raw_boxes = []
    for quad, text, score in zip(boxes, txts, scores, strict=True):
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

    def __init__(
        self,
        *,
        model_dir: str | Path | None = None,
        data_dir: str | Path | None = None,
        config: Mapping[str, Any] | None = None,
    ):
        try:
            rapidocr = importlib.import_module("rapidocr")
        except ImportError as exc:
            package = exc.name or "rapidocr"
            raise OcrError("OCR_FAILED", f"Missing OCR package {package}.") from exc
        except Exception as exc:
            raise OcrError(
                "OCR_FAILED",
                f"Could not import OCR package rapidocr ({type(exc).__name__}).",
            ) from exc

        try:
            self._numpy = importlib.import_module("numpy")
        except ImportError as exc:
            package = exc.name or "numpy"
            raise OcrError("OCR_FAILED", f"Missing OCR package {package}.") from exc
        except Exception as exc:
            raise OcrError(
                "OCR_FAILED",
                f"Could not import OCR package numpy ({type(exc).__name__}).",
            ) from exc

        self.version = str(getattr(rapidocr, "__version__", "unknown"))
        try:
            effective_config = dict(config or {})
            model_paths = _resolve_model_paths(rapidocr, model_dir, data_dir)
            engine_params = _rapidocr_constructor_params(model_paths, effective_config)
            engine_class = rapidocr.RapidOCR
            self._engine = engine_class(**engine_params)
            self._models = _model_digests(model_paths.values())
            if not self._models:
                raise OcrError("OCR_FAILED", "RapidOCR did not load any model files.")
            self.config_sha256 = _config_sha256(
                self._models,
                {
                    "params": engine_params["params"],
                },
            )
        except OcrError:
            raise
        except Exception as exc:
            raise OcrError(
                "OCR_FAILED",
                f"Could not initialize RapidOCR ({type(exc).__name__}).",
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
                "OCR_FAILED",
                f"RapidOCR recognition failed ({type(exc).__name__}).",
            ) from exc


def make_rapidocr_engine(
    *,
    model_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
) -> RapidOcrEngine:
    """Default OCR engine factory."""

    return RapidOcrEngine(model_dir=model_dir, data_dir=data_dir, config=config)


def _model_digests(model_paths) -> list[dict[str, str]]:
    model_paths = sorted({Path(path).resolve() for path in model_paths})
    return [{"name": path.name, "sha256": _file_sha256(path)} for path in model_paths]


def _resolve_model_paths(
    rapidocr_module,
    model_dir: str | Path | None,
    data_dir: str | Path | None,
) -> dict[str, Path]:
    roots = _allowed_model_roots(rapidocr_module, model_dir, data_dir)
    by_role: dict[str, Path] = {}
    for root in roots:
        root_candidates = _model_candidates_by_role(root)
        for role, paths in root_candidates.items():
            if role in by_role:
                continue
            if len(paths) > 1:
                raise OcrError(
                    "OCR_FAILED",
                    f"Ambiguous OCR model {role} under {root}.",
                )
            by_role[role] = paths[0].resolve()

    missing = [role for role in ("det", "rec") if role not in by_role]
    if missing:
        searched = ", ".join(str(root) for root in roots) or "no model roots"
        raise OcrError(
            "OCR_FAILED",
            f"Missing OCR model {', '.join(missing)} under {searched}.",
        )
    return by_role


def _allowed_model_roots(
    rapidocr_module,
    model_dir: str | Path | None,
    data_dir: str | Path | None,
) -> tuple[Path, ...]:
    roots = []

    if model_dir is not None:
        roots.append(Path(model_dir).resolve())

    for directory in (data_dir, _default_data_dir()):
        if directory is not None:
            roots.append(Path(directory).resolve() / "models")

    module_file = getattr(rapidocr_module, "__file__", None)
    if module_file:
        roots.append(Path(module_file).resolve().parent)

    return tuple(dict.fromkeys(root for root in roots if root.exists()))


def _default_data_dir() -> Path | None:
    raw = os.environ.get("PRIVASHEET_DATA_DIR")
    if not raw:
        return None
    return Path(raw)


def _is_model_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {
        ".onnx",
        ".bin",
        ".param",
        ".pdiparams",
        ".pth",
    }


def _model_role(path: Path) -> str | None:
    tokens = set(re.split(r"[^a-z0-9]+", path.stem.lower().replace("_", "-")))
    for role in ("det", "rec", "cls"):
        if role in tokens:
            return role
    return None


def _model_candidates_by_role(root: Path) -> dict[str, list[Path]]:
    candidates: dict[str, list[Path]] = {}
    for path in sorted(item for item in root.rglob("*") if _is_model_file(item)):
        role = _model_role(path)
        if role:
            candidates.setdefault(role, []).append(path)
    return candidates


def _rapidocr_constructor_params(
    model_paths: dict[str, Path], config: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    params: dict[str, Any] = dict(config)
    params["Det.model_path"] = str(model_paths["det"])
    params["Rec.model_path"] = str(model_paths["rec"])
    if "cls" in model_paths:
        params["Cls.model_path"] = str(model_paths["cls"])
    else:
        params["use_cls"] = False
    return {"params": params}


def _serialize_config(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_config(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_serialize_config(item) for item in value]
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_sha256(models: list[dict[str, str]], config: Mapping[str, Any]) -> str:
    payload = {
        "config": _serialize_config(dict(config)),
        "models": sorted(models, key=lambda model: model["name"]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
