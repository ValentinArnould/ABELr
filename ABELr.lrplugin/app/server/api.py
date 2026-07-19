"""FastAPI routes — localhost server that the Lr plugin polls.

The plugin is the client: it does GET /jobs/pending then POST /jobs/{id}/result.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Response, status

from ..mcp.server import mcp
from .job_queue import job_queue
from .models import JobResult

# Creates the MCP ASGI app **before** the lifespan: `streamable_http_app()`
# instantiates the session manager (lazy creation), which the lifespan then starts.
_mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Forwards the MCP session manager's lifespan — REQUIRED when mounting the
    MCP server on a host app, otherwise RuntimeError "Task group is not initialized"."""
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="ABELr", version="0.1.0", lifespan=_lifespan)

_started_at = time.time()


@app.get("/health")
def health() -> dict:
    """Healthcheck — the plugin checks at startup that the App is running."""
    return {"status": "ok", "uptime_s": round(time.time() - _started_at, 1)}


@app.get("/status")
def status_() -> dict:
    """Overall App state."""
    return {
        "status": "ready",
        "pending_jobs": job_queue.pending_count(),
        "bridge_connected": job_queue.bridge_connected(),
        "last_poll_s_ago": job_queue.seconds_since_poll(),
    }


@app.get("/bridge")
def bridge() -> dict:
    """Plugin bridge state: has it polled recently?

    The Lr plugin polls /jobs/pending every 300ms as long as its listen loop
    is running. This heartbeat is how we know whether the bridge is still active.
    """
    return {
        "connected": job_queue.bridge_connected(),
        "last_poll_s_ago": job_queue.seconds_since_poll(),
    }


@app.get("/jobs/pending")
def jobs_pending() -> Response:
    """The plugin fetches the next job. 204 (no body — RFC) if there is none."""
    job_queue.mark_poll()  # bridge heartbeat
    job = job_queue.next_pending()
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # Serialization AFTER the pop (the job is already IN_PROGRESS): on failure,
    # mark the job FAILED and free the worker waiting on it, instead of letting
    # it hang until the 900s TTL (Fable 5 review B-02).
    try:
        payload = job.model_dump_json()
    except Exception as exc:
        job_queue.submit_result(JobResult(
            job_id=job.job_id, status="error",
            error=f"payload not serializable server-side: {exc}",
        ))
        raise HTTPException(status_code=500, detail="payload not serializable")
    return Response(content=payload, media_type="application/json")


@app.post("/shutdown")
def shutdown() -> dict:
    """Stops the process (GUI + server) — used by the plugin for "Restart".

    Terminates the whole Python process after a short delay, long enough to
    return the response. The plugin then launches a fresh process.
    """
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"status": "shutting_down"}


@app.post("/jobs/{job_id}/result")
def jobs_result(job_id: str, result: JobResult) -> dict:
    """The plugin submits a job's result."""
    if result.job_id != job_id:
        raise HTTPException(
            status_code=400, detail="path job_id != body job_id"
        )
    if not job_queue.submit_result(result):
        raise HTTPException(status_code=404, detail="unknown job_id")
    return {"status": "accepted"}


# MCP server mounted last (after the routes): URL http://127.0.0.1:5000/mcp.
# The MCP tools share the same `job_queue` as the routes above.
app.mount("/mcp", _mcp_app)
