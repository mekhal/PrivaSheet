"""Pure validation of extraction evidence and canonical review values."""

from dataclasses import dataclass
from difflib import SequenceMatcher

from privasheet.templates import key_labels
from privasheet.validate.parse import (
    is_canonical_date,
    is_canonical_decimal,
    parse_date,
    parse_decimal,
)

AI_UNCERTAIN = "AI_UNCERTAIN"
DUPLICATE_DOCUMENT = "DUPLICATE_DOCUMENT"
PROCESSING_TIMEOUT = "PROCESSING_TIMEOUT"


@dataclass
class Issue:
    """A failed check at a field, cell, table, or the template as a whole."""

    code: str
    target: str
    detail: str


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _parse(definition: dict, raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    if definition["type"] == "decimal":
        return parse_decimal(raw)
    if definition["type"] == "date":
        return parse_date(raw, definition["format"])
    return raw


def _canonical(definition: dict, value: object) -> bool:
    if definition["type"] == "decimal":
        return is_canonical_decimal(value)
    if definition["type"] == "date":
        return is_canonical_date(value)
    return isinstance(value, str)


def _values(template: dict, data: dict, check_value, issues: list[Issue]) -> dict:
    """Walk template-defined fields and cells, sharing required-table checks."""
    values = {"fields": {}, "tables": {}}
    for definition in template.get("fields", []):
        key = definition["key"]
        values["fields"][key] = check_value(
            definition,
            data.get("fields", {}).get(key),
            key,
            definition.get("required", False),
        )
    for table in template.get("tables", []):
        key = table["key"]
        rows = data.get("tables", {}).get(key, [])
        if table.get("required", False) and not rows:
            issues.append(Issue("REQUIRED_MISSING", key, "Required table has no rows."))
        values["tables"][key] = [
            {
                column["key"]: check_value(
                    column,
                    row.get(column["key"]),
                    f"{key}[{index}].{column['key']}",
                    column.get("required", True),
                )
                for column in table["columns"]
            }
            for index, row in enumerate(rows)
        ]
    return values


def check_extraction(
    template: dict, snapshot: dict, extracted: dict
) -> tuple[dict, list[Issue]]:
    """Return canonical ``{fields, tables}`` values and validation issues.

    Inputs are never modified. The template and evidence references are already
    schema-validated upstream (§5.3); this checks raw text and OCR quality.
    Missing and unparseable values both yield None, with distinct issue codes.
    """
    issues = []
    boxes = {box["id"]: box for page in snapshot["pages"] for box in page["boxes"]}
    labels = [_normalize(label) for label in key_labels(template)]
    texts = [_normalize(box["text"]) for box in boxes.values()]
    min_key_label_ratio = template.get("match", {}).get("min_key_label_ratio", 0.9)
    found = sum(
        any(
            label in text or SequenceMatcher(None, label, text).ratio() >= 0.8
            for text in texts
        )
        for label in labels
    )
    if labels and found / len(labels) < min_key_label_ratio:
        issues.append(
            Issue(
                "TEMPLATE_MISMATCH",
                "template",
                f"Found {found} of {len(labels)} key labels; "
                f"required ratio is {min_key_label_ratio}. "
                "Consider creating a new template.",
            )
        )

    used = {}

    def check_value(definition, evidence, target, required):
        if evidence is not None and evidence.get("uncertain") is True:
            issues.append(
                Issue(
                    AI_UNCERTAIN,
                    target,
                    evidence.get("reason") or "AI marked this value as uncertain.",
                )
            )
            return None

        if evidence is None or evidence.get("missing") is True:
            if required:
                issues.append(
                    Issue("REQUIRED_MISSING", target, "Required value is missing.")
                )
            return None

        for box_id in evidence["box_ids"]:
            reference = (box_id, evidence["span"])
            previous = used.setdefault(reference, target)
            if previous != target:
                issues.append(
                    Issue(
                        "DUPLICATE_BOX",
                        target,
                        f"Box {box_id} with the same span is also used by {previous}.",
                    )
                )
            if boxes[box_id]["score"] < 0.90:
                issues.append(
                    Issue(
                        "LOW_OCR_SCORE",
                        target,
                        f"Box {box_id} has OCR score {boxes[box_id]['score']} below 0.90.",
                    )
                )

        raw = evidence.get("raw")
        if raw is None:
            issues.append(
                Issue("UNGROUNDED_VALUE", target, "Span has no grounded raw text.")
            )
            return None
        value = _parse(definition, raw)
        if value is None:
            issues.append(
                Issue(
                    "PARSE_ERROR",
                    target,
                    f"Raw text is not a valid {definition['type']} value.",
                )
            )
        return value

    values = _values(template, extracted, check_value, issues)
    return values, issues


def check_review(template: dict, review: dict) -> list[Issue]:
    """Validate canonical review values without consulting OCR evidence.

    None means missing. Dates use ISO form, regardless of the source format in
    the template; decimals must already be plain decimal strings.
    """
    issues = []

    def check_value(definition, value, target, required):
        if value is None:
            if required:
                issues.append(
                    Issue("REQUIRED_MISSING", target, "Required value is missing.")
                )
        elif not _canonical(definition, value):
            issues.append(
                Issue(
                    "PARSE_ERROR",
                    target,
                    f"Review value is not a canonical {definition['type']} value.",
                )
            )
        return value

    _values(template, review, check_value, issues)
    return issues
