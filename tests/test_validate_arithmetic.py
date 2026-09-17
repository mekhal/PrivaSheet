"""Arithmetic acceptance checks through extraction and canonical review."""

from copy import deepcopy
from decimal import localcontext

import pytest

from privasheet.validate.checks import check_extraction, check_review

CODES = {
    "LINE_AMOUNT_MISMATCH",
    "SUBTOTAL_MISMATCH",
    "TOTAL_MISMATCH",
    "RECONCILIATION_UNAVAILABLE",
}


def document(fields=None, rows=None, columns=("qty", "unit_price", "amount")):
    fields = (
        fields
        if fields is not None
        else {"subtotal": "1200.00", "tax": "84.00", "total": "1284.00"}
    )
    rows = (
        rows
        if rows is not None
        else [{"qty": "2", "unit_price": "600.00", "amount": "1200.00"}]
    )
    template = {
        "fields": [{"key": key, "type": "decimal"} for key in fields],
        "tables": [
            {
                "key": "items",
                "columns": [{"key": key, "type": "decimal"} for key in columns],
            }
        ],
        "match": {"min_key_label_ratio": 0.6},
    }
    return template, {"fields": fields, "tables": {"items": rows}}


def run(template, values, mode):
    if mode == "review":
        issues = check_review(template, values)
    else:
        boxes = []

        def evidence(value):
            if value is None:
                return {"missing": True}
            box_id = str(len(boxes))
            boxes.append({"id": box_id, "text": value, "score": 1})
            return {"raw": value, "span": value, "box_ids": [box_id]}

        extracted = {
            "fields": {key: evidence(value) for key, value in values["fields"].items()},
            "tables": {
                key: [
                    {col: evidence(value) for col, value in row.items()} for row in rows
                ]
                for key, rows in values["tables"].items()
            },
        }
        issues = check_extraction(template, {"pages": [{"boxes": boxes}]}, extracted)[1]
    return [issue for issue in issues if issue.code in CODES]


@pytest.fixture(params=["extraction", "review"])
def mode(request):
    return request.param


def test_balanced_and_inputs_unchanged(mode):
    template, values = document()
    before = deepcopy((template, values))
    assert run(template, values, mode) == []
    assert (template, values) == before


@pytest.mark.parametrize(
    "change,code,target,numbers",
    [
        (
            "qty",
            "LINE_AMOUNT_MISMATCH",
            "items[0].amount",
            ("3", "600.00", "1800.00", "1200.00"),
        ),
        ("subtotal", "SUBTOTAL_MISMATCH", "items", ("1200.00", "1201.00")),
        (
            "total",
            "TOTAL_MISMATCH",
            "total",
            ("1200.00", "84.00", "1284.00", "1248.00"),
        ),
    ],
)
def test_mismatches(mode, change, code, target, numbers):
    template, values = document()
    if change == "qty":
        values["tables"]["items"][0][change] = "3"
    elif change == "subtotal":
        values["fields"].update(subtotal="1201.00", total="1285.00")
    else:
        values["fields"][change] = "1248.00"
    issues = run(template, values, mode)
    assert [(i.code, i.target) for i in issues] == [(code, target)]
    assert all(number in issues[0].detail for number in numbers)


@pytest.mark.parametrize(
    "operand", ["qty", "unit_price", "amount", "subtotal", "total"]
)
@pytest.mark.parametrize("value", [None, "invalid"])
def test_required_operands(mode, operand, value):
    template, values = document()
    container = (
        values["fields"]
        if operand in values["fields"]
        else values["tables"]["items"][0]
    )
    container[operand] = value
    issues = run(template, values, mode)
    expected = 2 if operand in {"amount", "subtotal"} else 1
    assert len(issues) == expected
    assert all(
        i.code == "RECONCILIATION_UNAVAILABLE" and operand in i.detail for i in issues
    )


@pytest.mark.parametrize("operand", ["discount", "shipping", "tax"])
@pytest.mark.parametrize("value", [None, "invalid", "3.00"])
def test_optional_operands(mode, operand, value):
    template, values = document(
        fields={"subtotal": "1200", "total": "1200", operand: value}
    )
    issues = run(template, values, mode)
    assert [i.code for i in issues] == (
        []
        if value is None
        else ["RECONCILIATION_UNAVAILABLE" if value == "invalid" else "TOTAL_MISMATCH"]
    )


def test_discount_shipping_tax_signs(mode):
    template, values = document(
        fields={
            "subtotal": "1200",
            "discount": "100",
            "shipping": "20",
            "tax": "84",
            "total": "1204",
        }
    )
    assert run(template, values, mode) == []


