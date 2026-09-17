"""Arithmetic issue codes are no longer emitted by validation checks."""

from copy import deepcopy

from privasheet.validate.checks import check_extraction, check_review

ARITHMETIC_CODES = {
    "LINE_AMOUNT_MISMATCH",
    "SUBTOTAL_MISMATCH",
    "TOTAL_MISMATCH",
    "RECONCILIATION_UNAVAILABLE",
}


def document(
    *,
    item_rows=None,
    subtotal="9999.99",
    discount=None,
    shipping="2.00",
    tax="invalid",
    total="1.00",
    include_services=True,
):
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
            "subtotal": subtotal,
            "discount": discount,
            "shipping": shipping,
            "tax": tax,
            "total": total,
        },
        "tables": {
            "items": item_rows
            if item_rows is not None
            else [
                {"qty": "2", "unit_price": "5.00", "amount": "999.00"},
                {"qty": None, "unit_price": "3.00", "amount": "7.00"},
            ],
        },
    }
    if include_services:
        review["tables"]["services"] = [
            {"qty": "invalid", "unit_price": "10.00", "amount": "1.00"}
        ]
    else:
        template["tables"] = template["tables"][:1]
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


def assert_no_arithmetic_issues(template, review):
    before = deepcopy((template, review))
    snapshot, extracted = extracted_from(review)

    review_issues = check_review(template, review)
    extraction_issues = check_extraction(template, snapshot, extracted)[1]

    assert arithmetic_issues(review_issues) == []
    assert arithmetic_issues(extraction_issues) == []
    assert (template, review) == before


def test_arithmetic_issue_codes_are_not_emitted():
    template, review = document()
    assert_no_arithmetic_issues(template, review)


def test_arithmetic_codes_are_reserved_for_other_layers():
    template, review = document()
    assert_no_arithmetic_issues(template, review)


def test_balanced_and_inputs_unchanged():
    template, review = document(
        item_rows=[{"qty": "2", "unit_price": "5.00", "amount": "10.00"}],
        subtotal="10.00",
        shipping="2.00",
        tax="1.00",
        total="13.00",
    )
    assert_no_arithmetic_issues(template, review)


def test_mismatches():
    template, review = document()
    assert_no_arithmetic_issues(template, review)


def test_required_operands():
    template, review = document(subtotal=None, total=None)
    assert_no_arithmetic_issues(template, review)


def test_optional_operands():
    template, review = document(discount=None, shipping=None, tax=None)
    assert_no_arithmetic_issues(template, review)


def test_discount_shipping_tax_signs():
    template, review = document(
        subtotal="100.00",
        discount="-10.00",
        shipping="-5.00",
        tax="-2.00",
        total="200.00",
    )
    assert_no_arithmetic_issues(template, review)


def test_line_rule_needs_all_columns():
    template, review = document(
        item_rows=[
            {"qty": "2", "unit_price": None, "amount": "999.00"},
            {"qty": None, "unit_price": "3.00", "amount": "7.00"},
        ]
    )
    assert_no_arithmetic_issues(template, review)


def test_other_rules_need_definitions():
    template, review = document(include_services=False)
    template["fields"] = [
        field for field in template["fields"] if field["key"] != "subtotal"
    ]
    assert_no_arithmetic_issues(template, review)


def test_unrounded_product_tolerance_boundary():
    template, review = document(
        item_rows=[{"qty": "3", "unit_price": "0.3333", "amount": "1.01"}],
        subtotal="1.01",
        total="1.02",
    )
    assert_no_arithmetic_issues(template, review)


def test_total_default_tolerance():
    template, review = document(subtotal="1.00", tax="0.01", total="1.02")
    assert_no_arithmetic_issues(template, review)


def test_custom_tolerance_and_local_context():
    template, review = document(subtotal="10.00", tax="0.50", total="99.99")
    template["validation"] = {"arithmetic_tolerance": "100.00"}
    assert_no_arithmetic_issues(template, review)


def test_parenthesized_negatives_match_review():
    template, review = document(discount="(5.00)", total="999.99")
    assert_no_arithmetic_issues(template, review)


def test_empty_table_sum_and_multiple_tables():
    template, review = document(item_rows=[])
    review["tables"]["services"].append(
        {"qty": "4", "unit_price": "10.00", "amount": "1000.00"}
    )
    assert_no_arithmetic_issues(template, review)


def test_ungrounded_optional_operand_is_unavailable():
    template, review = document(discount=None, total="999.99")
    snapshot, extracted = extracted_from(review)
    extracted["fields"]["discount"] = {"box_ids": [], "span": "", "raw": None}

    assert arithmetic_issues(check_review(template, review)) == []
    assert arithmetic_issues(check_extraction(template, snapshot, extracted)[1]) == []


def test_subtotal_tolerance():
    template, review = document(subtotal="100.00", total="100.03")
    assert_no_arithmetic_issues(template, review)
