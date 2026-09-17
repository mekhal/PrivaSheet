"""Arithmetic issue codes are no longer emitted by validation checks."""

from copy import deepcopy

import pytest

from privasheet.validate.checks import check_extraction, check_review

ARITHMETIC_CODES = {
    "LINE_AMOUNT_MISMATCH",
    "SUBTOTAL_MISMATCH",
    "TOTAL_MISMATCH",
    "RECONCILIATION_UNAVAILABLE",
}


def document():
    template = {
        "fields": [
            {"key": "subtotal", "type": "decimal"},
            {"key": "discount", "type": "decimal", "required": False},
            {"key": "shipping", "type": "decimal", "required": False},
            {"key": "tax", "type": "decimal", "required": False},
            {"key": "total", "type": "decimal"},
        ],
        "tables": [
            {
                "key": "items",
                "columns": [
                    {"key": "qty", "type": "decimal"},
                    {"key": "unit_price", "type": "decimal"},
                    {"key": "amount", "type": "decimal"},
                ],
            },
            {
                "key": "services",
                "columns": [
                    {"key": "qty", "type": "decimal"},
                    {"key": "unit_price", "type": "decimal"},
                    {"key": "amount", "type": "decimal"},
                ],
            },
        ],
        "match": {"min_key_label_ratio": 0},
    }
    review = {
        "fields": {
            "subtotal": "9999.99",
            "discount": None,
            "shipping": "2.00",
            "tax": "invalid",
            "total": "1.00",
        },
        "tables": {
            "items": [
                {"qty": "2", "unit_price": "5.00", "amount": "999.00"},
                {"qty": None, "unit_price": "3.00", "amount": "7.00"},
            ],
            "services": [
                {"qty": "invalid", "unit_price": "10.00", "amount": "1.00"}
            ],
        },
    }
    return template, review


def extracted_from(review):
    boxes = []

    def evidence(value):
        if value is None:
            return {"missing": True}
        box_id = f"b{len(boxes)}"
        boxes.append({"id": box_id, "text": value, "score": 1.0})
        return {"box_ids": [box_id], "span": value, "raw": value}

    extracted = {
        "fields": {key: evidence(value) for key, value in review["fields"].items()},
        "tables": {
            table: [
                {column: evidence(value) for column, value in row.items()}
                for row in rows
            ]
            for table, rows in review["tables"].items()
        },
    }
    return {"pages": [{"page": 1, "boxes": boxes}]}, extracted


def arithmetic_issues(issues):
    return [issue for issue in issues if issue.code in ARITHMETIC_CODES]


@pytest.mark.parametrize("mode", ["extraction", "review"])
def test_arithmetic_issue_codes_are_not_emitted(mode):
    template, review = document()
    before = deepcopy((template, review))

    if mode == "review":
        issues = check_review(template, review)
    else:
        snapshot, extracted = extracted_from(review)
        issues = check_extraction(template, snapshot, extracted)[1]

    assert arithmetic_issues(issues) == []
    assert (template, review) == before


def test_arithmetic_codes_are_reserved_for_other_layers():
    template, review = document()
    snapshot, extracted = extracted_from(review)

    extraction_codes = {
        issue.code for issue in check_extraction(template, snapshot, extracted)[1]
    }
    review_codes = {issue.code for issue in check_review(template, review)}

    assert not (extraction_codes | review_codes) & ARITHMETIC_CODES
