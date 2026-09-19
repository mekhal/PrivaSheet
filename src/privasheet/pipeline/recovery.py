"""Startup recovery for interrupted pipeline work."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from privasheet.store import repo as store_repo

from .datadir import DataDir, DataDirError

_COUNTS = ("reset_results", "corrected_paths", "removed_files", "removed_dirs")


def recover(conn: sqlite3.Connection, datadir: DataDir, now: str) -> dict[str, int]:
    """Repair interrupted work and prune unreferenced managed files."""
    counts = dict.fromkeys(_COUNTS, 0)
    counts["reset_results"] = len(store_repo.reset_processing_to_queued(conn, now))
    counts["corrected_paths"] = _correct_archived_document_paths(conn, datadir)
    removed_files, removed_dirs = _remove_orphans(conn, datadir)
    counts["removed_files"] = removed_files
    counts["removed_dirs"] = removed_dirs
    return counts


def _correct_archived_document_paths(conn: sqlite3.Connection, datadir: DataDir) -> int:
    corrected = 0
    rows = conn.execute(
        "SELECT document_id, path FROM documents "
        "WHERE path IS NOT NULL AND path LIKE 'process/%' "
        "ORDER BY document_id ASC"
    ).fetchall()
    for row in rows:
        old_path = row["path"]
        archive_path = datadir.archive / Path(old_path).name
        if archive_path.is_file() and not archive_path.is_symlink():
            store_repo.update_document_path(
                conn, row["document_id"], datadir.rel(archive_path)
            )
            corrected += 1
    return corrected


def _remove_orphans(conn: sqlite3.Connection, datadir: DataDir) -> tuple[int, int]:
    root = datadir.root.resolve()
    process = root / "process"
    archive = root / "archive"
    pages = root / "pages"
    exports = root / "exports"
    managed_roots = (process, archive, pages)
    referenced = _referenced_paths(
        conn, datadir, managed_roots=(process, archive, pages, exports)
    )
    removed_files = 0
    removed_dirs = 0

    for managed_root in managed_roots:
        if not managed_root.exists():
            continue
        for current, dirs, files in os.walk(
            managed_root, topdown=False, followlinks=False
        ):
            current_path = Path(current)
            for name in files:
                path = current_path / name
                if _remove_file_if_orphan(
                    path,
                    managed_root,
                    referenced,
                    managed_roots=(process, archive, pages, exports),
                ):
                    removed_files += 1
            for name in dirs:
                path = current_path / name
                if path.is_symlink():
                    if _remove_file_if_orphan(
                        path,
                        managed_root,
                        referenced,
                        managed_roots=(process, archive, pages, exports),
                    ):
                        removed_files += 1
                    continue
                if (
                    managed_root == pages or pages in path.parents
                ) and _remove_empty_page_dir(path, pages, referenced):
                    removed_dirs += 1
    return removed_files, removed_dirs


def _referenced_paths(
    conn: sqlite3.Connection, datadir: DataDir, managed_roots: tuple[Path, ...]
) -> set[Path]:
    referenced: set[Path] = set()
    for stored in store_repo.referenced_file_paths(conn):
        try:
            path = datadir.resolve(stored)
        except DataDirError:
            continue
        if _is_in_managed_dir(path, managed_roots):
            referenced.add(path)
    return referenced


def _remove_file_if_orphan(
    path: Path,
    managed_root: Path,
    referenced: set[Path],
    managed_roots: tuple[Path, ...],
) -> bool:
    if not _is_inside(path, managed_root) or not _is_in_managed_dir(
        path, managed_roots
    ):
        return False
    if path in referenced:
        return False
    path.unlink(missing_ok=True)
    return True


def _remove_empty_page_dir(path: Path, pages: Path, referenced: set[Path]) -> bool:
    if not _is_inside(path, pages) or path == pages:
        return False
    if path in referenced:
        return False
    try:
        path.rmdir()
    except OSError:
        return False
    return True


def _is_in_managed_dir(path: Path, managed_roots: tuple[Path, ...]) -> bool:
    return any(_is_inside(path, root) for root in managed_roots)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
