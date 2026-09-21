"""Self-check: what is ready and what is not, without starting the server or processing a document."""

from __future__ import annotations

import errno
import importlib
import importlib.metadata
import logging
import operator
import re
import socket
import sqlite3
import sys
import textwrap
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from privasheet.llm import (
    LlmClient,
    LlmConfigError,
    LlmInvalidResponse,
    LlmTimeout,
    LlmUnavailable,
)
from privasheet.ocr.engine import OcrError, make_rapidocr_engine
from privasheet.pipeline.datadir import (
    AlreadyRunning,
    DataDir,
    DataDirError,
    check_location,
)
from privasheet.store import StoreError, migrate, open_db

LLM_TIMEOUT_S = 20.0
TABLE_WIDTH = 100

_LLM_MESSAGES = [
    {"role": "user", "content": 'Reply with the JSON object {"ok": true}.'},
]
_PIP_ADVICE = (
    "The OCR engine is missing. "
    "Activate the environment and run `pip install rapidocr onnxruntime`."
)
_NOT_CHECKED = "not checked, the llm server check failed"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # "ok" | "error" | "info"
    detail: str


Row = tuple[str, str]


class _DataDirState:
    """What the data-directory check learned, for the checks that follow it."""

    def __init__(self) -> None:
        self.lock = None
        self.in_use = False
        self.usable = False


def run_checks(
    settings,
    *,
    import_module: Callable = importlib.import_module,
    ocr_engine_factory: Callable = make_rapidocr_engine,
    llm_client_factory: Callable = LlmClient,
) -> list[CheckResult]:
    """Run every check in order and return one row each. Prints nothing and never raises.

    The keyword arguments are seams for tests; the defaults are what the application uses.
    """
    state = _DataDirState()
    try:
        results = [
            _run("python", _check_python),
            _run("data directory", lambda: _check_data_dir(settings, state)),
            _run("database", lambda: _check_database(settings, state)),
            _run("ocr engine", lambda: _check_ocr_engine(import_module)),
            _run("ocr models", lambda: _check_ocr_models(ocr_engine_factory)),
            *_check_llm(settings, llm_client_factory),
            _run("web port", lambda: _check_web_port(settings, state)),
        ]
    finally:
        if state.lock is not None:
            state.lock.release()
    return results


def exit_code(results: Sequence[CheckResult]) -> int:
    return 1 if any(result.status == "error" for result in results) else 0


def format_table(results: Sequence[CheckResult]) -> str:
    """Fixed-width columns in plain ASCII; a long detail wraps under its own column."""
    name_width = max([len("check"), *(len(result.name) for result in results)])
    status_width = max([len("status"), *(len(result.status) for result in results)])
    indent = name_width + status_width + 4
    detail_width = max(TABLE_WIDTH - indent, 30)

    def line(name: str, status: str, detail: str) -> str:
        return f"{name:<{name_width}}  {status:<{status_width}}  {detail}".rstrip()

    lines = [line("check", "status", "detail")]
    for result in results:
        chunks = textwrap.wrap(result.detail, detail_width, break_long_words=False)
        first, *rest = chunks or [""]
        lines.append(line(result.name, result.status, first))
        lines.extend(" " * indent + chunk for chunk in rest)
    return "\n".join(lines)


def _run(name: str, check: Callable[[], Row]) -> CheckResult:
    try:
        status, detail = check()
    except Exception as exc:  # noqa: BLE001 - a check must never take the others down.
        status, detail = _unexpected(exc)
    return CheckResult(name, status, detail)


def _unexpected(exc: Exception) -> Row:
    return "error", f"unexpected {type(exc).__name__}"


def _check_python() -> Row:
    version = sys.version_info
    running = f"{version.major}.{version.minor}.{version.micro}"
    required = _requires_python()
    if required is None:
        return "info", f"{running} (requires-python not found)"
    if not _satisfies(tuple(version[:3]), required):
        return (
            "error",
            (
                f"{running} does not satisfy requires-python {required}. "
                f"Install Python {required.removeprefix('>=')} or newer."
            ),
        )
    return "ok", f"{running} (requires {required})"


def _requires_python() -> str | None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
        if project and project.get("requires-python"):
            return project["requires-python"]
    try:
        return importlib.metadata.metadata("privasheet").get("Requires-Python")
    except importlib.metadata.PackageNotFoundError:
        return None


_OPERATORS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}


def _satisfies(version: tuple[int, ...], specifier: str) -> bool:
    for clause in specifier.split(","):
        match = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*", clause)
        if match is None:
            raise ValueError(f"unsupported requires-python {specifier!r}")
        wanted = tuple(int(part) for part in match[2].split("."))
        if not _OPERATORS[match[1]](version[: len(wanted)], wanted):
            return False
    return True


def _check_data_dir(settings, state: _DataDirState) -> Row:
    root = Path(settings.data_dir)
    try:
        check_location(root)
    except DataDirError as exc:
        if "network share" in str(exc):
            advice = "Use a local disk; a mapped drive or UNC path will not do."
        else:
            advice = (
                "Set PRIVASHEET_DATA_DIR in .env to a local folder outside OneDrive."
            )
        return "error", f"{exc}. {advice}"

    # Taking the lock creates the directory, proves it is writable, and keeps a
    # PrivaSheet from starting on it while the database is being checked.
    try:
        state.lock = DataDir(root).acquire_lock()
    except AlreadyRunning:
        state.in_use = True
        return "info", f"{root} is in use by a running PrivaSheet"
    except OSError as exc:
        return (
            "error",
            (
                f"{root} cannot be created or written to ({type(exc).__name__}). "
                "Set PRIVASHEET_DATA_DIR in .env to a folder you can write to."
            ),
        )
    state.usable = True
    return "ok", f"{root} is writable"


