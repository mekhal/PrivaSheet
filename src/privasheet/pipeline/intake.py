"""Batch intake orchestration for uploaded files."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from privasheet.ingest.checks import IngestError, Limits, copy_limited, detect_type
from privasheet.ingest.checks import inspect as inspect_upload
from privasheet.store import StoreError, repo

from .ids import new_id as default_new_id


@dataclass(frozen=True)
class Upload:
    source_name: str
    stream: BinaryIO


@dataclass(frozen=True)
class RejectedUpload:
    source_file: str
    code: str
    detail: str


@dataclass(frozen=True)
class IntakeResult:
    batch_id: str | None
    accepted: list[tuple[str, str, str]] = field(default_factory=list)
    rejected: list[RejectedUpload] = field(default_factory=list)


Now = Callable[[], str]
NewId = Callable[[str], str]
DEFAULT_LIMITS = Limits()

_EXTENSIONS = {
    "pdf": "pdf",
    "jpeg": "jpg",
    "png": "png",
    "tiff": "tif",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def create_batch(
    conn,
    datadir: str | os.PathLike[str],
    template_id: str,
    version: int,
    uploads: list[Upload],
    limits: Limits = DEFAULT_LIMITS,
    now: Now = _now_iso,
    new_id: NewId = default_new_id,
) -> IntakeResult:
    """Create a batch from valid uploads and queue extraction results."""

    if repo.get_template(conn, template_id, version) is None:
        raise ValueError(f"template {template_id} version {version} does not exist")

    batch_id = new_id("bat")
    created_at = now()
    intake = _prepare_uploads(
        datadir, batch_id, template_id, version, uploads, limits, created_at, new_id
    )
    if not intake["documents"]:
        return IntakeResult(batch_id=None, rejected=intake["rejected"])

    try:
        manifest = {
            "batch_id": batch_id,
            "created_at": created_at,
            "template": {"id": template_id, "version": version},
            "documents": _manifest_entries(intake["documents"], intake["results"]),
        }
        repo.create_batch(conn, manifest, intake["documents"], intake["results"])
    except BaseException:
        _remove_paths(datadir, intake["paths"])
        raise

    return IntakeResult(
        batch_id=batch_id,
        accepted=intake["accepted"],
        rejected=intake["rejected"],
    )


def add_files(
    conn,
    datadir: str | os.PathLike[str],
    batch_id: str,
    uploads: list[Upload],
    limits: Limits = DEFAULT_LIMITS,
    now: Now = _now_iso,
    new_id: NewId = default_new_id,
) -> IntakeResult:
    """Append valid uploads to an existing batch and queue extraction results."""

    manifest = repo.get_batch(conn, batch_id)
    if manifest is None:
        raise StoreError("batch not found")
    template = manifest["template"]
    if repo.get_template(conn, template["id"], template["version"]) is None:
        raise ValueError(
            f"template {template['id']} version {template['version']} does not exist"
        )

    created_at = now()
    intake = _prepare_uploads(
        datadir,
        batch_id,
        template["id"],
        template["version"],
        uploads,
        limits,
        created_at,
        new_id,
    )
    if not intake["documents"]:
        return IntakeResult(batch_id=batch_id, rejected=intake["rejected"])

    try:
        repo.add_documents_to_batch(
            conn, batch_id, intake["documents"], intake["results"]
        )
    except BaseException:
        _remove_paths(datadir, intake["paths"])
        raise

    return IntakeResult(
        batch_id=batch_id,
        accepted=intake["accepted"],
        rejected=intake["rejected"],
    )


def remove_file(
    conn,
    datadir: str | os.PathLike[str],
    batch_id: str,
    document_id: str,
) -> None:
    """Remove one queued document row, then delete its file after commit."""

    path = repo.remove_queued_document(conn, batch_id, document_id)
    if path:
        (Path(datadir) / path).unlink(missing_ok=True)


def _prepare_uploads(
    datadir: str | os.PathLike[str],
    batch_id: str,
    template_id: str,
    version: int,
    uploads: list[Upload],
    limits: Limits,
    updated_at: str,
    new_id: NewId,
) -> dict:
    accepted: list[tuple[str, str, str]] = []
    rejected: list[RejectedUpload] = []
    documents: list[dict] = []
    results: list[dict] = []
    saved_paths: list[str] = []

    process_dir = Path(datadir) / "process"
    process_dir.mkdir(parents=True, exist_ok=True)

    for upload in uploads:
        document_id = new_id("doc")
        result_id = new_id("res")
        temp_path = process_dir / f"{document_id}.upload"
        final_path: Path | None = None
        try:
            copy_limited(upload.stream, temp_path, limits.max_bytes)
            first_bytes = _read_prefix(temp_path)
            kind = detect_type(first_bytes)
            if kind is None:
                raise IngestError(
                    "UNSUPPORTED_TYPE",
                    "Unsupported upload type; expected PDF, JPEG, PNG, or TIFF.",
                )
            final_path = process_dir / f"{document_id}.{_EXTENSIONS[kind]}"
            temp_path.replace(final_path)
            inspect_upload(final_path, kind, limits)
            sha256 = _sha256_file(final_path)
            _fsync_file(final_path)

            relative_path = f"process/{final_path.name}"
            documents.append(
                {
                    "document_id": document_id,
                    "sha256": sha256,
                    "source_file": upload.source_name,
                    "path": relative_path,
                    "snapshot_id": None,
                }
            )
            results.append(
                {
                    "result_id": result_id,
                    "batch_id": batch_id,
                    "document_id": document_id,
                    "source_file": upload.source_name,
                    "snapshot_id": None,
                    "template": {"id": template_id, "version": version},
                    "llm": {"model": None, "prompt_version": 1},
                    "status": "queued",
                    "extracted": None,
                    "issues": [],
                    "review": None,
                    "error": None,
                    "revision": 0,
                    "job": 1,
                    "updated_at": updated_at,
                }
            )
            accepted.append((document_id, result_id, upload.source_name))
            saved_paths.append(relative_path)
        except IngestError as exc:
            temp_path.unlink(missing_ok=True)
            if final_path is not None:
                final_path.unlink(missing_ok=True)
            rejected.append(RejectedUpload(upload.source_name, exc.code, exc.detail))

    return {
        "accepted": accepted,
        "rejected": rejected,
        "documents": documents,
        "results": results,
        "paths": saved_paths,
    }


def _manifest_entries(documents: list[dict], results: list[dict]) -> list[dict]:
    by_document_id = {result["document_id"]: result for result in results}
    return [
        {
            "document_id": document["document_id"],
            "result_id": by_document_id[document["document_id"]]["result_id"],
            "source_file": document["source_file"],
        }
        for document in documents
    ]


def _read_prefix(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(16)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _remove_paths(datadir: str | os.PathLike[str], paths: list[str]) -> None:
    root = Path(datadir)
    for path in paths:
        (root / path).unlink(missing_ok=True)
