"""Queue de jobs thread-safe — pont entre le thread GUI et le thread serveur FastAPI.

Le plugin Lr est TOUJOURS client : il récupère les jobs (GET /jobs/pending) et
soumet les résultats (POST /jobs/{id}/result). Le GUI produit les jobs et attend
leurs résultats. Comme FastAPI (uvicorn) tourne dans un thread séparé du GUI,
l'accès est protégé par un Lock et la synchronisation se fait via threading.Event.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any, Optional

from .models import Job, JobResult, JobStatus, JobType


class _JobEntry:
    """État interne d'un job + événement de complétion."""

    def __init__(self, job: Job) -> None:
        self.job = job
        self.status = JobStatus.PENDING
        self.result: Optional[JobResult] = None
        self.created_at = time.time()
        self.done_event = threading.Event()


class JobQueue:
    """Queue FIFO thread-safe avec attente bloquante du résultat."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[str] = deque()
        self._jobs: dict[str, _JobEntry] = {}
        # Horodatage du dernier GET /jobs/pending du plugin = battement de cœur
        # du pont. Tant que le pont écoute, il poll toutes les 300ms.
        self._last_poll_at: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Côté producteur (GUI)
    # ------------------------------------------------------------------ #
    def submit(self, job_type: JobType, payload: Optional[dict[str, Any]] = None) -> str:
        """Crée un job, le pousse dans la queue, retourne son job_id."""
        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id, type=job_type, payload=payload or {})
        entry = _JobEntry(job)
        with self._lock:
            self._jobs[job_id] = entry
            self._pending.append(job_id)
        return job_id

    def wait_result(self, job_id: str, timeout: float = 30.0) -> Optional[JobResult]:
        """Bloque jusqu'au résultat ou au timeout. None si timeout.

        À appeler depuis un worker, jamais depuis le thread Qt principal
        (sinon le GUI gèle).
        """
        with self._lock:
            entry = self._jobs.get(job_id)
        if entry is None:
            return None
        if entry.done_event.wait(timeout):
            return entry.result
        return None

    # ------------------------------------------------------------------ #
    # Côté plugin (via endpoints FastAPI)
    # ------------------------------------------------------------------ #
    def mark_poll(self) -> None:
        """Le plugin vient de poller : rafraîchit le battement de cœur du pont."""
        with self._lock:
            self._last_poll_at = time.time()

    def seconds_since_poll(self) -> Optional[float]:
        """Secondes depuis le dernier poll plugin. None si jamais vu."""
        with self._lock:
            if self._last_poll_at is None:
                return None
            return time.time() - self._last_poll_at

    def bridge_connected(self, threshold: float = 5.0) -> bool:
        """True si le plugin a pollé dans les `threshold` dernières secondes."""
        since = self.seconds_since_poll()
        return since is not None and since <= threshold

    def next_pending(self) -> Optional[Job]:
        """Retourne le prochain job en attente et le passe IN_PROGRESS. None si vide."""
        with self._lock:
            while self._pending:
                job_id = self._pending.popleft()
                entry = self._jobs.get(job_id)
                if entry is None or entry.status != JobStatus.PENDING:
                    continue
                entry.status = JobStatus.IN_PROGRESS
                return entry.job
        return None

    def submit_result(self, result: JobResult) -> bool:
        """Enregistre le résultat soumis par le plugin. False si job inconnu."""
        with self._lock:
            entry = self._jobs.get(result.job_id)
            if entry is None:
                return False
            entry.result = result
            entry.status = (
                JobStatus.DONE if result.status == "ok" else JobStatus.FAILED
            )
        entry.done_event.set()
        return True

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def status_of(self, job_id: str) -> Optional[JobStatus]:
        with self._lock:
            entry = self._jobs.get(job_id)
            return entry.status if entry else None


# Instance partagée (singleton applicatif).
job_queue = JobQueue()
