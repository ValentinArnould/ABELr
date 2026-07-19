"""Thread-safe job queue — bridge between the GUI thread and the FastAPI server thread.

The Lr plugin is ALWAYS the client: it fetches jobs (GET /jobs/pending) and
submits results (POST /jobs/{id}/result). The GUI produces jobs and waits for
their results. Since FastAPI (uvicorn) runs in a thread separate from the GUI,
access is protected by a Lock and synchronization goes through threading.Event.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any, Optional

from .models import Job, JobResult, JobStatus, JobType


class _JobEntry:
    """Internal state of a job + completion event."""

    def __init__(self, job: Job) -> None:
        self.job = job
        self.status = JobStatus.PENDING
        self.result: Optional[JobResult] = None
        self.created_at = time.time()
        self.done_event = threading.Event()


class JobQueue:
    """Thread-safe FIFO queue with blocking wait for the result."""

    # Beyond this delay, an entry that's never been picked up (worker timed
    # out, plugin died before POSTing) is considered orphaned and evicted →
    # bounds RAM usage. 900s: above the longest legitimate worker timeout
    # (render_probe on a large selection), so we never evict a job that's
    # still awaited.
    _ENTRY_TTL = 900.0  # seconds
    # Hard cap on the queue: beyond this, submit() refuses (plugin
    # disconnected + producer looping = RAM leak otherwise).
    _MAX_PENDING = 100

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[str] = deque()
        self._jobs: dict[str, _JobEntry] = {}
        # Timestamp of the plugin's last GET /jobs/pending = bridge heartbeat.
        # As long as the bridge is listening, it polls every 300ms.
        self._last_poll_at: Optional[float] = None

    def _prune_locked(self, now: float) -> None:
        """Evicts orphaned entries that are too old. Must be called under `_lock`.

        Fetched jobs are already removed by `wait_result`; this only sweeps
        orphans (never consumed) to prevent `_jobs` from growing without
        bound (RAM leak if the app runs a long time with many jobs).
        """
        stale = [
            jid for jid, e in self._jobs.items()
            if now - e.created_at > self._ENTRY_TTL
        ]
        for jid in stale:
            self._jobs.pop(jid, None)
        if stale:
            # Also strip the now-ghost ids from the queue (otherwise they'd
            # count toward the _MAX_PENDING cap even though next_pending
            # would just skip them).
            self._pending = deque(jid for jid in self._pending if jid in self._jobs)

    # ------------------------------------------------------------------ #
    # Producer side (GUI)
    # ------------------------------------------------------------------ #
    def submit(self, job_type: JobType, payload: Optional[dict[str, Any]] = None) -> str:
        """Creates a job, pushes it onto the queue, returns its job_id.

        Raises RuntimeError if the queue exceeds `_MAX_PENDING` (plugin
        disconnected while producers keep submitting in a loop) — better
        than a RAM leak.
        """
        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id, type=job_type, payload=payload or {})
        entry = _JobEntry(job)
        with self._lock:
            self._prune_locked(time.time())
            if len(self._pending) >= self._MAX_PENDING:
                raise RuntimeError(
                    f"Job queue saturated ({self._MAX_PENDING} pending) — "
                    "the Lightroom plugin is no longer picking up jobs (bridge inactive?)."
                )
            self._jobs[job_id] = entry
            self._pending.append(job_id)
        return job_id

    def wait_result(self, job_id: str, timeout: float = 30.0) -> Optional[JobResult]:
        """Blocks until the result or the timeout. None on timeout.

        Must be called from a worker, never from the main Qt thread
        (otherwise the GUI freezes). The fetched result is immediately
        **removed** from `_jobs` (consumed → frees RAM).
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
    # Plugin side (via FastAPI endpoints)
    # ------------------------------------------------------------------ #
    def mark_poll(self) -> None:
        """The plugin just polled: refreshes the bridge heartbeat."""
        with self._lock:
            self._last_poll_at = time.time()

    def seconds_since_poll(self) -> Optional[float]:
        """Seconds since the plugin's last poll. None if never seen."""
        with self._lock:
            if self._last_poll_at is None:
                return None
            return time.time() - self._last_poll_at

    def bridge_connected(self, threshold: float = 5.0) -> bool:
        """True if the plugin polled within the last `threshold` seconds."""
        since = self.seconds_since_poll()
        return since is not None and since <= threshold

    def next_pending(self) -> Optional[Job]:
        """Returns the next pending job and moves it to IN_PROGRESS. None if empty."""
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
        """Records the result submitted by the plugin. False if the job is unknown."""
        with self._lock:
            entry = self._jobs.get(result.job_id)
            if entry is None:
                return False
            entry.result = result
            entry.status = (
                JobStatus.DONE if result.status == "ok" else JobStatus.FAILED
            )
            entry.done_event.set()  # under the lock: state + signal published atomically
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


# Shared instance (application singleton).
job_queue = JobQueue()
