"""Template rules from design section 4.1."""

from copy import deepcopy

import pytest

from privasheet.templates import (
    TemplateError,
    ensure_valid,
    field_keys,
    key_labels,
    table,
    validate_template,
)


@pytest.fixture
def doc():
    return {
        "template_id": "abc-layout-1",
        "version": 2,
        "name": "ABC Co. – Layout 1",
        "created_at": "2026-09-16T10:00:00Z",
        "sample_document_id": "doc_sample",
        "sample_snapshot_id": "sha256:sample",
        "fields": [
            {
                "key": "invoice_no",
                "type": "text",
                "required": True,
                "key_label": True,
                "hint": {
                    "labels": ["Invoice No"],
                    "region": "top-right",
                    "example": "INV-0042",
                },
            },
            {
                "key": "date",
                "type": "date",
                "required": True,
                "key_label": False,
                "format": "DD/MM/YYYY",
                "hint": {
                    "labels": ["Date"],
                    "region": "top-right",
                    "example": "01/09/2026",
                },
            },
            {
                "key": "total",
                "type": "decimal",
                "required": True,
                "key_label": True,
                "hint": {
                    "labels": ["Grand Total"],
                    "region": "bottom-right",
                    "example": "1,284.00",
                },
            },
        ],
        "tables": [
            {
                "key": "line_items",
                "required": True,
                "columns": [
                    {"key": "description", "type": "text"},
                    {"key": "qty", "type": "decimal"},
                    {"key": "unit_price", "type": "decimal"},
                    {"key": "amount", "type": "decimal"},
                ],
                "hint": {
                    "header_labels": ["Description", "Qty", "Unit Price", "Amount"],
                    "end_labels": ["Subtotal"],
                },
            }
        ],
        "rules": {"tolerance": "0.01"},
        "match": {"min_key_label_ratio": 0.6},
    }


def test_example_and_helpers(doc):
    original = deepcopy(doc)
    assert validate_template(doc) == []
    assert ensure_valid(doc) is None
    assert field_keys(doc) == ["invoice_no", "date", "total"]
    assert key_labels(doc) == ["Invoice No", "Grand Total"]
    result = table(doc, "line_items")
    assert all(c["required"] is True for c in result["columns"])
    assert table(doc, "missing") is None
    assert doc == original
    doc["tables"][0]["columns"][0]["required"] = False
    assert table(doc, "line_items")["columns"][0]["required"] is False


@pytest.mark.parametrize("scope", ["field", "table", "column"])
@pytest.mark.parametrize("key", ["", "A", "1a", "a-b", "a" * 41, "a\n", None, []])
def test_invalid_keys(doc, scope, key):
    item = {
        "field": doc["fields"][0],
        "table": doc["tables"][0],
        "column": doc["tables"][0]["columns"][0],
    }[scope]
    item["key"] = key
    assert any("key" in e for e in validate_template(doc))


@pytest.mark.parametrize("scope", ["fields", "shared", "columns"])
def test_duplicate_keys(doc, scope):
    if scope == "fields":
        doc["fields"].append(deepcopy(doc["fields"][0]))
    elif scope == "shared":
        doc["tables"][0]["key"] = "invoice_no"
    else:
        doc["tables"][0]["columns"].append({"key": "qty", "type": "decimal"})
    assert any("duplicate" in e.lower() for e in validate_template(doc))


@pytest.mark.parametrize("column", [False, True])
@pytest.mark.parametrize("kind", ["integer", None, []])
def test_invalid_types(doc, column, kind):
    item = doc["tables"][0]["columns"][0] if column else doc["fields"][0]
    item["type"] = kind
    assert any("type" in e for e in validate_template(doc))


@pytest.mark.parametrize("column", [False, True])
@pytest.mark.parametrize(
    "fmt", [None, "", "dd/mm/yyyy", "DD/MM/YYY", "DD day MM", "123", 42]
)
def test_invalid_date_format(doc, column, fmt):
    item = doc["tables"][0]["columns"][0] if column else doc["fields"][1]
    item["type"] = "date"
    if fmt is None:
        item.pop("format", None)
    else:
        item["format"] = fmt
    assert any("format" in e for e in validate_template(doc))


