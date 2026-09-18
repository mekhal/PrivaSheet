"""Build batch JSONL from canonical results (design sections 4.3–4.5)."""

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path


class ExportNotAllowed(ValueError):
    """A selected document is not currently exportable."""


EXPORTABLE_STATUSES = {"passed", "reviewed"}


def build_jsonl(
    manifest: dict,
    results: dict[str, dict],
    template: dict,
    selected_document_ids: Iterable[str],
) -> str:
    """Return export lines in manifest order without modifying the inputs.

    The caller must read the manifest and results in one read transaction so
    eligibility and exported values describe the same state.
    """
    selected = set(selected_document_ids)
    found = set()
    for document in manifest["documents"]:
        if document["document_id"] not in selected:
            continue
        found.add(document["document_id"])
        status = results.get(document["result_id"], {}).get("status")
        if status not in EXPORTABLE_STATUSES:
            raise ExportNotAllowed(
                f"Document {document['document_id']} is {status}; export is not allowed"
            )
    missing = selected - found
    if missing:
        document_id = min(missing)
        raise ExportNotAllowed(f"Document {document_id} is not in the batch")

    lines = []
    for document in manifest["documents"]:
        if document["document_id"] not in selected:
            continue
        result = results[document["result_id"]]
        review = result.get("review")
        values = review if review is not None else result["extracted"]

        def canonical(items, key, reviewed=review is not None):
            if reviewed:
                return items.get(key)
            return items.get(key, {}).get("value")

        fields = {
            field["key"]: canonical(values.get("fields", {}), field["key"])
            for field in template.get("fields", [])
        }
        tables = {
            table["key"]: [
                {
                    column["key"]: canonical(row, column["key"])
                    for column in table["columns"]
                }
                for row in values.get("tables", {}).get(table["key"], [])
            ]
            for table in template.get("tables", [])
        }
        line = {
            "schema_version": 1,
            "batch_id": manifest["batch_id"],
            "document_id": document["document_id"],
            "source_file": document["source_file"],
            "template": manifest["template"],
            "human_reviewed": result["status"] == "reviewed",
            "fields": fields,
            "tables": tables,
        }
        lines.append(
            json.dumps(line, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            + "\n"
        )
    return "".join(lines)


def selectable_documents(manifest: dict, results: dict[str, dict]) -> list[str]:
    """Return document IDs that can currently be exported, in manifest order."""
    return [
        document["document_id"]
        for document in manifest["documents"]
        if results[document["result_id"]]["status"] in EXPORTABLE_STATUSES
    ]


def export_filename(batch_id: str, now) -> str:
    """Return the JSONL export filename with a filename-safe UTC timestamp."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    timestamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{batch_id}-{timestamp}.jsonl"


def failed_documents(manifest: dict, results: dict[str, dict]) -> list[str]:
    """Return failed source filenames in manifest order for the UI."""
    return [
        document["source_file"]
        for document in manifest["documents"]
        if results[document["result_id"]]["status"] == "failed"
    ]


def write_atomic(path: str | os.PathLike[str], text: str) -> None:
    """Flush and sync UTF-8 content before atomically replacing the target."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
