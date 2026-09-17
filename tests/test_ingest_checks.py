"""Upload validation and lightweight document inspection."""

from io import BytesIO

import pytest
from PIL import Image

from privasheet.ingest.checks import (
    IngestError,
    Limits,
    PageInfo,
    check_batch_size,
    copy_limited,
    detect_type,
    inspect,
)


def save_image(path, fmt, size=(10, 12)):
    image = Image.new("RGB", size, "white")
    image.save(path, format=fmt)
    return path


def save_tiff(path, sizes):
    frames = [Image.new("RGB", size, "white") for size in sizes]
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])
    return path


def save_pdf(path, sizes):
    frames = [Image.new("RGB", size, "white") for size in sizes]
    frames[0].save(path, format="PDF", save_all=True, append_images=frames[1:])
    return path


def assert_error_code(code, fn, *args):
    with pytest.raises(IngestError) as excinfo:
        fn(*args)
    assert excinfo.value.code == code
    assert str(excinfo.value)


def test_limits_defaults_match_upload_table():
    limits = Limits()
    assert limits.max_bytes == 25 * 1024 * 1024
    assert limits.max_files == 50
    assert limits.max_pages == 10
    assert limits.max_megapixels == 40
    assert limits.pdf_dpi == 200


@pytest.mark.parametrize(
    "header,expected",
    [
        (b"%PDF-1.7\n", "pdf"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"),
        (b"\x89PNG\r\n\x1a\nrest", "png"),
        (b"II*\x00more", "tiff"),
        (b"MM\x00*more", "tiff"),
        (b"GIF89a", None),
        (b"", None),
    ],
)
def test_detect_type_uses_signature_not_extension(header, expected):
    assert detect_type(header) == expected


def test_check_batch_size_uses_file_limit():
    check_batch_size(50, Limits())
    assert_error_code("BATCH_TOO_LARGE", check_batch_size, 51, Limits())


def test_copy_limited_streams_to_destination(tmp_path):
    dst = tmp_path / "upload.bin"
    copy_limited(BytesIO(b"abcdef"), dst, 6)
    assert dst.read_bytes() == b"abcdef"


def test_copy_limited_removes_partial_file_when_too_large(tmp_path):
    dst = tmp_path / "upload.bin"
    assert_error_code("FILE_TOO_LARGE", copy_limited, BytesIO(b"abcdef"), dst, 5)
    assert not dst.exists()


@pytest.mark.parametrize(
    "fmt,kind",
    [
        ("PNG", "png"),
        ("JPEG", "jpeg"),
    ],
)
def test_inspect_single_image_header(tmp_path, fmt, kind):
    path = save_image(tmp_path / f"doc.{kind}", fmt, (31, 37))
    assert inspect(path, kind, Limits()) == [
        PageInfo(page=1, width_px=31, height_px=37, pixels=31 * 37)
    ]


def test_inspect_tiff_counts_frames_without_rendering(tmp_path):
    path = save_tiff(tmp_path / "doc.tiff", [(11, 13), (17, 19)])
    assert inspect(path, "tiff", Limits()) == [
        PageInfo(page=1, width_px=11, height_px=13, pixels=11 * 13),
        PageInfo(page=2, width_px=17, height_px=19, pixels=17 * 19),
    ]


def test_inspect_pdf_counts_pages_and_estimates_pixels_from_dpi(tmp_path):
    path = save_pdf(tmp_path / "doc.pdf", [(72, 144), (36, 72)])
    pages = inspect(path, "pdf", Limits(pdf_dpi=72))
    assert pages == [
        PageInfo(page=1, width_px=72, height_px=144, pixels=72 * 144),
        PageInfo(page=2, width_px=36, height_px=72, pixels=36 * 72),
    ]


def test_inspect_rejects_too_many_pages_for_pdf_and_tiff(tmp_path):
    limits = Limits(max_pages=1)
    assert_error_code(
        "PAGE_LIMIT_EXCEEDED",
        inspect,
        save_pdf(tmp_path / "doc.pdf", [(10, 10), (10, 10)]),
        "pdf",
        limits,
    )
    assert_error_code(
        "PAGE_LIMIT_EXCEEDED",
        inspect,
        save_tiff(tmp_path / "doc.tiff", [(10, 10), (10, 10)]),
        "tiff",
        limits,
    )


@pytest.mark.parametrize(
    "filename,kind,make",
    [
        ("doc.png", "png", lambda path: save_image(path, "PNG", (11, 10))),
        ("doc.pdf", "pdf", lambda path: save_pdf(path, [(11, 10)])),
    ],
)
def test_inspect_rejects_pages_over_pixel_limit(tmp_path, filename, kind, make):
    path = make(tmp_path / filename)
    assert_error_code(
        "PIXEL_LIMIT_EXCEEDED", inspect, path, kind, Limits(max_megapixels=0.0001)
    )


def test_inspect_rejects_unknown_kind(tmp_path):
    path = tmp_path / "doc.bin"
    path.write_bytes(b"not a document")
    assert_error_code("UNSUPPORTED_TYPE", inspect, path, "gif", Limits())
