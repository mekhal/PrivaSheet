"""Data-directory layout, location checks, and process locking."""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Self


class DataDirError(Exception):
    """The data directory cannot be used safely."""


class AlreadyRunning(DataDirError):
    """Another process already holds the data-directory lock."""


class DataDir:
    """Application-owned storage rooted under one local data directory."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    @property
    def db_path(self) -> Path:
        return self.root / "privasheet.db"

    @property
    def process(self) -> Path:
        return self.root / "process"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def pages(self) -> Path:
        return self.root / "pages"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def lock_path(self) -> Path:
        return self.root / "privasheet.lock"

    def prepare(self) -> None:
        check_location(self.root)
        for path in (self.process, self.archive, self.pages, self.exports):
            path.mkdir(parents=True, exist_ok=True)

    def rel(self, path: str | os.PathLike[str]) -> str:
        root = self.root.resolve()
        candidate = Path(path).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise DataDirError("path is outside the data directory") from exc
        return relative.as_posix()

    def resolve(self, rel: str | os.PathLike[str]) -> Path:
        raw = os.fspath(rel)
        if _is_network_path(raw) or PureWindowsPath(raw).drive:
            raise DataDirError("stored path must be relative to the data directory")
        raw = raw.replace("\\", "/")
        posix = PurePosixPath(raw)
        if posix.is_absolute() or ".." in posix.parts:
            raise DataDirError("stored path must be relative to the data directory")
        root = self.root.resolve()
        candidate = (root / Path(*posix.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DataDirError("stored path leaves the data directory") from exc
        return candidate

    def acquire_lock(self) -> _BaseLock:
        self.root.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            return _WindowsLock.acquire(self.lock_path)
        return _PosixLock.acquire(self.lock_path)


def check_location(
    root: str | os.PathLike[str],
    environ: os._Environ[str] | dict[str, str] = os.environ,
) -> None:
    raw = os.fspath(root)
    if _is_network_path(raw) or PureWindowsPath(raw).drive.startswith("\\\\"):
        raise DataDirError("data directory must not be a network share")

    root_resolved = Path(root).resolve()
    root_cmp = _location_key(root_resolved)
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = environ.get(name)
        if not value:
            continue
        onedrive = Path(value).resolve()
        onedrive_cmp = _location_key(onedrive)
        if root_cmp == onedrive_cmp or root_cmp.startswith(f"{onedrive_cmp}{os.sep}"):
            raise DataDirError("data directory must not be inside OneDrive")


def _is_network_path(path: str) -> bool:
    return path.startswith(("//", "\\\\"))


def _location_key(path: Path) -> str:
    text = os.fspath(path)
    return os.path.normcase(text) if sys.platform == "win32" else text


class _BaseLock:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def release(self) -> None:
        raise NotImplementedError


class _PosixLock(_BaseLock):
    def __init__(self, fileobj) -> None:
        self._fileobj = fileobj
        self._released = False

    @classmethod
    def acquire(cls, path: Path) -> _PosixLock:
        import fcntl

        fileobj = path.open("a+")
        try:
            fcntl.flock(fileobj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            fileobj.close()
            raise AlreadyRunning("data directory is already locked") from exc
        except OSError:
            fileobj.close()
            raise
        return cls(fileobj)

    def release(self) -> None:
        if self._released:
            return
        import fcntl

        try:
            fcntl.flock(self._fileobj.fileno(), fcntl.LOCK_UN)
        finally:
            self._released = True
            self._fileobj.close()


class _WindowsLock(_BaseLock):
    """Small Windows-only msvcrt lock branch; it is not exercised on POSIX CI."""

    def __init__(self, fileobj) -> None:
        self._fileobj = fileobj
        self._released = False

    @classmethod
    def acquire(cls, path: Path) -> _WindowsLock:
        import msvcrt

        fileobj = path.open("a+b")
        try:
            fileobj.seek(0)
            fileobj.write(b"\0")
            fileobj.flush()
            fileobj.seek(0)
            msvcrt.locking(fileobj.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            fileobj.close()
            raise AlreadyRunning("data directory is already locked") from exc
        return cls(fileobj)

    def release(self) -> None:
        if self._released:
            return
        import msvcrt

        try:
            self._fileobj.seek(0)
            msvcrt.locking(self._fileobj.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._released = True
            self._fileobj.close()
