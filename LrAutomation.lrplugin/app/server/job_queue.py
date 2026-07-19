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

    # Au-delà de ce délai, une entrée jamais récupérée (worker timeouté, plugin
    # mort avant de POSTer) est considérée orpheline et évincée → borne la RAM.
    # 900 s : au-dessus du plus long timeout worker légitime (render_probe sur une
    # grosse sélection), pour ne jamais évincer un job encore attendu.
    _ENTRY_TTL = 900.0  # secondes
    # Borne dure de la file d'attente : au-delà, submit() refuse (plugin déconnecté
    # + producteur en boucle = fuite RAM sinon).
    _MAX_PENDING = 100

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[str] = deque()
        self._jobs: dict[str, _JobEntry] = {}
        # Horodatage du dernier GET /jobs/pending du plugin = battement de cœur
        # du pont. Tant que le pont écoute, il poll toutes les 300ms.
        self._last_poll_at: Optional[float] = None

    def _prune_locked(self, now: float) -> None:
        """Évince les entrées orphelines trop vieilles. À appeler sous `_lock`.

        Les jobs récupérés sont déjà retirés par `wait_result` ; ceci ne ramasse
        que les orphelins (jamais consommés) pour empêcher `_jobs` de croître sans
        fin (fuite RAM si l'app tourne longtemps avec beaucoup de jobs).
        """
        stale = [
            jid for jid, e in self._jobs.items()
            if now - e.created_at > self._ENTRY_TTL
        ]
        for jid in stale:
            self._jobs.pop(jid, None)
        if stale:
            # Retire aussi les ids fantômes de la file (sinon ils comptent dans
            # la borne _MAX_PENDING alors que next_pending les sauterait).
            self._pending = deque(jid for jid in self._pending if jid in self._jobs)

    # ------------------------------------------------------------------ #
    # Côté producteur (GUI)
    # ------------------------------------------------------------------ #
    def submit(self, job_type: JobType, payload: Optional[dict[str, Any]] = None) -> str:
        """Crée un job, le pousse dans la queue, retourne son job_id.

        Lève RuntimeError si la file dépasse `_MAX_PENDING` (plugin déconnecté
        pendant que des producteurs soumettent en boucle) — mieux qu'une fuite RAM.
        """
        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id, type=job_type, payload=payload or {})
        entry = _JobEntry(job)
        with self._lock:
            self._prune_locked(time.time())
            if len(self._pending) >= self._MAX_PENDING:
                raise RuntimeError(
                    f"File de jobs saturée ({self._MAX_PENDING} en attente) — "
                    "le plugin Lightroom ne récupère plus les jobs (pont inactif ?)."
                )
            self._jobs[job_id] = entry
            self._pending.append(job_id)
        return job_id

    def wait_result(self, job_id: str, timeout: float = 30.0) -> Optional[JobResult]:
        """Bloque jusqu'au résultat ou au timeout. None si timeout.

        À appeler depuis un worker, jamais depuis le thread Qt principal
        (sinon le GUI gèle). Le résultat récupéré est aussitôt **retiré** de
        `_jobs` (consommé → libère la RAM).
        """
        with self._lock:
            entry = self._jobs.get(job_id)
        if entry is None:
            return None
        if entry.done_event.wait(timeout):
            with self._lock:
                self._jobs.pop(job_id, None)
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
            entry.done_event.set()  # sous le lock : état + signal publiés atomiquement
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
