"""Upload size, type, and lightweight page inspection checks."""

from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import BinaryIO, Literal

Kind = Literal["pdf", "jpeg", "png", "tiff"]


@dataclass(frozen=True)
class Limits:
    """Upload limits from spec section 7."""

    max_bytes: int = 25 * 1024 * 1024
    max_megapixels: float = 40
    pdf_dpi: int = 200


@dataclass(frozen=True)
class PageInfo:
    """Lightweight page geometry derived without image decode or PDF render."""

    page: int
    width_px: int
    height_px: int
    pixels: int


class IngestError(Exception):
    """Validation failure with a stable code for callers and tests."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def detect_type(first_bytes: bytes) -> Kind | None:
    """Detect an upload type from its file signature, never from its name."""

    if first_bytes.startswith(b"%PDF-"):
        return "pdf"
    if first_bytes.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if first_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if first_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    return None


def copy_limited(src_stream: BinaryIO, dst_path: str | Path, max_bytes: int) -> None:
    """Copy a stream to disk, aborting and removing partial output on overflow."""

    path = Path(dst_path)
    total = 0
    try:
        with path.open("wb") as dst:
            while True:
                chunk = src_stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise IngestError(
                        "FILE_TOO_LARGE",
                        f"Upload exceeds the {max_bytes} byte limit.",
                    )
                dst.write(chunk)
    except (IngestError, OSError):
        path.unlink(missing_ok=True)
        raise


def inspect(path: str | Path, kind: Kind | str, limits: Limits) -> list[PageInfo]:
    """Return lightweight page information and enforce upload pixel limits."""

    if kind == "pdf":
        pages = _inspect_pdf(path, limits)
    elif kind in {"jpeg", "png"}:
        pages = _inspect_image(path)
    elif kind == "tiff":
        pages = _inspect_tiff(path)
    else:
        raise IngestError("UNSUPPORTED_TYPE", f"Unsupported upload type: {kind}.")

    _check_pages(pages, limits)
    return pages


def _inspect_image(path: str | Path) -> list[PageInfo]:
    from PIL import Image

    try:
        with Image.open(path) as image:
            return [_page_info(1, image.size[0], image.size[1])]
    except IngestError:
        raise
    except Image.DecompressionBombError as exc:
        raise IngestError(
            "PIXEL_LIMIT_EXCEEDED",
            f"Image header declares too many pixels: {exc}.",
        )
    except (OSError, ValueError) as exc:
        raise IngestError("INSPECT_FAILED", f"Could not inspect image header: {exc}.")


def _inspect_tiff(path: str | Path) -> list[PageInfo]:
    from PIL import Image

    try:
        with Image.open(path) as image:
            frame_count = getattr(image, "n_frames", 1)
            pages = []
            for index in range(frame_count):
                image.seek(index)
                pages.append(_page_info(index + 1, image.size[0], image.size[1]))
            return pages
    except IngestError:
        raise
    except Image.DecompressionBombError as exc:
        raise IngestError(
            "PIXEL_LIMIT_EXCEEDED",
            f"TIFF header declares too many pixels: {exc}.",
        )
    except (EOFError, OSError, ValueError) as exc:
        raise IngestError("INSPECT_FAILED", f"Could not inspect TIFF header: {exc}.")


def _inspect_pdf(path: str | Path, limits: Limits) -> list[PageInfo]:
    import pypdfium2 as pdfium

    pdfium_error = getattr(pdfium, "PdfiumError", RuntimeError)
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            page_count = len(document)
            pages = []
            for index in range(page_count):
                page = document[index]
                try:
                    width_pt, height_pt = page.get_size()
                finally:
                    page.close()
                width_px = ceil(width_pt * limits.pdf_dpi / 72)
                height_px = ceil(height_pt * limits.pdf_dpi / 72)
                pages.append(_page_info(index + 1, width_px, height_px))
            return pages
        finally:
            document.close()
    except IngestError:
        raise
    except (OSError, ValueError, pdfium_error) as exc:
        raise IngestError("INSPECT_FAILED", f"Could not inspect PDF header: {exc}.")


def _page_info(page: int, width_px: int, height_px: int) -> PageInfo:
    return PageInfo(
        page=page,
        width_px=width_px,
        height_px=height_px,
        pixels=width_px * height_px,
    )


def _check_pages(pages: list[PageInfo], limits: Limits) -> None:
    max_pixels = limits.max_megapixels * 1_000_000
    for page in pages:
        if page.pixels > max_pixels:
            raise IngestError(
                "PIXEL_LIMIT_EXCEEDED",
                f"Page {page.page} has {page.pixels} pixels; "
                f"limit is {limits.max_megapixels} megapixels.",
            )
