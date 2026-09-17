"""Hashtag presets from design section 5.1."""

from privasheet.presets import PRESETS, REQUIRED_FLAG, search_presets
from privasheet.templates import validate_template

EXPECTED_TAGS = {
    "#vendor_name",
    "#vendor_address",
    "#vendor_tax_id",
    "#vendor_contact",
    "#buyer_name",
    "#buyer_address",
    "#buyer_tax_id",
    "#ship_to_address",
    "#invoice_no",
    "#invoice_date",
    "#due_date",
    "#po_number",
    "#po_date",
    "#quotation_no",
    "#contract_no",
    "#delivery_note_no",
    "#delivery_date",
    "#receipt_no",
    "#project_code",
    "#cost_center",
    "#payment_terms",
    "#delivery_terms",
    "#currency",
    "#subtotal",
    "#discount",
    "#tax_rate",
    "#tax_amount",
    "#withholding_tax",
    "#shipping",
    "#total",
    "#amount_in_words",
    "#line_items",
}


def test_all_spec_presets_present_with_required_flag():
    assert {preset["tag"] for preset in PRESETS} == EXPECTED_TAGS
    assert REQUIRED_FLAG == "#required"


def test_search_presets_by_prefix():
    assert [preset["tag"] for preset in search_presets("#invoice")] == [
        "#invoice_no",
        "#invoice_date",
    ]
    assert [preset["tag"] for preset in search_presets("tax")] == [
        "#tax_rate",
        "#tax_amount",
    ]
    assert search_presets("#missing") == []


def test_search_presets_returns_detached_results():
    result = search_presets("#total")
    result[0]["description"] = "changed"
    assert search_presets("#total")[0]["description"] != "changed"


def test_every_preset_can_make_valid_definition():
    fields = []
    tables = []
    for preset in PRESETS:
        definition = {
            "key": preset["key"],
            "type": preset["type"],
            "required": False,
            "key_label": not fields,
            "description": preset["description"],
            "hint": {"labels": [preset["key"].replace("_", " ").title()]},
        }
        if definition["type"] == "date":
            definition["format"] = preset["format"]
        if preset["kind"] == "table":
            definition.pop("type")
            definition.pop("key_label")
            definition["columns"] = preset["columns"]
            tables.append(definition)
        else:
            fields.append(definition)

    errors = validate_template(
        {
            "fields": fields,
            "tables": tables,
            "match": {"min_key_label_ratio": 0.9},
        }
    )
    assert errors == []
