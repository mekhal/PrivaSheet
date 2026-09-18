"""Upload validation and lightweight document inspection."""

from io import BytesIO

import pytest
from PIL import Image

from privasheet.ingest import checks
from privasheet.ingest.checks import (
    IngestError,
    Limits,
    PageInfo,
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
    assert limits.max_megapixels == 40
    assert limits.pdf_dpi == 200


def test_ingest_has_no_file_or_page_count_limit_api():
    limits = Limits()
    assert not hasattr(limits, "max_files")
    assert not hasattr(limits, "max_pages")
    assert not hasattr(checks, "check_batch_size")


def test_check_batch_size_uses_file_limit():
    limits = Limits()
    assert not hasattr(limits, "max_files")
    assert not hasattr(checks, "check_batch_size")


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


def test_inspect_allows_more_than_previous_page_limit_for_pdf_and_tiff(tmp_path):
    sizes = [(10 + index, 12 + index) for index in range(11)]

    pdf_pages = inspect(save_pdf(tmp_path / "doc.pdf", sizes), "pdf", Limits())
    tiff_pages = inspect(save_tiff(tmp_path / "doc.tiff", sizes), "tiff", Limits())

    assert len(pdf_pages) == 11
    assert len(tiff_pages) == 11
    assert [page.page for page in pdf_pages] == list(range(1, 12))
    assert [page.page for page in tiff_pages] == list(range(1, 12))


def test_inspect_rejects_too_many_pages_for_pdf_and_tiff(tmp_path):
    sizes = [(10 + index, 12 + index) for index in range(11)]

    pdf_pages = inspect(save_pdf(tmp_path / "many.pdf", sizes), "pdf", Limits())
    tiff_pages = inspect(save_tiff(tmp_path / "many.tiff", sizes), "tiff", Limits())

    assert len(pdf_pages) == 11
    assert len(tiff_pages) == 11


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


@pytest.mark.parametrize("kind", ["png", "tiff"])
def test_inspect_translates_pillow_decompression_bomb(monkeypatch, tmp_path, kind):
    from PIL import Image

    def bomb(_path):
        raise Image.DecompressionBombError("declared dimensions are too large")

    monkeypatch.setattr(Image, "open", bomb)
    path = tmp_path / f"doc.{kind}"
    path.write_bytes(b"placeholder")

    assert_error_code("PIXEL_LIMIT_EXCEEDED", inspect, path, kind, Limits())


def test_inspect_pdf_reads_page_geometry_without_rendering(monkeypatch, tmp_path):
    import pypdfium2 as pdfium

    sizes = [(72, 144), (36, 72)]

    class FakePage:
        def __init__(self, size):
            self.size = size
            self.closed = False

        def get_size(self):
            return self.size

        def render(self, **_kwargs):
            raise AssertionError("page inspection must not render PDF pages")

        def close(self):
            self.closed = True

    class FakePdfDocument:
        def __init__(self, _path):
            self.closed = False

        def __len__(self):
            return 2

        def __getitem__(self, index):
            return FakePage(sizes[index])

        def close(self):
            self.closed = True

    monkeypatch.setattr(pdfium, "PdfDocument", FakePdfDocument)
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7\n")

    assert inspect(path, "pdf", Limits(pdf_dpi=72)) == [
        PageInfo(page=1, width_px=72, height_px=144, pixels=72 * 144),
        PageInfo(page=2, width_px=36, height_px=72, pixels=36 * 72),
    ]


def test_inspect_pdf_reads_geometry_for_many_pages_without_rendering(
    monkeypatch, tmp_path
):
    import pypdfium2 as pdfium

    sizes = [(72 + index, 144 + index) for index in range(11)]
    opened_pages = []

    class FakePage:
        def __init__(self, index):
            self.index = index

        def get_size(self):
            return sizes[self.index]

        def render(self, **_kwargs):
            raise AssertionError("page inspection must not render PDF pages")

        def close(self):
            pass

    class FakePdfDocument:
        def __init__(self, _path):
            pass

        def __len__(self):
            return len(sizes)

        def __getitem__(self, index):
            opened_pages.append(index)
            return FakePage(index)

        def close(self):
            pass

    monkeypatch.setattr(pdfium, "PdfDocument", FakePdfDocument)
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7\n")

    pages = inspect(path, "pdf", Limits(pdf_dpi=72))

    assert opened_pages == list(range(11))
    assert [page.page for page in pages] == list(range(1, 12))
    assert pages[-1] == PageInfo(page=11, width_px=82, height_px=154, pixels=82 * 154)


def test_inspect_pdf_rejects_page_limit_before_opening_pages(monkeypatch, tmp_path):
    import pypdfium2 as pdfium

    sizes = [(72 + index, 144 + index) for index in range(11)]
    opened_pages = []

    class FakePage:
        def __init__(self, index):
            self.index = index

        def get_size(self):
            return sizes[self.index]

        def render(self, **_kwargs):
            raise AssertionError("page inspection must not render PDF pages")

        def close(self):
            pass

    class FakePdfDocument:
        def __init__(self, _path):
            pass

        def __len__(self):
            return len(sizes)

        def __getitem__(self, index):
            opened_pages.append(index)
            return FakePage(index)

        def close(self):
            pass

    monkeypatch.setattr(pdfium, "PdfDocument", FakePdfDocument)
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.7\n")

    pages = inspect(path, "pdf", Limits(pdf_dpi=72))

    assert opened_pages == list(range(11))
    assert [page.page for page in pages] == list(range(1, 12))


def test_inspect_tiff_allows_all_frames(monkeypatch, tmp_path):
    from PIL import Image

    class FakeTiff:
        n_frames = 11

        def __init__(self):
            self.index = 0

        @property
        def size(self):
            return (10 + self.index, 20 + self.index)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def seek(self, index):
            self.index = index

    monkeypatch.setattr(Image, "open", lambda _path: FakeTiff())
    path = tmp_path / "doc.tiff"
    path.write_bytes(b"II*\x00")

    pages = inspect(path, "tiff", Limits())
    assert len(pages) == 11
    assert pages[-1] == PageInfo(page=11, width_px=20, height_px=30, pixels=20 * 30)


def test_inspect_tiff_reads_geometry_for_many_frames(monkeypatch, tmp_path):
    from PIL import Image

    sought_frames = []

    class FakeTiff:
        n_frames = 11

        def __init__(self):
            self.index = 0

        @property
        def size(self):
            return (20 + self.index, 30 + self.index)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def seek(self, index):
            sought_frames.append(index)
            self.index = index

    monkeypatch.setattr(Image, "open", lambda _path: FakeTiff())
    path = tmp_path / "doc.tiff"
    path.write_bytes(b"II*\x00")

    pages = inspect(path, "tiff", Limits())

    assert sought_frames == list(range(11))
    assert [page.page for page in pages] == list(range(1, 12))
    assert pages[-1] == PageInfo(page=11, width_px=30, height_px=40, pixels=30 * 40)


def test_inspect_tiff_does_not_decode_frames_for_many_pages(monkeypatch, tmp_path):
    from PIL import Image

    sought_frames = []

    class FakeTiff:
        n_frames = 11

        def __init__(self):
            self.index = 0

        @property
        def size(self):
            return (20 + self.index, 30 + self.index)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def seek(self, index):
            sought_frames.append(index)
            self.index = index

        def load(self):
            raise AssertionError("page inspection must not decode TIFF frames")

    monkeypatch.setattr(Image, "open", lambda _path: FakeTiff())
    path = tmp_path / "doc.tiff"
    path.write_bytes(b"II*\x00")

    pages = inspect(path, "tiff", Limits())

    assert sought_frames == list(range(11))
    assert [page.page for page in pages] == list(range(1, 12))


def test_inspect_tiff_rejects_page_limit_before_seeking_frames(monkeypatch, tmp_path):
    from PIL import Image

    sought_frames = []

    class FakeTiff:
        n_frames = 11

        def __init__(self):
            self.index = 0

        @property
        def size(self):
            return (20 + self.index, 30 + self.index)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def seek(self, index):
            sought_frames.append(index)
            self.index = index

        def load(self):
            raise AssertionError("page inspection must not decode TIFF frames")

    monkeypatch.setattr(Image, "open", lambda _path: FakeTiff())
    path = tmp_path / "doc.tiff"
    path.write_bytes(b"II*\x00")

    pages = inspect(path, "tiff", Limits())

    assert sought_frames == list(range(11))
    assert [page.page for page in pages] == list(range(1, 12))


def test_inspect_rejects_unknown_kind(tmp_path):
    path = tmp_path / "doc.bin"
    path.write_bytes(b"not a document")
    assert_error_code("UNSUPPORTED_TYPE", inspect, path, "gif", Limits())


@pytest.mark.parametrize(
    ("filename", "kind"),
    [
        ("bad.png", "png"),
        ("bad.tiff", "tiff"),
        ("bad.pdf", "pdf"),
    ],
)
def test_inspect_reports_clear_code_for_malformed_uploads(tmp_path, filename, kind):
    path = tmp_path / filename
    path.write_bytes(b"not a valid upload")
    assert_error_code("INSPECT_FAILED", inspect, path, kind, Limits())
