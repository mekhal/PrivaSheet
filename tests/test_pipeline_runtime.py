import os
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import privasheet.__main__ as launcher
import privasheet.pipeline.runtime as runtime_module
from privasheet.ingest.checks import Limits
from privasheet.llm import LlmConfigError
from privasheet.pipeline.datadir import AlreadyRunning, DataDir, DataDirError
from privasheet.pipeline.intake import Upload, create_batch
from privasheet.pipeline.runtime import PipelineRuntime
from privasheet.store import migrate, open_db
from privasheet.store.repo import insert_template
from privasheet.web.app import create_app
from privasheet.web.settings import Settings, load_settings

OCR_ENGINE = "tests.fake_ocr:make_engine"


class FakeLlmClient:
    """Blocks in chat_json until released, so queue counts stay observable."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def chat_json(self, messages, timeout=None):
        self.entered.set()
        self.release.wait(5)
        return {
            "fields": {"size": {"box_ids": ["p1-b0000"], "span": "10x12"}},
            "tables": {},
        }


def make_settings(tmp_path, **overrides):
    settings = Settings(
        host="127.0.0.1",
        port=8765,
        allowed_hosts=("testserver",),
        data_dir=tmp_path / "data",
        base_url="http://127.0.0.1:11434",
        model="fake-model",
        document_timeout_s=30,
    )
    return replace(settings, **overrides)


def template_doc():
    return {
        "template_id": "invoice-a",
        "version": 1,
        "version_label": "1.0.20260917",
        "name": "Invoice A",
        "created_at": "2026-09-17T00:00:00Z",
        "fields": [
            {
                "key": "size",
                "type": "text",
                "required": True,
                "description": "The page size text.",
                "hint": {"labels": ["10x12"]},
            }
        ],
        "tables": [],
        "match": {"min_key_label_ratio": 0},
    }


def make_runtime(tmp_path, llm=None, **overrides):
    return PipelineRuntime(
        make_settings(tmp_path, **overrides),
        ocr_engine=OCR_ENGINE,
        llm_client=llm or FakeLlmClient(),
    )


def lock_is_free(datadir):
    try:
        lock = datadir.acquire_lock()
    except AlreadyRunning:
        return False
    lock.release()
    return True


def submit_batch(datadir):
    conn = open_db(datadir.db_path)
    try:
        insert_template(conn, template_doc())
        streams = []
        uploads = []
        for name in ("one.png", "two.png"):
            path = datadir.process / name
            Image.new("RGB", (10, 12), (len(name), 90, 120)).save(path, format="PNG")
            stream = path.open("rb")
            streams.append(stream)
            uploads.append(Upload(name, stream))
        try:
            create_batch(conn, datadir.root, "invoice-a", 1, uploads, limits=Limits())
        finally:
            for stream in streams:
                stream.close()
    finally:
        conn.close()


def test_start_creates_layout_migrates_and_starts_worker(tmp_path):
    runtime = make_runtime(tmp_path)
    datadir = DataDir(tmp_path / "data")
    try:
        runtime.start()
        for path in (datadir.process, datadir.archive, datadir.pages, datadir.exports):
            assert path.is_dir()
        conn = open_db(datadir.db_path)
        try:
            migrate(conn)  # idempotent; the tables must already exist
            assert conn.execute("SELECT count(*) FROM results").fetchone()[0] == 0
        finally:
            conn.close()
        assert runtime.status() == {"worker": "running", "queue": {}}
    finally:
        runtime.stop()
    assert runtime.status()["worker"] == "stopped"


def test_second_runtime_is_refused_and_first_keeps_working(tmp_path):
    first = make_runtime(tmp_path)
    second = make_runtime(tmp_path)
    try:
        first.start()
        with pytest.raises(AlreadyRunning):
            second.start()
        second.stop()
        assert first.status()["worker"] == "running"
        assert not lock_is_free(DataDir(tmp_path / "data"))
    finally:
        first.stop()


def test_non_loopback_llm_url_refuses_start_and_leaves_no_lock(tmp_path):
    runtime = PipelineRuntime(
        make_settings(tmp_path, base_url="http://llm.example.com:11434"),
        ocr_engine=OCR_ENGINE,
    )
    with pytest.raises(LlmConfigError):
        runtime.start()
    assert lock_is_free(DataDir(tmp_path / "data"))
    assert runtime.status()["worker"] == "stopped"


def test_bad_data_dir_refuses_start(tmp_path, monkeypatch):
    onedrive = tmp_path / "OneDrive"
    monkeypatch.setenv("OneDrive", os.fspath(onedrive))
    runtime = make_runtime(tmp_path, data_dir=onedrive / "data")
    with pytest.raises(DataDirError):
        runtime.start()


def test_stop_twice_is_fine_and_frees_the_lock(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.start()
    runtime.stop()
    runtime.stop()
    assert lock_is_free(DataDir(tmp_path / "data"))


def test_stop_keeps_the_lock_while_the_worker_is_still_processing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runtime_module, "STOP_TIMEOUT_S", 0.1)
    llm = FakeLlmClient()
    runtime = make_runtime(tmp_path, llm=llm)
    datadir = DataDir(tmp_path / "data")
    runtime.start()
    try:
        submit_batch(datadir)
        assert llm.entered.wait(5)
        with pytest.raises(RuntimeError, match="did not stop"):
            runtime.stop()
        assert runtime.status()["worker"] == "running"
        assert not lock_is_free(datadir)
        with pytest.raises(AlreadyRunning):
            make_runtime(tmp_path).start()
    finally:
        llm.release.set()
        monkeypatch.setattr(runtime_module, "STOP_TIMEOUT_S", 5)
        runtime.stop()
    assert runtime.status()["worker"] == "stopped"
    assert lock_is_free(datadir)


def test_stop_without_start_is_fine(tmp_path):
    make_runtime(tmp_path).stop()


def test_status_endpoint_reports_worker_and_queue_counts(tmp_path):
    llm = FakeLlmClient()
    runtime = make_runtime(tmp_path, llm=llm)
    app = create_app(make_settings(tmp_path), runtime)
    datadir = DataDir(tmp_path / "data")
    with TestClient(app) as client:
        submit_batch(datadir)

        assert llm.entered.wait(5)
        response = client.get("/api/pipeline/status")
        llm.release.set()

        assert response.status_code == 200
        body = response.json()
        assert body["worker"] == "running"
        assert sum(body["queue"].values()) == 2
        assert body["queue"].get("processing") == 1
    assert runtime.status()["worker"] == "stopped"
    assert lock_is_free(datadir)


def test_status_endpoint_is_503_without_a_runtime(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    response = client.get("/api/pipeline/status")
    assert response.status_code == 503
    assert response.json() == {"worker": "not_configured"}


def test_status_endpoint_applies_host_allowlist_but_not_origin_check(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    assert (
        client.get("/api/pipeline/status", headers={"host": "evil.test"}).status_code
        == 403
    )
    response = client.get(
        "/api/pipeline/status", headers={"origin": "http://evil.test"}
    )
    assert response.status_code == 503


def test_lifespan_starts_and_stops_the_runtime(tmp_path):
    runtime = make_runtime(tmp_path)
    app = create_app(make_settings(tmp_path), runtime)
    assert runtime.status()["worker"] == "stopped"
    with TestClient(app):
        assert runtime.status()["worker"] == "running"
    assert runtime.status()["worker"] == "stopped"


def test_default_base_url_is_ollama_and_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("PRIVASHEET_BASE_URL", raising=False)
    monkeypatch.delenv("PRIVASHEET_HOST", raising=False)
    monkeypatch.delenv("PRIVASHEET_PORT", raising=False)
    missing = tmp_path / "missing.env"
    assert load_settings(missing).base_url == "http://127.0.0.1:11434"
    monkeypatch.setenv("PRIVASHEET_HOST", "localhost")
    assert load_settings(missing).base_url == "http://127.0.0.1:11434"
    monkeypatch.setenv("PRIVASHEET_BASE_URL", "http://127.0.0.1:9999")
    assert load_settings(missing).base_url == "http://127.0.0.1:9999"


def test_build_app_wires_a_runtime_through_the_lifespan(tmp_path):
    settings = make_settings(tmp_path)
    app = launcher.build_app(settings)
    runtime = app.state.runtime
    assert isinstance(runtime, PipelineRuntime)
    assert app.state.settings == settings
    assert runtime.status()["worker"] == "stopped"
    with TestClient(app) as client:
        assert client.get("/api/pipeline/status").json()["worker"] == "running"
        assert runtime.status()["worker"] == "running"
    assert runtime.status()["worker"] == "stopped"
    assert lock_is_free(DataDir(tmp_path / "data"))


def test_main_reports_a_refusal_in_one_line_and_exits_1(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path, base_url="http://llm.example.com:11434")
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)

    def fake_run(app, host, port):
        assert (host, port) == ("127.0.0.1", 8765)
        with TestClient(app):
            pass

    monkeypatch.setattr(launcher.uvicorn, "run", fake_run)

    assert launcher.main() == 1
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert "loopback" in err
    assert lock_is_free(DataDir(tmp_path / "data"))


def test_main_runs_uvicorn_on_configured_address(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, host="127.0.0.1", port=9001)
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(
        launcher.uvicorn, "run", lambda app, host, port: calls.append((app, host, port))
    )
    assert launcher.main() == 0
    assert calls[0][1:] == ("127.0.0.1", 9001)


@pytest.mark.parametrize("outcome", ["returns", "exits"])
def test_main_reports_a_refusal_however_uvicorn_ends(
    tmp_path, monkeypatch, capsys, outcome
):
    settings = make_settings(tmp_path, base_url="http://llm.example.com:11434")
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)

    def fake_run(app, host, port):
        with pytest.raises(LlmConfigError), TestClient(app):
            pass
        if outcome == "exits":
            raise SystemExit(3)

    monkeypatch.setattr(launcher.uvicorn, "run", fake_run)
    assert launcher.main() == 1
    assert "cannot start" in capsys.readouterr().err


def test_main_reraises_an_unrelated_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "load_settings", lambda: make_settings(tmp_path))

    def fake_run(app, host, port):
        raise SystemExit(2)

    monkeypatch.setattr(launcher.uvicorn, "run", fake_run)
    with pytest.raises(SystemExit):
        launcher.main()
