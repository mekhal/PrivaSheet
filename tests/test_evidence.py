from copy import deepcopy

import pytest

from privasheet.evidence import apply, ground, validate_response


@pytest.fixture
def document():
    template = {
        "fields": [
            {"key": "invoice_no", "type": "text", "required": True},
            {"key": "total", "type": "decimal", "required": True},
            {"key": "po_no", "type": "text", "required": False},
        ],
        "tables": [
            {
                "key": "line_items",
                "columns": [
                    {"key": "description", "type": "text"},
                    {"key": "qty", "type": "decimal"},
                ],
            }
        ],
    }
    boxes = [
        {"id": "p1-b0000", "text": "Invoice   No: INV-0042"},
        {"id": "p1-b0001", "text": "Grand Total:\n1,284.00"},
        {"id": "p1-b0002", "text": "Paper A4"},
        {"id": "p1-b0003", "text": "Qty 2"},
    ]
    snapshot = {"pages": [{"page": 1, "boxes": boxes}]}
    response = {
        "fields": {
            "invoice_no": {"box_ids": ["p1-b0000"], "span": "INV-0042"},
            "total": {"box_ids": ["p1-b0001"], "span": "1,284.00"},
            "po_no": {"missing": True},
        },
        "tables": {
            "line_items": [
                {
                    "description": {"box_ids": ["p1-b0002"], "span": "Paper A4"},
                    "qty": {"uncertain": True, "reason": "quantity mark is faint"},
                }
            ]
        },
    }
    return template, snapshot, response


def test_valid_response_has_no_errors(document):
    template, snapshot, response = document
    before = deepcopy(document)

    assert validate_response(template, snapshot, response) == []
    assert document == before


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda response: response["fields"].pop("total"),
            "fields.total is required",
        ),
        (
            lambda response: response["fields"].__setitem__("extra", {"missing": True}),
            "fields.extra is not defined by the template",
        ),
        (
            lambda response: response.pop("fields"),
            "fields is required",
        ),
        (
            lambda response: response["tables"].pop("line_items"),
            "tables.line_items is required",
        ),
        (
            lambda response: response["tables"].__setitem__("extra", []),
            "tables.extra is not defined by the template",
        ),
        (
            lambda response: response["tables"]["line_items"][0].pop("qty"),
            "tables.line_items[0].qty is required",
        ),
        (
            lambda response: response["tables"]["line_items"][0].__setitem__(
                "extra", {"missing": True}
            ),
            "tables.line_items[0].extra is not defined by the template",
        ),
    ],
)
def test_validate_response_requires_template_keys(document, mutate, expected):
    template, snapshot, response = deepcopy(document)
    mutate(response)

    assert expected in validate_response(template, snapshot, response)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (None, "fields.invoice_no must be an object"),
        ({}, "fields.invoice_no must be grounded, missing, or uncertain"),
        (
            {"box_ids": [], "span": "INV-0042"},
            "fields.invoice_no.box_ids must be non-empty",
        ),
        (
            {"box_ids": ["p1-b0000", "p1-b0000"], "span": "INV-0042"},
            "fields.invoice_no.box_ids must not contain duplicates",
        ),
        (
            {"box_ids": ["missing"], "span": "INV-0042"},
            "fields.invoice_no.box_ids[0] does not exist in the snapshot",
        ),
        (
            {"box_ids": ["p1-b0000"], "span": 42},
            "fields.invoice_no.span must be a string",
        ),
        (
            {"box_ids": ["p1-b0000"], "span": "INV-0042", "raw": "INV-0042"},
            "fields.invoice_no.raw is not allowed",
        ),
        (
            {"missing": False},
            "fields.invoice_no.missing must be true",
        ),
        (
            {"missing": True, "reason": "not needed"},
            "fields.invoice_no.reason is not allowed",
        ),
        (
            {"uncertain": True, "reason": ""},
            "fields.invoice_no.reason must be non-empty",
        ),
        (
            {"uncertain": True, "reason": "x" * 301},
            "fields.invoice_no.reason must be at most 300 characters",
        ),
        (
            {"uncertain": False, "reason": "faint"},
            "fields.invoice_no.uncertain must be true",
        ),
        (
            {"uncertain": True, "reason": "faint", "box_ids": ["p1-b0000"]},
            "fields.invoice_no.box_ids is not allowed",
        ),
    ],
)
def test_validate_response_entry_rules(document, entry, expected):
    template, snapshot, response = deepcopy(document)
    response["fields"]["invoice_no"] = entry

    assert expected in validate_response(template, snapshot, response)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ([], "response must be an object"),
        ({"fields": {}, "tables": {}, "extra": True}, "extra is not allowed"),
        (
            {"fields": [], "tables": {}},
            "fields must be an object",
        ),
        (
            {"fields": {}, "tables": []},
            "tables must be an object",
        ),
        (
            {"fields": {}, "tables": {"line_items": {}}},
            "tables.line_items must be a list",
        ),
        (
            {"fields": {}, "tables": {"line_items": [None]}},
            "tables.line_items[0] must be an object",
        ),
    ],
)
def test_validate_response_container_rules(document, response, expected):
    template, snapshot, _ = document

    assert expected in validate_response(template, snapshot, response)


def test_ground_uses_collapsed_case_sensitive_first_occurrence():
    snapshot = {
        "pages": [
            {
                "boxes": [
                    {"id": "a", "text": "Alpha\nBeta  beta"},
                    {"id": "b", "text": "Gamma"},
                ]
            }
        ]
    }

    assert ground(snapshot, ["a", "b"], "Beta   beta Gamma") == "Beta beta Gamma"
    assert ground(snapshot, ["a"], "beta") == "beta"
    assert ground(snapshot, ["a"], "ALPHA") is None
    assert ground(snapshot, ["missing"], "Alpha") is None


def test_apply_returns_extracted_structure_with_raw_text(document):
    template, snapshot, response = document
    before = deepcopy(document)

    assert apply(template, snapshot, response) == {
        "fields": {
            "invoice_no": {
                "box_ids": ["p1-b0000"],
                "span": "INV-0042",
                "raw": "INV-0042",
            },
            "total": {
                "box_ids": ["p1-b0001"],
                "span": "1,284.00",
                "raw": "1,284.00",
            },
            "po_no": {"missing": True},
        },
        "tables": {
            "line_items": [
                {
                    "description": {
                        "box_ids": ["p1-b0002"],
                        "span": "Paper A4",
                        "raw": "Paper A4",
                    },
                    "qty": {"uncertain": True, "reason": "quantity mark is faint"},
                }
            ]
        },
    }
    assert document == before


def test_apply_keeps_ungrounded_values_with_none_raw(document):
    template, snapshot, response = deepcopy(document)
    response["fields"]["invoice_no"]["span"] = "INV-9999"

    assert apply(template, snapshot, response)["fields"]["invoice_no"] == {
        "box_ids": ["p1-b0000"],
        "span": "INV-9999",
        "raw": None,
    }
