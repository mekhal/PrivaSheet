"""Ground and validate AI responses against template evidence requirements."""

from collections.abc import Iterable


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _box_map(snapshot: dict) -> dict[str, dict]:
    return {
        box["id"]: box
        for page in snapshot.get("pages", [])
        for box in page.get("boxes", [])
        if isinstance(box, dict) and "id" in box
    }


def _field_keys(template: dict) -> list[str]:
    return [field["key"] for field in template.get("fields", [])]


def _table_columns(template: dict) -> dict[str, list[str]]:
    return {
        table["key"]: [column["key"] for column in table.get("columns", [])]
        for table in template.get("tables", [])
    }


def _unknown_keys(keys: Iterable[str], allowed: set[str], target: str) -> list[str]:
    prefix = f"{target}." if target else ""
    return [f"{prefix}{key} is not allowed" for key in keys if key not in allowed]


def _validate_entry(entry: object, boxes: dict[str, dict], target: str) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{target} must be an object"]

    keys = set(entry)
    grounded = {"box_ids", "span"} <= keys
    missing = "missing" in keys
    uncertain = "uncertain" in keys
    variants = sum([grounded, missing, uncertain])
    if variants != 1:
        return [f"{target} must be grounded, missing, or uncertain"]

    errors = []
    if grounded:
        allowed = {"box_ids", "span"}
        errors.extend(_unknown_keys(entry, allowed, target))
        box_ids = entry.get("box_ids")
        if not isinstance(box_ids, list):
            errors.append(f"{target}.box_ids must be a list")
        elif not box_ids:
            errors.append(f"{target}.box_ids must be non-empty")
        else:
            seen = set()
            duplicate = False
            for index, box_id in enumerate(box_ids):
                if box_id in seen:
                    duplicate = True
                seen.add(box_id)
                if box_id not in boxes:
                    errors.append(
                        f"{target}.box_ids[{index}] does not exist in the snapshot"
                    )
            if duplicate:
                errors.append(f"{target}.box_ids must not contain duplicates")
        span = entry.get("span")
        if not isinstance(span, str):
            errors.append(f"{target}.span must be a string")
        elif not span:
            errors.append(f"{target}.span must be non-empty")
        return errors

    if missing:
        allowed = {"missing"}
        errors.extend(_unknown_keys(entry, allowed, target))
        if entry.get("missing") is not True:
            errors.append(f"{target}.missing must be true")
        return errors

    allowed = {"uncertain", "reason"}
    errors.extend(_unknown_keys(entry, allowed, target))
    if entry.get("uncertain") is not True:
        errors.append(f"{target}.uncertain must be true")
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason:
        errors.append(f"{target}.reason must be non-empty")
    elif len(reason) > 300:
        errors.append(f"{target}.reason must be at most 300 characters")
    return errors


def validate_response(template: dict, snapshot: dict, response: object) -> list[str]:
    """Return response-shape errors for template-defined fields and tables."""
    if not isinstance(response, dict):
        return ["response must be an object"]

    errors = _unknown_keys(response, {"fields", "tables"}, "")
    fields = response.get("fields")
    tables = response.get("tables")
    field_keys = _field_keys(template)
    table_columns = _table_columns(template)
    boxes = _box_map(snapshot)

    if fields is None:
        errors.append("fields is required")
    elif not isinstance(fields, dict):
        errors.append("fields must be an object")
    else:
        allowed = set(field_keys)
        for key in fields:
            if key not in allowed:
                errors.append(f"fields.{key} is not defined by the template")
        for key in field_keys:
            if key not in fields:
                errors.append(f"fields.{key} is required")
            else:
                errors.extend(_validate_entry(fields[key], boxes, f"fields.{key}"))

    if tables is None:
        errors.append("tables is required")
    elif not isinstance(tables, dict):
        errors.append("tables must be an object")
    else:
        allowed = set(table_columns)
        for key in tables:
            if key not in allowed:
                errors.append(f"tables.{key} is not defined by the template")
        for table_key, columns in table_columns.items():
            if table_key not in tables:
                errors.append(f"tables.{table_key} is required")
                continue
            rows = tables[table_key]
            if not isinstance(rows, list):
                errors.append(f"tables.{table_key} must be a list")
                continue
            allowed_columns = set(columns)
            for index, row in enumerate(rows):
                row_target = f"tables.{table_key}[{index}]"
                if not isinstance(row, dict):
                    errors.append(f"{row_target} must be an object")
                    continue
                for key in row:
                    if key not in allowed_columns:
                        errors.append(
                            f"{row_target}.{key} is not defined by the template"
                        )
                for column in columns:
                    target = f"{row_target}.{column}"
                    if column not in row:
                        errors.append(f"{target} is required")
                    else:
                        errors.extend(_validate_entry(row[column], boxes, target))

    return errors


def ground(snapshot: dict, box_ids: list[str], span: str) -> str | None:
    """Return the normalized raw span from cited box text, or None if absent."""
    boxes = _box_map(snapshot)
    texts = []
    for box_id in box_ids:
        box = boxes.get(box_id)
        if box is None:
            return None
        texts.append(str(box.get("text", "")))
    source = _collapse(" ".join(texts))
    normalized_span = _collapse(span)
    if not normalized_span:
        return None
    index = source.find(normalized_span)
    if index == -1:
        return None
    return source[index : index + len(normalized_span)]


def _apply_entry(snapshot: dict, entry: dict) -> dict:
    if entry.get("missing") is True:
        return {"missing": True}
    if entry.get("uncertain") is True:
        return {"uncertain": True, "reason": entry["reason"]}
    box_ids = list(entry["box_ids"])
    span = entry["span"]
    return {"box_ids": box_ids, "span": span, "raw": ground(snapshot, box_ids, span)}


def apply(template: dict, snapshot: dict, response: dict) -> dict:
    """Convert a validated response into the extracted structure from section 4.3."""
    return {
        "fields": {
            key: _apply_entry(snapshot, response["fields"][key])
            for key in _field_keys(template)
        },
        "tables": {
            table_key: [
                {
                    column: _apply_entry(snapshot, row[column])
                    for column in table_columns
                }
                for row in response["tables"][table_key]
            ]
            for table_key, table_columns in _table_columns(template).items()
        },
    }
