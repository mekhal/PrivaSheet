"""Pipeline runtime: start-up order, shutdown and status for the web app."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from privasheet.llm import LlmClient, LlmConfigError
from privasheet.pipeline.datadir import AlreadyRunning, DataDir, DataDirError
from privasheet.pipeline.recovery import recover
from privasheet.pipeline.worker import Worker
from privasheet.store import migrate, open_db, repo

STOP_TIMEOUT_S = 30

# Start-up failures that mean "refuse to start" rather than "bug".
REFUSALS = (AlreadyRunning, DataDirError, LlmConfigError)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class PipelineRuntime:
    """Owns the data-directory lock and the single background worker."""

    def __init__(self, settings, *, ocr_engine=None, llm_client=None) -> None:
        self.settings = settings
        self.datadir = DataDir(settings.data_dir)
        self._ocr_engine = ocr_engine
        self._llm_client = llm_client
        self._lock = None
        self._worker: Worker | None = None
        self._state_lock = threading.Lock()

    def start(self) -> None:
        with self._state_lock:
            if self._lock is not None:
                raise RuntimeError("pipeline runtime already started")
            self.datadir.prepare()
            lock = self.datadir.acquire_lock()
            try:
                conn = open_db(self.datadir.db_path)
                try:
                    migrate(conn)
                    recover(conn, self.datadir, _now_iso())
                finally:
                    conn.close()
                llm_client = self._llm_client or LlmClient(
                    self.settings.base_url, self.settings.model
                )
                worker = Worker(
                    self.datadir.root,
                    self.datadir.db_path,
                    self.settings,
                    llm_client,
                    ocr_engine=self._ocr_engine,
                )
                worker.start()
            except BaseException:
                lock.release()
                raise
            self._lock = lock
            self._worker = worker

    def stop(self) -> None:
        with self._state_lock:
            worker, lock = self._worker, self._lock
            self._worker = None
            self._lock = None
            try:
                if worker is not None:
                    worker.stop(STOP_TIMEOUT_S)
            finally:
                if lock is not None:
                    lock.release()

    def status(self) -> dict:
        worker = self._worker
        running = worker is not None and worker.is_running()
        queue: dict[str, int] = {}
        if self._lock is not None:
            # The worker owns its own connection; this request thread opens a short-lived one.
            conn = open_db(self.datadir.db_path)
            try:
                queue = repo.count_results_by_status(conn)
            finally:
                conn.close()
        return {"worker": "running" if running else "stopped", "queue": queue}
