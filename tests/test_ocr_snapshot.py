"""OCR snapshot normalization and hashing tests."""

import pytest
from PIL import Image

from privasheet.ocr.engine import OcrError, RawBox
from privasheet.ocr.snapshot import (
    build_snapshot,
    compute_snapshot_id,
    normalize_boxes,
)
from privasheet.store import migrate, open_db
from privasheet.store.repo import get_snapshot, insert_snapshot


class FakeEngine:
    name = "fake-ocr"
    version = "1.0"
    config_sha256 = "config-a"

    def __init__(self, models=None):
        self._models = (
            models
            if models is not None
            else [
                {"name": "det", "sha256": "model-a"},
                {"name": "rec", "sha256": "model-b"},
            ]
        )

    def models(self):
        return list(self._models)

    def recognize(self, image):
        raise NotImplementedError


def raw_box(text, score=0.8, quad=None):
    return RawBox(
        quad_px=quad or ((10, 20), (110, 20), (110, 60), (10, 60)),
        text=text,
        score=score,
    )


def test_normalize_boxes_clamps_and_drops_empty_text():
    boxes = normalize_boxes(
        3,
        200,
        100,
        [
            raw_box(
                "  Invoice  ",
                0.91,
                ((-10, 5), (100, -20), (250, 50), (50, 150)),
            ),
            raw_box("   "),
            raw_box("Total", 0.4, ((20, 10), (40, 10), (40, 30), (20, 30))),
        ],
    )

    assert boxes == [
        {
            "id": "p3-b0000",
            "text": "Invoice",
            "quad": [[0.0, 0.05], [0.5, 0.0], [1.0, 0.5], [0.25, 1.0]],
            "score": 0.91,
        },
        {
            "id": "p3-b0001",
            "text": "Total",
            "quad": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.3], [0.1, 0.3]],
            "score": 0.4,
        },
    ]


def test_normalize_boxes_numbers_each_page_independently():
    page_1 = normalize_boxes(1, 100, 100, [raw_box("A"), raw_box("B")])
    page_2 = normalize_boxes(2, 100, 100, [raw_box("C")])

    assert [box["id"] for box in page_1] == ["p1-b0000", "p1-b0001"]
    assert [box["id"] for box in page_2] == ["p2-b0000"]


def test_normalize_boxes_rejects_non_positive_dimensions():
    with pytest.raises(OcrError) as excinfo:
        normalize_boxes(1, 0, 100, [raw_box("A")])

    assert excinfo.value.code == "OCR_FAILED"
    assert "page dimensions" in excinfo.value.detail


def test_compute_snapshot_id_is_stable_and_sensitive_to_inputs():
    page_1 = Image.new("RGB", (2, 1), "white")
    page_2 = Image.new("RGB", (1, 2), "black")
    engine = FakeEngine(
        models=[{"name": "z", "sha256": "2"}, {"name": "a", "sha256": "1"}]
    )

    first = compute_snapshot_id([page_1, page_2], engine)
    second = compute_snapshot_id([page_1.copy(), page_2.copy()], engine)

    assert first == second
    assert first.startswith("sha256:")
    assert compute_snapshot_id([page_2, page_1], engine) != first

    changed_pixel = page_1.copy()
    changed_pixel.putpixel((0, 0), (1, 2, 3))
    assert compute_snapshot_id([changed_pixel, page_2], engine) != first

    changed_model = FakeEngine(
        models=[{"name": "z", "sha256": "changed"}, {"name": "a", "sha256": "1"}]
    )
    assert compute_snapshot_id([page_1, page_2], changed_model) != first

    changed_config = FakeEngine(models=engine.models())
    changed_config.config_sha256 = "config-b"
    assert compute_snapshot_id([page_1, page_2], changed_config) != first


def test_build_snapshot_shape_and_store_round_trip(tmp_path):
    snapshot_id = "sha256:" + "a" * 64
    engine = FakeEngine(
        models=[{"name": "rec", "sha256": "b"}, {"name": "det", "sha256": "a"}]
    )
    pages = [
        {
            "page": 1,
            "width": 200,
            "height": 100,
            "boxes": normalize_boxes(1, 200, 100, [raw_box("A")]),
        },
        {
            "page": 2,
            "width": 50,
            "height": 80,
            "boxes": normalize_boxes(2, 50, 80, [raw_box("B")]),
        },
    ]

    snapshot = build_snapshot(snapshot_id, engine, pages, "2026-09-19T00:00:00Z")

    digest = "a" * 64
    assert snapshot == {
        "snapshot_id": snapshot_id,
        "created_at": "2026-09-19T00:00:00Z",
        "engine": {
            "name": "fake-ocr",
            "version": "1.0",
            "models": [{"name": "rec", "sha256": "b"}, {"name": "det", "sha256": "a"}],
            "config_sha256": "config-a",
        },
        "pages": [
            {
                "page": 1,
                "width": 200,
                "height": 100,
                "boxes": pages[0]["boxes"],
                "image": f"pages/{digest}/page-1.png",
            },
            {
                "page": 2,
                "width": 50,
                "height": 80,
                "boxes": pages[1]["boxes"],
                "image": f"pages/{digest}/page-2.png",
            },
        ],
    }

    conn = open_db(tmp_path / "store.db")
    try:
        migrate(conn)
        insert_snapshot(conn, snapshot)
        assert get_snapshot(conn, snapshot_id) == snapshot
    finally:
        conn.close()


def test_build_snapshot_rejects_document_with_no_boxes():
    snapshot_id = "sha256:" + "b" * 64
    pages = [{"page": 1, "width": 10, "height": 10, "boxes": []}]

    with pytest.raises(OcrError) as excinfo:
        build_snapshot(snapshot_id, FakeEngine(), pages, "2026-09-19T00:00:00Z")

    assert excinfo.value.code == "OCR_FAILED"
    assert excinfo.value.detail
