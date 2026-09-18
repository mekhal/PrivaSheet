"""Page rendering and canonical image bytes."""

import pytest
from PIL import Image, ImageChops

from privasheet.ingest.checks import IngestError, Limits
from privasheet.ingest.render import (
    canonical_page_bytes,
    render_pages,
    save_png_atomic,
)


def save_pdf(path, sizes):
    frames = [Image.new("RGB", size, "white") for size in sizes]
    frames[0].save(path, format="PDF", save_all=True, append_images=frames[1:])
    return path


def assert_error_code(code, fn, *args):
    with pytest.raises(IngestError) as excinfo:
        fn(*args)
    assert excinfo.value.code == code
    assert str(excinfo.value)


def test_render_jpeg_applies_exif_orientation_and_returns_rgb(tmp_path):
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (30, 20), "black")
    for x, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
        for px in range(x * 10, (x + 1) * 10):
            for py in range(20):
                image.putpixel((px, py), color)
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, format="JPEG", quality=100, exif=exif)

    (page,) = render_pages(path, "jpeg", Limits())

    assert page.mode == "RGB"
    assert page.size == (20, 30)
    assert page.getpixel((10, 5))[0] > 200
    assert page.getpixel((10, 15))[1] > 200
    assert page.getpixel((10, 25))[2] > 200


def test_render_tiff_reads_every_frame_as_rgb(tmp_path):
    path = tmp_path / "pages.tiff"
    frames = [
        Image.new("RGB", (3, 5), "red"),
        Image.new("RGB", (7, 11), "green"),
        Image.new("RGB", (13, 17), "blue"),
    ]
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])

    pages = render_pages(path, "tiff", Limits())

    assert [page.mode for page in pages] == ["RGB", "RGB", "RGB"]
    assert [page.size for page in pages] == [(3, 5), (7, 11), (13, 17)]


def test_render_tiff_allows_more_than_previous_page_limit(tmp_path):
    path = tmp_path / "many-pages.tiff"
    frames = [Image.new("RGB", (3 + index, 5 + index), "white") for index in range(11)]
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])

    pages = render_pages(path, "tiff", Limits())

    assert len(pages) == 11
    assert [page.size for page in pages] == [
        (3 + index, 5 + index) for index in range(11)
    ]


def test_render_pdf_page_size_matches_dpi(tmp_path):
    path = save_pdf(tmp_path / "doc.pdf", [(72, 144)])

    (page,) = render_pages(path, "pdf", Limits(pdf_dpi=144))

    assert page.mode == "RGB"
    assert page.size == (144, 288)


@pytest.mark.parametrize(
    ("filename", "kind", "make"),
    [
        ("doc.jpg", "jpeg", lambda path: Image.new("RGB", (11, 10)).save(path)),
        ("doc.png", "png", lambda path: Image.new("RGB", (11, 10)).save(path)),
        ("doc.tiff", "tiff", lambda path: Image.new("RGB", (11, 10)).save(path)),
        ("doc.pdf", "pdf", lambda path: save_pdf(path, [(11, 10)])),
    ],
)
def test_render_enforces_pixel_limit_before_decoding_or_rendering(
    tmp_path, filename, kind, make
):
    path = tmp_path / filename
    make(path)

    assert_error_code(
        "PIXEL_LIMIT_EXCEEDED",
        render_pages,
        path,
        kind,
        Limits(max_megapixels=0.0001),
    )


def test_canonical_page_bytes_include_dimensions_and_rgb_bytes():
    rgb = Image.new("RGB", (2, 1))
    rgb.putdata([(1, 2, 3), (4, 5, 6)])
    rgba = Image.new("RGBA", (2, 1))
    rgba.putdata([(1, 2, 3, 0), (4, 5, 6, 255)])

    assert canonical_page_bytes(rgb) == canonical_page_bytes(rgba)
    assert canonical_page_bytes(rgb) == (
        b"\x00\x00\x00\x02\x00\x00\x00\x01\x01\x02\x03\x04\x05\x06"
    )
    assert canonical_page_bytes(rgb) != canonical_page_bytes(rgb.resize((1, 2)))


def test_save_png_atomic_writes_png_bytes(tmp_path):
    path = tmp_path / "page.png"
    image = Image.new("RGB", (2, 2), "purple")

    save_png_atomic(image, path)

    with Image.open(path) as saved:
        assert saved.format == "PNG"
        assert saved.mode == "RGB"
        assert not ImageChops.difference(saved, image).getbbox()
    assert list(tmp_path.glob("*.tmp")) == []
