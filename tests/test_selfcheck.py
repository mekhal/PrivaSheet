"""Self-check tests: fakes and temporary directories, never the real OCR engine or an LLM server."""

import logging
import socket
from dataclasses import replace
from typing import ClassVar

import pytest

import privasheet.__main__ as launcher
from privasheet.llm import LlmConfigError, LlmTimeout, LlmUnavailable
from privasheet.pipeline.datadir import DataDir
from privasheet.selfcheck import CheckResult, exit_code, format_table, run_checks
from privasheet.store import MIGRATIONS
from privasheet.web.settings import Settings

NAMES = [
    "python",
    "data directory",
    "database",
    "ocr engine",
    "ocr models",
    "llm server",
    "llm model",
    "web port",
]


class FakeModule:
    def __init__(self, version):
        self.__version__ = version


class FakeEngine:
    def models(self):
        return [
            {"name": "det-fake.onnx", "sha256": "0" * 64},
            {"name": "rec-fake.onnx", "sha256": "1" * 64},
        ]


class FakeLlm:
    """Stands in for LlmClient; `outcome` is what chat_json does."""

    outcome = None
    calls: ClassVar[list] = []

    def __init__(self, base_url, model, timeout=30.0):
        FakeLlm.calls.append((base_url, model))
        if isinstance(self.outcome, LlmConfigError):
            raise self.outcome

    def chat_json(self, messages, timeout=None):
        assert timeout is not None and timeout <= 30
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {"ok": True}


def llm_returning(outcome):
    return type("Llm", (FakeLlm,), {"outcome": outcome})


def fake_import(missing=()):
    def import_module(name):
        if name in missing:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return FakeModule("9.9.9")

    return import_module


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    return Settings(
        host="127.0.0.1",
        port=free_port(),
        allowed_hosts=("127.0.0.1",),
        data_dir=tmp_path / "data",
        base_url="http://127.0.0.1:11434",
        model="fake-model:7b",
        document_timeout_s=30,
    )


def run(settings, *, llm=None, missing=(), ocr_factory=FakeEngine):
    return run_checks(
        settings,
        import_module=fake_import(missing),
        ocr_engine_factory=ocr_factory,
        llm_client_factory=llm or llm_returning(None),
    )


def by_name(results):
    return {result.name: result for result in results}


def test_every_check_ok_lists_every_row_and_exits_0(settings):
    results = run(settings)

    assert [result.name for result in results] == NAMES
    assert [result.status for result in results] == ["ok"] * len(NAMES)
    assert exit_code(results) == 0
    rows = by_name(results)
    assert f"schema version {len(MIGRATIONS)}" in rows["database"].detail
    assert "9.9.9" in rows["ocr engine"].detail
    assert "det-fake.onnx" in rows["ocr models"].detail
    assert settings.base_url in rows["llm server"].detail
    assert settings.model in rows["llm model"].detail
    assert str(settings.port) in rows["web port"].detail


def test_run_checks_prints_nothing(settings, capsys):
    run(settings)

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_the_llm_is_called_with_the_configured_base_url_and_model(settings):
    FakeLlm.calls.clear()

    run(settings)

    assert FakeLlm.calls == [(settings.base_url, settings.model)]


def test_the_data_directory_is_free_again_after_the_check(settings):
    run(settings)

    with DataDir(settings.data_dir).acquire_lock():
        pass


def test_a_data_directory_inside_onedrive_is_an_error(settings, tmp_path, monkeypatch):
    onedrive = tmp_path / "OneDrive"
    monkeypatch.setenv("OneDrive", str(onedrive))
    inside = replace(settings, data_dir=onedrive / "PrivaSheet")

    results = run(inside)

    rows = by_name(results)
    assert rows["data directory"].status == "error"
    assert "OneDrive" in rows["data directory"].detail
    assert rows["database"].status != "ok"
    assert not (onedrive / "PrivaSheet").exists()
    assert [result.name for result in results] == NAMES
    assert exit_code(results) == 1


def test_a_network_data_directory_is_an_error(settings):
    results = run(replace(settings, data_dir="//server/share/privasheet"))

    row = by_name(results)["data directory"]
    assert row.status == "error"
    assert "network share" in row.detail
    assert "Use a local disk" in row.detail


