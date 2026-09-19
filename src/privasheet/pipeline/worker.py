"""Single background worker for draining the document pipeline queue."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from privasheet.ingest.checks import Limits
from privasheet.ocr.runner import OcrCancelled
from privasheet.pipeline.datadir import AlreadyRunning, DataDir
from privasheet.pipeline.process import PROMPT_VERSION, process_document
from privasheet.store import open_db, repo

PROCESSING_ERROR = "PROCESSING_ERROR"

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Worker:
    """Drain queued pipeline results on one process-owned daemon thread."""

    _process_lock = threading.Lock()
    _process_worker: Worker | None = None

    def __init__(
        self,
        datadir,
        db_path,
        settings_like,
        llm_client,
        *,
        ocr_engine=None,
        poll_interval_s=1,
        limits=None,
        now=_now_iso,
    ) -> None:
        self.datadir = DataDir(datadir)
        self.db_path = Path(db_path)
        self.settings = settings_like
        self.llm_client = llm_client
        self.ocr_engine = ocr_engine
        self.poll_interval_s = poll_interval_s
        self.limits = limits or Limits()
        self.now = now

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("worker already started")
        self._require_existing_lock()
        with self._process_lock:
            if self.__class__._process_worker is not None:
                raise RuntimeError("worker already running in this process")
            self.__class__._process_worker = self
        self._started = True
        self._thread = threading.Thread(
            target=self._run,
            name="privasheet-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout=None) -> None:
        self._stop.set()
        self._cancel.set()
        self.wake()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        if thread is None or not thread.is_alive():
            with self._process_lock:
                if self.__class__._process_worker is self:
                    self.__class__._process_worker = None

    def wake(self) -> None:
        self._wake.set()

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _require_existing_lock(self) -> None:
        # flock/msvcrt locks conflict between separate handles, so a probe that
        # succeeds proves nobody (including this process) holds the lock.
        try:
            probe = self.datadir.acquire_lock()
        except AlreadyRunning:
            return
        probe.release()
        raise RuntimeError("data directory lock must be held before starting worker")

    def _run(self) -> None:
        conn = open_db(self.db_path)
        try:
            while not self._stop.is_set():
                self._cancel.clear()
                processed = self._process_next(conn)
                if processed:
                    continue
                self._wake.wait(self.poll_interval_s)
                self._wake.clear()
        finally:
            conn.close()
            with self._process_lock:
                if self.__class__._process_worker is self:
                    self.__class__._process_worker = None

    def _process_next(self, conn) -> bool:
        claimed = repo.claim_next_queued(conn, self.now())
        if claimed is None:
            return False

        started_at = time.monotonic()
        try:
            outcome = self._process_claimed(conn, claimed)
        except OcrCancelled:
            if self._stop.is_set():
                return True
            outcome = self._processing_error(claimed, "OcrCancelled")
        except Exception as exc:  # noqa: BLE001 - worker must survive bad documents.
            # Class name only: the message may contain document text.
            class_name = type(exc).__name__
            LOGGER.warning("processing %s raised %s", claimed["result_id"], class_name)
            outcome = self._processing_error(claimed, class_name)

        if self._stop.is_set():
            return True

        committed = repo.commit_worker_outcome(
            conn,
            claimed["result_id"],
            claimed["revision"],
            claimed["job"],
            outcome,
        )
        if not committed:
            return True

        try:
            self._archive_document(conn, claimed["document_id"])
        except Exception as exc:  # noqa: BLE001 - recovery repairs a half-archived file.
            LOGGER.warning(
                "archiving %s failed: %s", claimed["result_id"], type(exc).__name__
            )
        LOGGER.info(
            "result_id=%s source_file=%s status=%s elapsed_s=%.2f",
            claimed["result_id"],
            claimed.get("source_file"),
            outcome["status"],
            time.monotonic() - started_at,
        )
        return True

    def _process_claimed(self, conn, claimed: dict) -> dict:
        batch = repo.get_batch(conn, claimed["batch_id"])
        if batch is None:
            raise LookupError("batch not found")
        template_ref = batch.get("template") or {}
        template = repo.get_template(
            conn,
            template_ref.get("id"),
            template_ref.get("version"),
        )
        if template is None:
            raise LookupError("template not found")
        return process_document(
            conn,
            self.datadir.root,
            claimed,
            template,
            llm_client=self.llm_client,
            model=self.settings.model,
            limits=self.limits,
            timeout_s=self.settings.document_timeout_s,
            ocr_engine=self.ocr_engine,
            cancel=self._cancel,
            now=self.now,
        )

    def _processing_error(self, claimed: dict, class_name: str) -> dict:
        return {
            **claimed,
            "status": "failed",
            "extracted": None,
            "issues": [],
            "error": {"code": PROCESSING_ERROR, "detail": class_name},
            "llm": {"model": self.settings.model, "prompt_version": PROMPT_VERSION},
            "updated_at": self.now(),
        }

    def _archive_document(self, conn, document_id: str) -> None:
        document = repo.get_document(conn, document_id)
        if document is None:
            raise LookupError("document not found")
        source = self.datadir.resolve(document["path"])
        if source.parent != self.datadir.process:
            return
        if not source.exists():
            return
        destination = self.datadir.archive / source.name
        os.replace(source, destination)
        repo.update_document_path(conn, document_id, self.datadir.rel(destination))
