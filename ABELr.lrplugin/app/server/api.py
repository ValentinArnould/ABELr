"""Routes FastAPI — serveur localhost que le plugin Lr interroge en polling.

Le plugin est client : il fait GET /jobs/pending puis POST /jobs/{id}/result.
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

# Crée l'app ASGI MCP **avant** le lifespan : `streamable_http_app()` instancie
# le session manager (création lazy) que le lifespan démarre ensuite.
_mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Forward le lifespan du session manager MCP — REQUIS quand on monte le
    serveur MCP sur une app hôte, sinon RuntimeError « Task group is not initialized »."""
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="ABELr", version="0.1.0", lifespan=_lifespan)

_started_at = time.time()


@app.get("/health")
def health() -> dict:
    """Healthcheck — le plugin vérifie au démarrage que l'App tourne."""
    return {"status": "ok", "uptime_s": round(time.time() - _started_at, 1)}


@app.get("/status")
def status_() -> dict:
    """État global de l'App."""
    return {
        "status": "ready",
        "pending_jobs": job_queue.pending_count(),
        "bridge_connected": job_queue.bridge_connected(),
        "last_poll_s_ago": job_queue.seconds_since_poll(),
    }


@app.get("/bridge")
def bridge() -> dict:
    """État du pont plugin : a-t-il pollé récemment ?

    Le plugin Lr poll /jobs/pending toutes les 300ms tant que sa boucle d'écoute
    tourne. Ce battement de cœur permet de savoir si le pont est encore actif.
    """
    return {
        "connected": job_queue.bridge_connected(),
        "last_poll_s_ago": job_queue.seconds_since_poll(),
    }


@app.get("/jobs/pending")
def jobs_pending() -> Response:
    """Le plugin récupère le prochain job. 204 (sans corps — RFC) si aucun job."""
    job_queue.mark_poll()  # battement de cœur du pont
    job = job_queue.next_pending()
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # Sérialisation APRÈS le pop (le job est déjà IN_PROGRESS) : en cas d'échec,
    # marquer le job FAILED et libérer le worker qui l'attend, au lieu de le
    # laisser pendre jusqu'au TTL 900 s (revue Fable 5 B-02).
    try:
        payload = job.model_dump_json()
    except Exception as exc:
        job_queue.submit_result(JobResult(
            job_id=job.job_id, status="error",
            error=f"payload non sérialisable côté serveur : {exc}",
        ))
        raise HTTPException(status_code=500, detail="payload non sérialisable")
    return Response(content=payload, media_type="application/json")


@app.post("/shutdown")
def shutdown() -> dict:
    """Arrêt du process (GUI + serveur) — utilisé par le plugin pour « Relancer ».

    Termine tout le process Python après un court délai, le temps de renvoyer la
    réponse. Le plugin relance ensuite un process neuf.
    """
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"status": "shutting_down"}


@app.post("/jobs/{job_id}/result")
def jobs_result(job_id: str, result: JobResult) -> dict:
    """Le plugin soumet le résultat d'un job."""
    if result.job_id != job_id:
        raise HTTPException(
            status_code=400, detail="job_id du chemin ≠ job_id du corps"
        )
    if not job_queue.submit_result(result):
        raise HTTPException(status_code=404, detail="job_id inconnu")
    return {"status": "accepted"}


# Serveur MCP monté en dernier (après les routes) : URL http://127.0.0.1:5000/mcp.
# Les outils MCP partagent le même `job_queue` que les routes ci-dessus.
app.mount("/mcp", _mcp_app)