def _check_database(settings, state: _DataDirState) -> Row:
    if state.in_use:
        return "info", "skipped, data directory in use"
    if not state.usable:
        return "info", "skipped, data directory not usable"
    try:
        conn = open_db(DataDir(settings.data_dir).db_path)
        try:
            migrate(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except StoreError as exc:
        return (
            "error",
            f"{exc}. Update PrivaSheet or Python, or use another data directory.",
        )
    except sqlite3.Error as exc:
        return (
            "error",
            (
                f"cannot open the database ({type(exc).__name__}). "
                "Check privasheet.db in the data directory, "
                "or set another PRIVASHEET_DATA_DIR."
            ),
        )
    return "ok", f"schema version {version}"


def _check_ocr_engine(import_module: Callable) -> Row:
    versions = []
    for name in ("rapidocr", "onnxruntime"):
        try:
            module = import_module(name)
        except ImportError as exc:
            return "error", f"cannot import {exc.name or name}. {_PIP_ADVICE}"
        version = getattr(module, "__version__", None)
        if version is None:
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = "unknown"
        versions.append(f"{name} {version}")
    return "ok", ", ".join(versions)


def _check_ocr_models(ocr_engine_factory: Callable) -> Row:
    # The pipeline builds its engine with no arguments, so the check does too.
    # RapidOCR logs every model it loads and resets its own logger level on each build,
    # so only switching logging off keeps that noise out of the table.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        engine = ocr_engine_factory()
    except OcrError as exc:
        return "error", f"{exc.detail} {_PIP_ADVICE}"
    finally:
        logging.disable(previous)
    names = [model["name"] for model in engine.models()]
    return "ok", ", ".join(names)


def _check_llm(settings, client_factory: Callable) -> list[CheckResult]:
    """One real call answers both the server row and the model row."""
    try:
        server, model = _llm_rows(settings, client_factory)
    except Exception as exc:  # noqa: BLE001 - see _run.
        server, model = _unexpected(exc), ("info", _NOT_CHECKED)
    return [CheckResult("llm server", *server), CheckResult("llm model", *model)]


def _llm_rows(settings, client_factory: Callable) -> tuple[Row, Row]:
    url, name = settings.base_url, settings.model
    answered: Row = ("ok", f"{url} answered")
    not_checked: Row = ("info", _NOT_CHECKED)

    def server_error(text: str) -> tuple[Row, Row]:
        return ("error", f"{url}: {text}"), not_checked

    try:
        client = client_factory(url, name)
        client.chat_json(_LLM_MESSAGES, timeout=LLM_TIMEOUT_S)
    except LlmConfigError as exc:
        return server_error(
            f"{exc}. Set PRIVASHEET_BASE_URL in .env to a loopback address."
        )
    except LlmTimeout:
        return server_error(
            f"the server did not answer within {LLM_TIMEOUT_S:.0f} s. "
            "Ollama may still be loading the model; wait a moment, then check again."
        )
    except LlmUnavailable as exc:
        # The client keeps only the HTTP status of an error answer, not its body.
        status = re.search(r"HTTP (\d{3})", str(exc))
        if status is None:
            return server_error(
                "the server did not answer. "
                "Ollama is not running. Start it, then check again."
            )
        if status[1] != "404":
            return server_error(
                f"the server answered HTTP {status[1]}. "
                "Read the server's own log, then check again."
            )
        missing = (
            f"model {name} is not available on that server. "
            "PRIVASHEET_MODEL names a model you have not pulled. "
            "Check with `ollama list`."
        )
        return answered, ("error", missing)
    except LlmInvalidResponse:
        return answered, ("info", f"{name} answered, but not with JSON")
    return answered, ("ok", f"{name} answered")


def _check_web_port(settings, state: _DataDirState) -> Row:
    host, port = settings.host, settings.port
    try:
        _try_bind(host, port)
    except OSError as exc:
        if not _is_address_in_use(exc):
            return (
                "error",
                (
                    f"cannot bind {host}:{port} ({type(exc).__name__}). "
                    "Check PRIVASHEET_HOST and PRIVASHEET_PORT in .env."
                ),
            )
        if state.in_use:
            return (
                "info",
                f"{host}:{port} is taken, most likely by the running PrivaSheet",
            )
        other = port + 1 if port < 65535 else port - 1
        return (
            "error",
            f"Something else holds {port}. Set PRIVASHEET_PORT={other} in .env.",
        )
    return "ok", f"{host}:{port} is free"


def _try_bind(host: str, port: int) -> None:
    family, kind, proto, _, address = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    with socket.socket(family, kind, proto) as sock:
        if sys.platform != "win32":
            # What the server does; without it a port in TIME_WAIT would look taken.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)


def _is_address_in_use(exc: OSError) -> bool:
    # WSAEACCES is what Windows answers for a port another program holds exclusively.
    return exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10013
