"""Built-in hashtag presets for template creation (design section 5.1)."""

from copy import deepcopy

REQUIRED_FLAG = "#required"

PRESETS = (
    {
        "tag": "#vendor_name",
        "key": "vendor_name",
        "type": "text",
        "kind": "field",
        "description": "The supplier or vendor name issuing the document.",
    },
    {
        "tag": "#vendor_address",
        "key": "vendor_address",
        "type": "text",
        "kind": "field",
        "description": "The supplier or vendor mailing address.",
    },
    {
        "tag": "#vendor_tax_id",
        "key": "vendor_tax_id",
        "type": "text",
        "kind": "field",
        "description": "The supplier or vendor tax registration number.",
    },
    {
        "tag": "#vendor_contact",
        "key": "vendor_contact",
        "type": "text",
        "kind": "field",
        "description": "The supplier contact person, email, or phone number.",
    },
    {
        "tag": "#buyer_name",
        "key": "buyer_name",
        "type": "text",
        "kind": "field",
        "description": "The customer, buyer, or bill-to organization name.",
    },
    {
        "tag": "#buyer_address",
        "key": "buyer_address",
        "type": "text",
        "kind": "field",
        "description": "The customer, buyer, or bill-to address.",
    },
    {
        "tag": "#buyer_tax_id",
        "key": "buyer_tax_id",
        "type": "text",
        "kind": "field",
        "description": "The customer or buyer tax registration number.",
    },
    {
        "tag": "#ship_to_address",
        "key": "ship_to_address",
        "type": "text",
        "kind": "field",
        "description": "The delivery or ship-to address.",
    },
    {
        "tag": "#invoice_no",
        "key": "invoice_no",
        "type": "text",
        "kind": "field",
        "description": "The invoice number, not a purchase order or receipt number.",
    },
    {
        "tag": "#invoice_date",
        "key": "invoice_date",
        "type": "date",
        "kind": "field",
        "format": "DD/MM/YYYY",
        "description": "The invoice issue date.",
    },
    {
        "tag": "#due_date",
        "key": "due_date",
        "type": "date",
        "kind": "field",
        "format": "DD/MM/YYYY",
        "description": "The payment due date.",
    },
    {
        "tag": "#po_number",
        "key": "po_number",
        "type": "text",
        "kind": "field",
        "description": "The purchase order number referenced by the document.",
    },
    {
        "tag": "#po_date",
        "key": "po_date",
        "type": "date",
        "kind": "field",
        "format": "DD/MM/YYYY",
        "description": "The purchase order date.",
    },
    {
        "tag": "#quotation_no",
        "key": "quotation_no",
        "type": "text",
        "kind": "field",
        "description": "The quotation or quote reference number.",
    },
    {
        "tag": "#contract_no",
        "key": "contract_no",
        "type": "text",
        "kind": "field",
        "description": "The contract reference number.",
    },
    {
        "tag": "#delivery_note_no",
        "key": "delivery_note_no",
        "type": "text",
        "kind": "field",
        "description": "The delivery note reference number.",
    },
    {
        "tag": "#delivery_date",
        "key": "delivery_date",
        "type": "date",
        "kind": "field",
        "format": "DD/MM/YYYY",
        "description": "The delivery date for the goods or services.",
    },
    {
        "tag": "#receipt_no",
        "key": "receipt_no",
        "type": "text",
        "kind": "field",
        "description": "The receipt reference number.",
    },
    {
        "tag": "#project_code",
        "key": "project_code",
        "type": "text",
        "kind": "field",
        "description": "The project code or project reference.",
    },
    {
        "tag": "#cost_center",
        "key": "cost_center",
        "type": "text",
        "kind": "field",
        "description": "The cost center code or department reference.",
    },
    {
        "tag": "#payment_terms",
        "key": "payment_terms",
        "type": "text",
        "kind": "field",
        "description": "The payment terms, such as net days or payment method.",
    },
    {
        "tag": "#delivery_terms",
        "key": "delivery_terms",
        "type": "text",
        "kind": "field",
        "description": "The delivery or shipping terms stated on the document.",
    },
    {
        "tag": "#currency",
        "key": "currency",
        "type": "text",
        "kind": "field",
        "description": "The currency code or symbol used for amounts.",
    },
    {
        "tag": "#subtotal",
        "key": "subtotal",
        "type": "decimal",
        "kind": "field",
        "description": "The subtotal amount before taxes and final adjustments.",
    },
    {
        "tag": "#discount",
        "key": "discount",
        "type": "decimal",
        "kind": "field",
        "description": "The discount amount applied to the document.",
    },
    {
        "tag": "#tax_rate",
        "key": "tax_rate",
        "type": "decimal",
        "kind": "field",
        "description": "The tax percentage or rate applied.",
    },
    {
        "tag": "#tax_amount",
        "key": "tax_amount",
        "type": "decimal",
        "kind": "field",
        "description": "The tax amount charged on the document.",
    },
    {
        "tag": "#withholding_tax",
        "key": "withholding_tax",
        "type": "decimal",
        "kind": "field",
        "description": "The withholding tax amount deducted or applied.",
    },
    {
        "tag": "#shipping",
        "key": "shipping",
        "type": "decimal",
        "kind": "field",
        "description": "The shipping, freight, or delivery charge.",
    },
    {
        "tag": "#total",
        "key": "total",
        "type": "decimal",
        "kind": "field",
        "description": "The final amount payable including taxes and adjustments.",
    },
    {
        "tag": "#amount_in_words",
        "key": "amount_in_words",
        "type": "text",
        "kind": "field",
        "description": "The total amount written in words.",
    },
    {
        "tag": "#line_items",
        "key": "line_items",
        "type": "table",
        "kind": "table",
        "description": (
            "One row per purchased item; skip subtotal, tax, and total rows."
        ),
        "columns": [
            {"key": "item_code", "type": "text"},
            {"key": "description", "type": "text"},
            {"key": "qty", "type": "decimal"},
            {"key": "unit", "type": "text"},
            {"key": "unit_price", "type": "decimal"},
            {"key": "discount", "type": "decimal"},
            {"key": "amount", "type": "decimal"},
        ],
    },
)


def search_presets(prefix: str) -> list[dict]:
    """Return detached presets whose hashtag starts with ``prefix``."""
    normalized = prefix if prefix.startswith("#") else f"#{prefix}"
    normalized = normalized.lower()
    return [
        deepcopy(preset)
        for preset in PRESETS
        if preset["tag"].lower().startswith(normalized)
    ]