def test_a_data_directory_that_cannot_be_created_is_an_error(settings, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")

    results = run(replace(settings, data_dir=blocker / "data"))

    row = by_name(results)["data directory"]
    assert row.status == "error"
    assert row.detail
    assert [result.name for result in results] == NAMES


def test_a_missing_rapidocr_is_an_error_and_later_checks_still_run(settings):
    results = run(settings, missing=("rapidocr",))

    rows = by_name(results)
    assert [result.name for result in results] == NAMES
    assert rows["ocr engine"].status == "error"
    assert "rapidocr" in rows["ocr engine"].detail
    assert "pip install rapidocr onnxruntime" in rows["ocr engine"].detail
    assert [rows[name].status for name in NAMES[5:]] == ["ok", "ok", "ok"]
    assert exit_code(results) == 1


def test_ocr_models_that_cannot_be_built_are_an_error(settings):
    from privasheet.ocr.engine import OcrError

    def broken():
        raise OcrError("OCR_FAILED", "Missing OCR model det under /nowhere.")

    rows = by_name(run(settings, ocr_factory=broken))

    assert rows["ocr models"].status == "error"
    assert "Missing OCR model det" in rows["ocr models"].detail
    assert rows["ocr engine"].status == "ok"


def test_an_unavailable_llm_is_an_error_and_the_table_lists_every_row(settings):
    llm = llm_returning(LlmUnavailable("LLM service is unavailable"))

    results = run(settings, llm=llm)

    rows = by_name(results)
    assert [result.name for result in results] == NAMES
    assert rows["llm server"].status == "error"
    assert "did not answer" in rows["llm server"].detail
    assert "Ollama is not running" in rows["llm server"].detail
    assert rows["llm model"].status == "info"
    assert rows["web port"].status == "ok"
    assert exit_code(results) == 1
    table = format_table(results)
    for name in NAMES:
        assert name in table


def test_an_llm_timeout_is_a_server_that_did_not_answer(settings):
    rows = by_name(run(settings, llm=llm_returning(LlmTimeout("timed out"))))

    assert rows["llm server"].status == "error"
    assert "did not answer" in rows["llm server"].detail


def test_a_refused_llm_host_names_the_broken_rule(settings):
    llm = llm_returning(LlmConfigError("LLM host must be loopback-only"))

    rows = by_name(run(settings, llm=llm))

    assert rows["llm server"].status == "error"
    assert "LLM host must be loopback-only" in rows["llm server"].detail
    assert rows["llm model"].status == "info"


def test_a_model_the_server_does_not_have_is_an_error_on_the_model_row(settings):
    llm = llm_returning(LlmUnavailable("LLM service returned HTTP 404"))

    rows = by_name(run(settings, llm=llm))

    assert rows["llm server"].status == "ok"
    assert rows["llm model"].status == "error"
    assert settings.model in rows["llm model"].detail
    assert "not available on that server" in rows["llm model"].detail
    assert "ollama list" in rows["llm model"].detail


def test_another_http_error_is_an_llm_server_error(settings):
    llm = llm_returning(LlmUnavailable("LLM service returned HTTP 500"))

    rows = by_name(run(settings, llm=llm))

    assert rows["llm server"].status == "error"
    assert "HTTP 500" in rows["llm server"].detail


def test_a_locked_data_directory_is_info_and_the_exit_code_stays_0(settings):
    with DataDir(settings.data_dir).acquire_lock():
        results = run(settings)

    rows = by_name(results)
    assert rows["data directory"].status == "info"
    assert "in use by a running PrivaSheet" in rows["data directory"].detail
    assert rows["database"].status == "info"
    assert rows["database"].detail == "skipped, data directory in use"
    assert not DataDir(settings.data_dir).db_path.exists()
    assert "error" not in [result.status for result in results]
    assert exit_code(results) == 0


def test_a_taken_port_is_an_error(settings):
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        taken = replace(settings, port=holder.getsockname()[1])

        row = by_name(run(taken))["web port"]

    assert row.status == "error"
    assert "Something else holds" in row.detail
    assert str(taken.port) in row.detail
    assert "PRIVASHEET_PORT" in row.detail


def test_a_taken_port_while_privasheet_runs_is_info(settings):
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        taken = replace(settings, port=holder.getsockname()[1])
        with DataDir(taken.data_dir).acquire_lock():
            results = run(taken)

    assert by_name(results)["web port"].status == "info"
    assert exit_code(results) == 0


def test_an_unexpected_exception_becomes_an_error_row_naming_its_class(settings):
    def explode():
        raise ZeroDivisionError("boom")

    results = run(settings, ocr_factory=explode)

    row = by_name(results)["ocr models"]
    assert row.status == "error"
    assert "ZeroDivisionError" in row.detail
    assert [result.name for result in results] == NAMES


def test_an_unexpected_llm_exception_does_not_stop_the_port_check(settings):
    results = run(settings, llm=llm_returning(RuntimeError("boom")))

    rows = by_name(results)
    assert rows["llm server"].status == "error"
    assert "RuntimeError" in rows["llm server"].detail
    assert rows["web port"].status == "ok"


def test_format_table_lines_up_the_columns_when_a_detail_is_long():
    long_detail = "the server did not answer " * 12
    results = [
        CheckResult("python", "ok", "3.12.4"),
        CheckResult("data directory", "info", "in use by a running PrivaSheet"),
        CheckResult("llm server", "error", long_detail.strip()),
    ]

    lines = format_table(results).splitlines()

    header = lines[0]
    detail_at = header.index("detail")
    status_at = header.index("status")
    assert lines[1] == lines[1].rstrip()
    rows = [line for line in lines[1:] if line.strip()]
    starts = {name: line for name, line in zip(("python", "data"), rows, strict=False)}
    assert starts["python"][status_at:].startswith("ok")
    assert starts["python"][detail_at:] == "3.12.4"
    assert starts["data"][status_at:].startswith("info")
    assert starts["data"][detail_at:].startswith("in use by a running PrivaSheet")
    error_line = next(line for line in rows if line.startswith("llm server"))
    assert error_line[status_at:].startswith("error")
    assert error_line[detail_at:].startswith("the server did not answer")
    # A long detail wraps under its own column instead of pushing the table out of line.
    continuation = [line for line in rows if line.startswith(" " * detail_at)]
    assert continuation
    assert all(len(line) <= 100 for line in lines)
    assert " ".join(
        part.strip() for part in [error_line[detail_at:], *continuation]
    ) == (long_detail.strip())


def test_format_table_is_plain_ascii_with_a_header():
    table = format_table([CheckResult("python", "ok", "3.12.4")])

    assert table.isascii()
    assert table.splitlines()[0].split() == ["check", "status", "detail"]
    assert "3.12.4" in table


def test_format_table_widens_the_name_column_for_a_long_name():
    lines = format_table(
        [
            CheckResult("a very long check name", "ok", "fine"),
            CheckResult("python", "ok", "fine"),
        ]
    ).splitlines()

    assert len({line.index("fine") for line in lines[1:]}) == 1


def test_exit_code_is_1_only_when_something_is_in_error():
    assert exit_code([CheckResult("a", "ok", ""), CheckResult("b", "info", "")]) == 0
    assert exit_code([CheckResult("a", "ok", ""), CheckResult("b", "error", "")]) == 1


def test_main_check_prints_the_table_and_never_starts_the_server(
    settings, monkeypatch, capsys
):
    rows = [CheckResult(name, "ok", "fine") for name in NAMES]
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)
    monkeypatch.setattr(launcher, "run_checks", lambda given: rows)

    def refuse(*args, **kwargs):
        raise AssertionError("--check must not start the server")

    monkeypatch.setattr(launcher, "build_app", refuse)
    monkeypatch.setattr(launcher.uvicorn, "run", refuse)

    assert launcher.main(["--check"]) == 0
    out = capsys.readouterr().out
    for name in NAMES:
        assert name in out