@pytest.mark.parametrize(
    "fmt", ["DD/MM/YYYY", "DD MMM YY", "YYYY-MM-DD", "DD.MM.YYYY", "DDMMYYYY"]
)
def test_date_tokens(doc, fmt):
    doc["fields"][1]["format"] = fmt
    assert validate_template(doc) == []


@pytest.mark.parametrize(
    "region",
    [
        "top-left",
        "top",
        "top-right",
        "left",
        "center",
        "right",
        "bottom-left",
        "bottom",
        "bottom-right",
    ],
)
def test_regions(doc, region):
    doc["fields"][0]["hint"]["region"] = region
    assert validate_template(doc) == []


def test_invalid_region(doc):
    doc["fields"][0]["hint"]["region"] = "middle"
    assert any("region" in e for e in validate_template(doc))


def test_no_key_label(doc):
    for field in doc["fields"]:
        field["key_label"] = False
    assert any("key label" in e for e in validate_template(doc))


@pytest.mark.parametrize("labels", [[], [""], None])
def test_key_label_needs_first_label(doc, labels):
    doc["fields"][0]["hint"]["labels"] = labels
    assert any("label" in e for e in validate_template(doc))


@pytest.mark.parametrize(
    "key",
    ["subtotal", "discount", "shipping", "tax", "total", "qty", "unit_price", "amount"],
)
def test_reserved_decimal_keys(doc, key):
    items = (
        doc["tables"][0]["columns"]
        if key in {"qty", "unit_price", "amount"}
        else doc["fields"]
    )
    items[:] = [i for i in items if i["key"] != key]
    items.append({"key": key, "type": "text"})
    assert any("decimal" in e for e in validate_template(doc))


def test_at_most_one_table(doc):
    other = deepcopy(doc["tables"][0])
    other["key"] = "other"
    doc["tables"].append(other)
    assert any("at most one table" in e for e in validate_template(doc))


@pytest.mark.parametrize("value", [0.01, None, "NaN", "Infinity", "1e-2", "oops", ""])
def test_tolerance(doc, value):
    doc["rules"]["tolerance"] = value
    assert any("tolerance" in e for e in validate_template(doc))


@pytest.mark.parametrize(
    "value", [0, -0.1, 1.1, True, "0.6", None, float("nan"), float("inf")]
)
def test_ratio(doc, value):
    doc["match"]["min_key_label_ratio"] = value
    assert any("min_key_label_ratio" in e for e in validate_template(doc))


def test_boundaries_and_optional_sections(doc):
    doc["match"]["min_key_label_ratio"] = 1
    doc["fields"][0]["key"] = "a" * 40
    doc["tables"][0]["columns"][0]["key"] = "invoice_no"
    assert validate_template(doc) == []
    del doc["tables"]
    del doc["rules"]
    assert validate_template(doc) == []


@pytest.mark.parametrize(
    "path,value", [("fields", None), ("tables", {}), ("rules", []), ("match", None)]
)
def test_malformed_sections(doc, path, value):
    doc[path] = value
    assert validate_template(doc)


def test_boolean_required(doc):
    doc["tables"][0]["columns"][0]["required"] = "true"
    assert any("required" in e for e in validate_template(doc))


def test_all_errors_raised(doc):
    doc["fields"][0]["key"] = "BAD"
    doc["rules"]["tolerance"] = "bad"
    errors = validate_template(doc)
    assert len(errors) >= 2
    with pytest.raises(TemplateError) as exc:
        ensure_valid(doc)
    assert isinstance(exc.value, ValueError)
    assert all(error in str(exc.value) for error in errors)


@pytest.mark.parametrize("fmt", ["DDéMMYYYY", "DD年MM月YYYY", "---"])
def test_date_format_rejects_literal_letters_and_separator_only(doc, fmt):
    doc["fields"][1]["format"] = fmt
    assert any("format" in e for e in validate_template(doc))


@pytest.mark.parametrize("value", [None, [], "template"])
def test_non_object_document(value):
    assert validate_template(value)


@pytest.mark.parametrize("scope", ["fields", "tables", "columns"])
def test_non_object_entries(doc, scope):
    items = doc["tables"][0]["columns"] if scope == "columns" else doc[scope]
    items.append(None)
    assert validate_template(doc)
