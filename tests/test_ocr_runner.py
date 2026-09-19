"""OCR runner child-process behavior."""

from __future__ import annotations

import multiprocessing
import queue
import threading

import pytest
from PIL import Image

from privasheet.ingest.checks import Limits
from privasheet.ingest.render import render_pages
from privasheet.ocr.engine import OcrError
from privasheet.ocr.runner import (
    OcrCancelled,
    OcrTimeout,
    run_ocr_job,
)
from privasheet.ocr.snapshot import compute_snapshot_id
from tests.fake_ocr import make_engine

FAKE_ENGINE = "tests.fake_ocr:make_engine"
SLEEPING_ENGINE = "tests.fake_ocr:make_sleeping_engine"
RAISING_ENGINE = "tests.fake_ocr:make_raising_engine"


@pytest.fixture(autouse=True)
def assert_no_leftover_children():
    yield
    for child in multiprocessing.active_children():
        child.join(timeout=0.2)
    assert multiprocessing.active_children() == []


def test_png_job_returns_snapshot_boxes_and_page_images(tmp_path):
    path = tmp_path / "page.png"
    out_dir = tmp_path / "ocr"
    Image.new("RGB", (20, 10), "white").save(path)

    result = run_ocr_job(
        path,
        "png",
        Limits(),
        out_dir,
        timeout_s=5,
        engine=FAKE_ENGINE,
    )

    expected_snapshot_id = compute_snapshot_id(
        render_pages(path, "png", Limits()), make_engine()
    )
    assert result.snapshot_id == expected_snapshot_id
    assert result.engine == {
        "name": "fake-ocr",
        "version": "1.0",
        "models": [
            {"name": "det", "sha256": "fake-det"},
            {"name": "rec", "sha256": "fake-rec"},
        ],
        "config_sha256": "fake-config",
    }
    assert result.pages == [
        {
            "page": 1,
            "width": 20,
            "height": 10,
            "boxes": [
                {
                    "id": "p1-b0000",
                    "text": "20x10",
                    "quad": [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
                    "score": 0.75,
                }
            ],
            "tmp_image": str(out_dir / "page-1.png"),
        }
    ]
    assert (out_dir / "page-1.png").is_file()


def test_two_page_tiff_reports_progress_for_both_pages(tmp_path):
    path = tmp_path / "pages.tiff"
    out_dir = tmp_path / "ocr"
    frames = [
        Image.new("RGB", (10, 10), "white"),
        Image.new("RGB", (12, 8), "white"),
    ]
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])
    progress = []

    run_ocr_job(
        path,
        "tiff",
        Limits(),
        out_dir,
        timeout_s=5,
        engine=FAKE_ENGINE,
        on_progress=lambda stage, page, total: progress.append((stage, page, total)),
    )

    assert [item for item in progress if item[0] == "render"] == [
        ("render", 1, 2),
        ("render", 2, 2),
    ]
    assert [item for item in progress if item[0] == "ocr"] == [
        ("ocr", 1, 2),
        ("ocr", 2, 2),
    ]


def test_sleeping_engine_hits_budget_and_child_is_dead(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (20, 10), "white").save(path)

    with pytest.raises(OcrTimeout) as excinfo:
        run_ocr_job(
            path,
            "png",
            Limits(),
            tmp_path / "ocr",
            timeout_s=1,
            engine=SLEEPING_ENGINE,
        )

    assert excinfo.value.stage == "ocr"
    assert excinfo.value.page == 1
    assert excinfo.value.total == 1
    assert excinfo.value.elapsed_s >= 1


def test_cancel_stops_running_job(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (20, 10), "white").save(path)
    cancel = threading.Event()
    started = threading.Event()
    results = queue.Queue()

    def run():
        try:
            run_ocr_job(
                path,
                "png",
                Limits(),
                tmp_path / "ocr",
                timeout_s=10,
                engine=SLEEPING_ENGINE,
                on_progress=lambda stage, page, total: started.set(),
                cancel=cancel,
            )
        except Exception as exc:  # noqa: BLE001 - test captures the thread result.
            results.put(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(timeout=5)
    cancel.set()
    thread.join(timeout=5)

    exc = results.get_nowait()
    assert isinstance(exc, OcrCancelled)


def test_raising_engine_gives_ocr_failed(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (20, 10), "white").save(path)

    with pytest.raises(OcrError) as excinfo:
        run_ocr_job(
            path,
            "png",
            Limits(),
            tmp_path / "ocr",
            timeout_s=5,
            engine=RAISING_ENGINE,
        )

    assert excinfo.value.code == "OCR_FAILED"


def test_garbage_file_gives_document_unreadable(tmp_path):
    path = tmp_path / "bad.png"
    path.write_bytes(b"not an image")

    with pytest.raises(OcrError) as excinfo:
        run_ocr_job(
            path,
            "png",
            Limits(),
            tmp_path / "ocr",
            timeout_s=5,
            engine=FAKE_ENGINE,
        )

    assert excinfo.value.code == "DOCUMENT_UNREADABLE"


def test_engine_with_no_boxes_gives_ocr_failed(tmp_path):
    path = tmp_path / "blank.png"
    Image.new("RGB", (1, 1), "white").save(path)

    with pytest.raises(OcrError) as excinfo:
        run_ocr_job(
            path,
            "png",
            Limits(),
            tmp_path / "ocr",
            timeout_s=5,
            engine=FAKE_ENGINE,
        )

    assert excinfo.value.code == "OCR_FAILED"
