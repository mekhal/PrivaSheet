import os
import subprocess
import sys
from pathlib import Path

import pytest

from privasheet.pipeline.datadir import (
    AlreadyRunning,
    DataDir,
    DataDirError,
    check_location,
)


def test_prepare_creates_layout(tmp_path):
    datadir = DataDir(tmp_path / "data")

    datadir.prepare()

    assert datadir.db_path == tmp_path / "data" / "privasheet.db"
    assert datadir.process == tmp_path / "data" / "process"
    assert datadir.archive == tmp_path / "data" / "archive"
    assert datadir.pages == tmp_path / "data" / "pages"
    assert datadir.exports == tmp_path / "data" / "exports"
    assert datadir.lock_path == tmp_path / "data" / "privasheet.lock"
    for path in (datadir.process, datadir.archive, datadir.pages, datadir.exports):
        assert path.is_dir()


def test_rel_and_resolve_round_trip(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    path = datadir.pages / "snap-1" / "page-1.png"
    path.parent.mkdir()
    path.write_text("page", encoding="utf-8")

    rel = datadir.rel(path)

    assert rel == "pages/snap-1/page-1.png"
    assert datadir.resolve(rel) == path


@pytest.mark.parametrize(
    "stored",
    [
        "/tmp/outside.txt",
        "../outside.txt",
        "pages/../../outside.txt",
        "C:/Users/alice/outside.txt",
        "C:\\Users\\alice\\outside.txt",
        "//server/share/file.txt",
        "\\\\server\\share\\file.txt",
    ],
)
def test_resolve_refuses_paths_that_leave_root(tmp_path, stored):
    datadir = DataDir(tmp_path / "data")

    with pytest.raises(DataDirError):
        datadir.resolve(stored)


def test_rel_refuses_paths_outside_root(tmp_path):
    datadir = DataDir(tmp_path / "data")

    with pytest.raises(DataDirError):
        datadir.rel(tmp_path / "outside.txt")


@pytest.mark.parametrize(
    "root", ["//server/share/privasheet", "\\\\server\\share\\privasheet"]
)
def test_check_location_refuses_unc_paths(root):
    with pytest.raises(DataDirError):
        check_location(root, environ={})


@pytest.mark.parametrize(
    "env_name", ["OneDrive", "OneDriveConsumer", "OneDriveCommercial"]
)
def test_check_location_refuses_onedrive_locations(tmp_path, env_name):
    onedrive = tmp_path / "OneDrive"
    root = onedrive / "PrivaSheet"

    with pytest.raises(DataDirError):
        check_location(root, environ={env_name: os.fspath(onedrive)})


def test_check_location_accepts_normal_directory(tmp_path):
    assert check_location(tmp_path / "local" / "PrivaSheet", environ={}) is None


def test_lock_refuses_second_holder_in_same_process(tmp_path):
    first = DataDir(tmp_path / "data")
    second = DataDir(tmp_path / "data")
    first.prepare()

    handle = first.acquire_lock()
    try:
        with pytest.raises(AlreadyRunning):
            second.acquire_lock()
    finally:
        handle.release()

    second.acquire_lock().release()


def test_lock_refuses_child_process_and_is_free_after_kill(tmp_path):
    datadir = DataDir(tmp_path / "data")
    datadir.prepare()
    script = (
        "import sys, time\n"
        "from privasheet.pipeline.datadir import DataDir\n"
        "handle = DataDir(sys.argv[1]).acquire_lock()\n"
        "print('locked', flush=True)\n"
        "time.sleep(60)\n"
        "handle.release()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, os.fspath(datadir.root)],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    [
                        os.fspath(Path(__file__).parents[1] / "src"),
                        os.environ.get("PYTHONPATH"),
                    ],
                )
            ),
        },
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(AlreadyRunning):
            datadir.acquire_lock()
    finally:
        child.kill()
        child.wait(timeout=10)

    datadir.acquire_lock().release()
