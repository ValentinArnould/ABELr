"""Shared helpers for the MCP tools — bridge to the `job_queue`.

The request→job→result round-trip already exists (`job_queue.submit()` +
`job_queue.wait_result()`). `wait_result` **blocks** on a `threading.Event`;
called inline on the MCP server's async loop it would freeze the session
manager and every concurrent call. So it's offloaded to a worker thread
via `anyio.to_thread.run_sync` — the async analogue of the GUI's `JobWorker`
(QThread).
"""

from __future__ import annotations

from typing import Any, Optional

import anyio
from mcp.server.fastmcp.exceptions import ToolError

from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType


def require_bridge() -> None:
    """Fails fast (<1 s) if the plugin bridge hasn't polled recently.

    Avoids a 30-60 s timeout when Lightroom/the plugin are closed: the
    heartbeat (`bridge_connected`) is refreshed on every GET /jobs/pending.
    """
    if not job_queue.bridge_connected():
        raise ToolError(
            "Lightroom bridge not connected. Make sure Lightroom Classic is open "
            "with the ABELr plugin loaded and connected (menu «Start / "
            "connect the application»), and that the App is running (python -m app.main)."
        )


async def run_job(
    job_type: JobType,
    payload: Optional[dict[str, Any]] = None,
    timeout: float = 30.0,
) -> JobResult:
    """Submits a job, waits for its result on a worker thread, returns the `JobResult`.

    Raises `ToolError` on: saturated queue (plugin disconnected), timeout (no
    response from the plugin), or an error reported by the plugin (`status != 'ok'`).
    """
    def _blocking() -> Optional[JobResult]:
        try:
            job_id = job_queue.submit(job_type, payload)
        except RuntimeError as exc:  # saturated queue (_MAX_PENDING) -> inactive bridge
            raise ToolError(str(exc)) from exc
        return job_queue.wait_result(job_id, timeout)

    result = await anyio.to_thread.run_sync(_blocking)
    if result is None:
        raise ToolError(
            f"Timeout: the Lightroom plugin did not respond to job "
            f"'{job_type.value}' within {timeout:g} s."
        )
    if result.status != "ok":
        raise ToolError(
            result.error or f"Job '{job_type.value}' failed on the Lightroom side."
        )
    return result
