"""Reading-order layout helpers for OCR snapshots."""

_REGIONS = (
    ("top-left", "top", "top-right"),
    ("left", "center", "right"),
    ("bottom-left", "bottom", "bottom-right"),
)


def lines(snapshot: dict) -> list[list[list[dict]]]:
    """Return boxes grouped into reading-order lines for each page.

    Boxes are grouped by vertical overlap of their normalized quads. Pages keep
    snapshot order, lines are top-to-bottom, and boxes inside each line are
    left-to-right. Ties are deterministic by box id.
    """
    return [_page_lines(page.get("boxes", [])) for page in snapshot.get("pages", [])]


def prompt_lines(snapshot: dict) -> list[str]:
    """Return OCR boxes formatted for extraction prompts."""
    result = []
    for page, page_lines in zip(
        snapshot.get("pages", []), lines(snapshot), strict=True
    ):
        page_number = page.get("page")
        for line in page_lines:
            for item in line:
                result.append(
                    f"{item['id']} | {page_number} | {_region(item)} | "
                    f"{item.get('text', '')}"
                )
    return result


def _page_lines(boxes: list[dict]) -> list[list[dict]]:
    grouped = []
    ordered = sorted(boxes, key=lambda item: (*_top_left(item), str(item["id"])))
    for item in ordered:
        extent = _vertical_extent(item)
        for group in grouped:
            if _overlaps(extent, group["band"]):
                group["boxes"].append(item)
                group["top"] = min(group["top"], extent[0])
                group["band"] = (
                    max(group["band"][0], extent[0]),
                    min(group["band"][1], extent[1]),
                )
                break
        else:
            grouped.append(
                {
                    "band": extent,
                    "top": extent[0],
                    "boxes": [item],
                }
            )

    grouped.sort(key=lambda group: (group["top"], _min_box_id(group["boxes"])))
    return [
        sorted(group["boxes"], key=lambda item: (_center(item)[0], str(item["id"])))
        for group in grouped
    ]


def _vertical_extent(box: dict) -> tuple[float, float]:
    y_values = [point[1] for point in box["quad"]]
    return min(y_values), max(y_values)


def _horizontal_extent(box: dict) -> tuple[float, float]:
    x_values = [point[0] for point in box["quad"]]
    return min(x_values), max(x_values)


def _top_left(box: dict) -> tuple[float, float]:
    top, _ = _vertical_extent(box)
    left, _ = _horizontal_extent(box)
    return top, left


def _center(box: dict) -> tuple[float, float]:
    xs = [point[0] for point in box["quad"]]
    ys = [point[1] for point in box["quad"]]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _overlaps(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return min(first[1], second[1]) > max(first[0], second[0])


def _min_box_id(boxes: list[dict]) -> str:
    return min(str(item["id"]) for item in boxes)


def _region(box: dict) -> str:
    x, y = _center(box)
    column = _third(x)
    row = _third(y)
    return _REGIONS[row][column]


def _third(value: float) -> int:
    if value < 1 / 3:
        return 0
    if value < 2 / 3:
        return 1
    return 2
