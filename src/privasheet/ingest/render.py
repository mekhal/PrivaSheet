"""Render upload pages to canonical RGB images."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from privasheet.ingest.checks import IngestError, Kind, Limits, inspect


def render_pages(
    path: str | Path, kind: Kind | str, limits: Limits
) -> list[Image.Image]:
    """Render or decode an upload into RGB page images."""

    page_infos = inspect(path, kind, limits)
    _check_page_pixels(page_infos, limits)

    if kind == "pdf":
        pages = _render_pdf(path, limits)
    elif kind in {"jpeg", "png"}:
        pages = [_render_image(path)]
    elif kind == "tiff":
        pages = _render_tiff(path)
    else:
        raise IngestError("UNSUPPORTED_TYPE", f"Unsupported upload type: {kind}.")

    _check_images(pages, limits)
    return pages


def canonical_page_bytes(image: Image.Image) -> bytes:
    """Return deterministic bytes for snapshot hashing."""

    rgb = image.convert("RGB")
    width, height = rgb.size
    return width.to_bytes(4, "big") + height.to_bytes(4, "big") + rgb.tobytes()


def save_png_atomic(image: Image.Image, path: str | Path) -> None:
    """Atomically write an image as PNG."""

    final_path = Path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w+b",
            dir=final_path.parent,
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            image.convert("RGB").save(tmp, format="PNG")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, final_path)
        _fsync_dir(final_path.parent)
    except Exception:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)
        raise


def _render_image(path: str | Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            return ImageOps.exif_transpose(image).convert("RGB").copy()
    except IngestError:
        raise
    except Image.DecompressionBombError as exc:
        raise IngestError(
            "PIXEL_LIMIT_EXCEEDED",
            f"Image declares too many pixels: {exc}.",
        )
    except (OSError, ValueError) as exc:
        raise IngestError("RENDER_FAILED", f"Could not decode image: {exc}.")


def _render_tiff(path: str | Path) -> list[Image.Image]:
    try:
        with Image.open(path) as image:
            pages = []
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                pages.append(ImageOps.exif_transpose(image).convert("RGB").copy())
            return pages
    except IngestError:
        raise
    except Image.DecompressionBombError as exc:
        raise IngestError(
            "PIXEL_LIMIT_EXCEEDED",
            f"TIFF declares too many pixels: {exc}.",
        )
    except (EOFError, OSError, ValueError) as exc:
        raise IngestError("RENDER_FAILED", f"Could not decode TIFF: {exc}.")


def _render_pdf(path: str | Path, limits: Limits) -> list[Image.Image]:
    import pypdfium2 as pdfium

    pdfium_error = getattr(pdfium, "PdfiumError", RuntimeError)
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            pages = []
            for index in range(len(document)):
                page = document[index]
                try:
                    bitmap = page.render(scale=limits.pdf_dpi / 72)
                    try:
                        pages.append(bitmap.to_pil().convert("RGB").copy())
                    finally:
                        close = getattr(bitmap, "close", None)
                        if close is not None:
                            close()
                finally:
                    page.close()
            return pages
        finally:
            document.close()
    except IngestError:
        raise
    except (OSError, ValueError, pdfium_error) as exc:
        raise IngestError("RENDER_FAILED", f"Could not render PDF: {exc}.")


def _check_page_pixels(page_infos, limits: Limits) -> None:
    max_pixels = limits.max_megapixels * 1_000_000
    for page in page_infos:
        if page.pixels > max_pixels:
            raise IngestError(
                "PIXEL_LIMIT_EXCEEDED",
                f"Page {page.page} has {page.pixels} pixels; "
                f"limit is {limits.max_megapixels} megapixels.",
            )


def _check_images(images: list[Image.Image], limits: Limits) -> None:
    max_pixels = limits.max_megapixels * 1_000_000
    for index, image in enumerate(images, start=1):
        pixels = image.size[0] * image.size[1]
        if pixels > max_pixels:
            raise IngestError(
                "PIXEL_LIMIT_EXCEEDED",
                f"Page {index} has {pixels} pixels; "
                f"limit is {limits.max_megapixels} megapixels.",
            )


def _fsync_dir(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
