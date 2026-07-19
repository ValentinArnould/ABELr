"""Qt worker — submits a job and waits for its result off the GUI thread.

wait_result() blocks: it must NEVER run on the main Qt thread, or the
window freezes. So it runs inside a QThread.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QThread, Signal

from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType


class JobWorker(QThread):
    """Submits a job, waits for the result, emits `finished_result`."""

    finished_result = Signal(object)  # JobResult | None
    failed = Signal(str)

    def __init__(
        self,
        job_type: JobType,
        payload: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__()
        self._job_type = job_type
        self._payload = payload
        self._timeout = timeout

    def run(self) -> None:  # runs on the worker thread
        try:
            job_id = job_queue.submit(self._job_type, self._payload)
            result: Optional[JobResult] = job_queue.wait_result(job_id, self._timeout)
        except Exception as exc:  # propagate cleanly to the GUI
            self.failed.emit(str(exc))
            return
        if result is None:
            self.failed.emit("Timeout — no response from the Lr plugin.")
            return
        self.finished_result.emit(result)
