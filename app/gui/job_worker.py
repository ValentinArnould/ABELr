"""Worker Qt — soumet un job et attend son résultat hors du thread GUI.

wait_result() bloque : il ne doit JAMAIS tourner sur le thread Qt principal,
sinon la fenêtre gèle. On l'exécute donc dans un QThread.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QThread, Signal

from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType


class JobWorker(QThread):
    """Soumet un job, attend le résultat, émet `finished_result`."""

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

    def run(self) -> None:  # exécuté dans le thread du worker
        try:
            job_id = job_queue.submit(self._job_type, self._payload)
            result: Optional[JobResult] = job_queue.wait_result(job_id, self._timeout)
        except Exception as exc:  # remonter proprement vers le GUI
            self.failed.emit(str(exc))
            return
        if result is None:
            self.failed.emit("Timeout — aucune réponse du plugin Lr.")
            return
        self.finished_result.emit(result)
