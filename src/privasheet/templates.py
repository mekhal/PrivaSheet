"""Validation and read helpers for the template JSON model (design §4.1)."""

import re
from copy import deepcopy
from datetime import date, datetime

_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
_DATE_FORMAT = re.compile(r"(?:YYYY|MMM|DD|MM|YY|[^\w])+")
_VERSION_LABEL = re.compile(r"1\.(0|[1-9][0-9]*)\.([0-9]{8})")
_REGIONS = (
    "top-left",
    "top",
    "top-right",
    "left",
    "center",
    "right",
    "bottom-left",
    "bottom",
    "bottom-right",
)


class TemplateError(ValueError):
    """A template violates one or more model rules."""


def validate_template(doc: dict) -> list[str]:
    """Return all model errors without modifying the input document.

    Omitted column ``required`` values mean true; :func:`table` exposes that
    default to consumers. Optional tables and rules may be omitted.
    """
    errors = []
    if not isinstance(doc, dict):
        return ["template must be an object"]

    def array(value, path):
        if not isinstance(value, list):
            errors.append(f"{path} must be an array")
            return []
        return value

    def obj(value, path):
        if not isinstance(value, dict):
            errors.append(f"{path} must be an object")
            return {}
        return value

    def check_key(item, path, seen):
        key = item.get("key")
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            errors.append(f"{path}.key must match ^[a-z][a-z0-9_]{{0,39}}$")
        elif key in seen:
            errors.append(f"{path}.key duplicates key {key!r}")
        else:
            seen.add(key)

    def check_common(item, path):
        for flag in ("required", "key_label"):
            if flag in item and not isinstance(item[flag], bool):
                errors.append(f"{path}.{flag} must be a boolean")
        hint = obj(item.get("hint", {}), f"{path}.hint")
        if "region" in hint and hint["region"] not in _REGIONS:
            errors.append(f"{path}.hint.region must be one of {', '.join(_REGIONS)}")
        return hint

    def check_description(item, path):
        description = item.get("description")
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 500
        ):
            errors.append(
                f"{path}.description must be non-empty and at most 500 characters"
            )

    def check_type(item, path):
        kind = item.get("type")
        if kind not in ("text", "date", "decimal"):
            errors.append(f"{path}.type must be text, date or decimal")
        if kind == "date":
            fmt = item.get("format")
            if (
                not isinstance(fmt, str)
                or not _DATE_FORMAT.fullmatch(fmt)
                or not any(token in fmt for token in ("DD", "MM", "YY"))
            ):
                errors.append(
                    f"{path}.format is required for date and must contain only "
                    "DD, MM, MMM, YYYY, YY tokens and separators"
                )

    seen = set()
    has_key_label = False
    for index, value in enumerate(array(doc.get("fields", []), "fields")):
        path = f"fields[{index}]"
        item = obj(value, path)
        check_key(item, path, seen)
        check_description(item, path)
        check_type(item, path)
        hint = check_common(item, path)
        if item.get("key_label") is True:
            has_key_label = True
            labels = hint.get("labels")
            if (
                not isinstance(labels, list)
                or not labels
                or not isinstance(labels[0], str)
                or not labels[0].strip()
            ):
                errors.append(f"{path} key label requires a non-empty first hint label")
    if not has_key_label:
        errors.append("template needs at least one key label (key_label: true)")

    tables = array(doc.get("tables", []), "tables")
    for index, value in enumerate(tables):
        path = f"tables[{index}]"
        item = obj(value, path)
        check_key(item, path, seen)
        check_description(item, path)
        check_common(item, path)
        column_keys = set()
        for col_index, value in enumerate(
            array(item.get("columns", []), f"{path}.columns")
        ):
            col_path = f"{path}.columns[{col_index}]"
            column = obj(value, col_path)
            check_key(column, col_path, column_keys)
            check_type(column, col_path)
            check_common(column, col_path)

    match = obj(doc.get("match", {}), "match")
    if "min_key_label_ratio" in match:
        ratio = match["min_key_label_ratio"]
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not 0 < ratio <= 1
        ):
            errors.append("match.min_key_label_ratio must be a number in (0, 1]")
    return errors


def version_label(saved_on: date, earlier_saves_that_day: int) -> str:
    """Return a user-facing template version label for a local save date."""
    if not isinstance(saved_on, date):
        raise TypeError("saved_on must be a date")
    if isinstance(earlier_saves_that_day, bool) or not isinstance(
        earlier_saves_that_day, int
    ):
        raise TypeError("earlier_saves_that_day must be an integer")
    if earlier_saves_that_day < 0:
        raise ValueError("earlier_saves_that_day must be non-negative")
    return f"1.{earlier_saves_that_day}.{saved_on:%Y%m%d}"


def valid_version_label(label: str) -> bool:
    """Return true when ``label`` has the approved ``1.<n>.<YYYYMMDD>`` shape."""
    if not isinstance(label, str):
        return False
    match = _VERSION_LABEL.fullmatch(label)
    if not match:
        return False
    try:
        datetime.strptime(match.group(2), "%Y%m%d")
    except ValueError:
        return False
    return True


def ensure_valid(doc: dict) -> None:
    """Raise TemplateError containing every validation error, if any."""
    errors = validate_template(doc)
    if errors:
        raise TemplateError("Invalid template:\n" + "\n".join(errors))


def field_keys(doc: dict) -> list[str]:
    """Return field keys in template order, for a validated template."""
    return [field["key"] for field in doc.get("fields", [])]


def table(doc: dict, key: str) -> dict | None:
    """Return a detached table with column defaults, or None if absent."""
    for definition in doc.get("tables", []):
        if definition["key"] == key:
            result = deepcopy(definition)
            for column in result["columns"]:
                column.setdefault("required", True)
            return result
    return None


def key_labels(doc: dict) -> list[str]:
    """Return the first label of each key-label field in template order."""
    return [
        field["hint"]["labels"][0]
        for field in doc.get("fields", [])
        if field.get("key_label") is True
    ]