@pytest.mark.parametrize(
    "columns", [(), ("qty", "amount"), ("unit_price", "amount"), ("qty", "unit_price")]
)
def test_line_rule_needs_all_columns(mode, columns):
    template, values = document(
        fields={}, columns=columns, rows=[{key: None for key in columns}]
    )
    assert run(template, values, mode) == []


@pytest.mark.parametrize(
    "fields,columns",
    [({"subtotal": None}, ()), ({"total": None}, ("amount",)), ({}, ("amount",))],
)
def test_other_rules_need_definitions(mode, fields, columns):
    template, values = document(
        fields=fields, columns=columns, rows=[{key: None for key in columns}]
    )
    assert run(template, values, mode) == []


@pytest.mark.parametrize("mismatch", [False, True])
def test_unrounded_product_tolerance_boundary(mode, mismatch):
    # 0.0001 * -0.001 = -0.0000001; discrepancies .01 and .0100001.
    qty, price = ("0.01", "1") if not mismatch else ("0.0001", "-0.001")
    amount = "0" if not mismatch else "0.01"
    template, values = document(
        fields={}, rows=[{"qty": qty, "unit_price": price, "amount": amount}]
    )
    assert [i.code for i in run(template, values, mode)] == (
        ["LINE_AMOUNT_MISMATCH"] if mismatch else []
    )


@pytest.mark.parametrize(
    "total,expected", [("1200.01", []), ("1200.0101", ["TOTAL_MISMATCH"])]
)
def test_total_default_tolerance(mode, total, expected):
    template, values = document(fields={"subtotal": "1200", "total": total})
    assert [i.code for i in run(template, values, mode)] == expected


def test_custom_tolerance_and_local_context(mode):
    template, values = document(fields={"subtotal": "1200.01", "total": "1200.03"})
    template["rules"] = {"tolerance": "0.001"}
    with localcontext() as context:
        context.prec = 3
        assert [i.code for i in run(template, values, mode)] == [
            "SUBTOTAL_MISMATCH",
            "TOTAL_MISMATCH",
        ]
        assert context.prec == 3


def test_parenthesized_negatives_match_review():
    template, values = document(
        fields={"subtotal": "(1200.00)", "tax": "(84.00)", "total": "(1248.00)"},
        rows=[{"qty": "(2)", "unit_price": "600.00", "amount": "(1200.00)"}],
    )
    extracted_issues = run(template, values, "extraction")
    values["fields"] = {
        key: "-" + value[1:-1] for key, value in values["fields"].items()
    }
    values["tables"]["items"][0].update(qty="-2", amount="-1200.00")
    assert extracted_issues == run(template, values, "review")
    assert [i.code for i in extracted_issues] == ["TOTAL_MISMATCH"]


def test_empty_table_sum_and_multiple_tables(mode):
    template, values = document(fields={"subtotal": "1"}, rows=[])
    assert [i.code for i in run(template, values, mode)] == ["SUBTOTAL_MISMATCH"]
    template["tables"].append(
        {"key": "other", "columns": [{"key": "amount", "type": "decimal"}]}
    )
    values["tables"]["other"] = [{"amount": "1"}]
    assert [(i.code, i.target) for i in run(template, values, mode)] == [
        ("SUBTOTAL_MISMATCH", "items")
    ]


@pytest.mark.parametrize("operand", ["discount", "shipping", "tax"])
def test_ungrounded_optional_operand_is_unavailable(operand):
    template, _ = document(
        fields={"subtotal": "0", "total": "0", operand: None}, rows=[]
    )
    missing = {"missing": True}
    extracted = {
        "fields": {
            "subtotal": {"raw": "0", "span": "0", "box_ids": ["s"]},
            "total": {"raw": "0", "span": "0", "box_ids": ["t"]},
            operand: missing,
        },
        "tables": {"items": []},
    }
    snapshot = {
        "pages": [
            {"boxes": [{"id": key, "text": "0", "score": 1} for key in ("s", "t", "o")]}
        ]
    }
    assert check_extraction(template, snapshot, extracted)[1] == []
    extracted["fields"][operand] = {"raw": None, "span": "absent", "box_ids": ["o"]}
    issues = check_extraction(template, snapshot, extracted)[1]
    assert [(i.code, i.target) for i in issues] == [
        ("UNGROUNDED_VALUE", operand),
        ("RECONCILIATION_UNAVAILABLE", "total"),
    ]


@pytest.mark.parametrize(
    "subtotal,expected", [("1200.01", []), ("1200.0101", ["SUBTOTAL_MISMATCH"])]
)
def test_subtotal_tolerance(mode, subtotal, expected):
    template, values = document(fields={"subtotal": subtotal})
    assert [i.code for i in run(template, values, mode)] == expected
