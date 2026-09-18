"""Reading-order layout helpers from design sections 3 and 5.3."""

from privasheet.layout import lines, prompt_lines, region


def box(box_id, text, left, top, right, bottom):
    return {
        "id": box_id,
        "text": text,
        "quad": [[left, top], [right, top], [right, bottom], [left, bottom]],
    }


def test_lines_group_two_columns_by_vertical_overlap_and_read_left_to_right():
    snapshot = {
        "pages": [
            {
                "page": 1,
                "boxes": [
                    box("r2c2", "amount", 0.66, 0.205, 0.90, 0.245),
                    box("r1c2", "date", 0.65, 0.105, 0.90, 0.145),
                    box("r2c1", "total", 0.10, 0.20, 0.30, 0.24),
                    box("r1c1", "invoice", 0.10, 0.10, 0.35, 0.14),
                ],
            }
        ]
    }

    assert [[item["id"] for item in line] for line in lines(snapshot)[0]] == [
        ["r1c1", "r1c2"],
        ["r2c1", "r2c2"],
    ]


def test_lines_keep_slightly_skewed_baseline_together():
    snapshot = {
        "pages": [
            {
                "page": 1,
                "boxes": [
                    {
                        "id": "right",
                        "text": "right",
                        "quad": [
                            [0.55, 0.125],
                            [0.80, 0.115],
                            [0.80, 0.155],
                            [0.55, 0.165],
                        ],
                    },
                    {
                        "id": "left",
                        "text": "left",
                        "quad": [
                            [0.10, 0.10],
                            [0.35, 0.11],
                            [0.35, 0.15],
                            [0.10, 0.14],
                        ],
                    },
                    box("next", "next", 0.10, 0.19, 0.30, 0.23),
                ],
            }
        ]
    }

    assert [[item["id"] for item in line] for line in lines(snapshot)[0]] == [
        ["left", "right"],
        ["next"],
    ]


def test_lines_are_per_page_and_empty_pages_are_preserved():
    snapshot = {
        "pages": [
            {"page": 1, "boxes": [box("p1", "page one", 0.10, 0.10, 0.30, 0.20)]},
            {"page": 2, "boxes": []},
            {"page": 3, "boxes": [box("p3", "page three", 0.10, 0.05, 0.30, 0.10)]},
        ]
    }

    assert [[item["id"] for item in line] for line in lines(snapshot)[0]] == [["p1"]]
    assert lines(snapshot)[1] == []
    assert [[item["id"] for item in line] for line in lines(snapshot)[2]] == [["p3"]]


def test_prompt_lines_use_page_region_and_text_in_reading_order():
    snapshot = {
        "pages": [
            {
                "page": 7,
                "boxes": [
                    box("br", "bottom right", 0.80, 0.80, 0.90, 0.90),
                    box("tl", "top left", 0.02, 0.02, 0.20, 0.20),
                    box("c", "center", 0.45, 0.45, 0.55, 0.55),
                ],
            }
        ]
    }

    assert prompt_lines(snapshot) == [
        "tl | 7 | top-left | top left",
        "c | 7 | center | center",
        "br | 7 | bottom-right | bottom right",
    ]


def test_prompt_lines_include_all_nine_regions():
    snapshot = {
        "pages": [
            {
                "page": 1,
                "boxes": [
                    box("top-left", "", 0.00, 0.00, 0.10, 0.10),
                    box("top", "", 0.45, 0.00, 0.55, 0.10),
                    box("top-right", "", 0.90, 0.00, 1.00, 0.10),
                    box("left", "", 0.00, 0.45, 0.10, 0.55),
                    box("center", "", 0.45, 0.45, 0.55, 0.55),
                    box("right", "", 0.90, 0.45, 1.00, 0.55),
                    box("bottom-left", "", 0.00, 0.90, 0.10, 1.00),
                    box("bottom", "", 0.45, 0.90, 0.55, 1.00),
                    box("bottom-right", "", 0.90, 0.90, 1.00, 1.00),
                ],
            }
        ]
    }

    assert [line.split(" | ")[2] for line in prompt_lines(snapshot)] == [
        "top-left",
        "top",
        "top-right",
        "left",
        "center",
        "right",
        "bottom-left",
        "bottom",
        "bottom-right",
    ]


def test_region_is_public_for_a_single_box():
    assert region(box("total", "Grand Total", 0.70, 0.70, 0.90, 0.82)) == (
        "bottom-right"
    )