def test_main_check_exits_1_when_a_row_is_an_error(settings, monkeypatch, capsys):
    rows = [
        CheckResult("python", "ok", "3.12"),
        CheckResult("llm server", "error", "x"),
    ]
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)
    monkeypatch.setattr(launcher, "run_checks", lambda given: rows)

    assert launcher.main(["--check"]) == 1
    assert "llm server" in capsys.readouterr().out


def test_main_without_arguments_still_starts_the_server(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)
    monkeypatch.setattr(
        launcher.uvicorn, "run", lambda app, host, port: calls.append((host, port))
    )
    monkeypatch.setattr(
        launcher, "run_checks", lambda given: pytest.fail("no check without --check")
    )

    assert launcher.main([]) == 0
    assert calls == [(settings.host, settings.port)]


def test_main_check_reports_unreadable_settings_in_one_line(monkeypatch, capsys):
    def broken():
        raise ValueError("invalid literal for int() with base 10: 'abc'")

    monkeypatch.setattr(launcher, "load_settings", broken)

    assert launcher.main(["--check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1


def test_the_engine_logging_stays_out_of_the_check_and_comes_back_after(
    settings, caplog
):
    def chatty():
        logging.getLogger("RapidOCR").warning("Using model file")
        return FakeEngine()

    with caplog.at_level(logging.INFO):
        run(settings, ocr_factory=chatty)
        assert "Using model file" not in caplog.text
        logging.getLogger("privasheet.test").info("after the check")
        assert "after the check" in caplog.text
