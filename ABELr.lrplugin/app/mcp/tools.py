"""Helpers partagés des outils MCP — pont vers la `job_queue`.

Le round-trip requête→job→résultat existe déjà (`job_queue.submit()` +
`job_queue.wait_result()`). `wait_result` **bloque** sur un `threading.Event` ;
appelé en ligne sur la boucle async du serveur MCP il gèlerait le session
manager et tous les appels concurrents. On l'offloade donc sur un thread worker
via `anyio.to_thread.run_sync` — l'analogue async du `JobWorker` (QThread) du GUI.
"""

from __future__ import annotations

from typing import Any, Optional

import anyio
from mcp.server.fastmcp.exceptions import ToolError

from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType


def require_bridge() -> None:
    """Échoue vite (<1 s) si le pont plugin n'a pas pollé récemment.

    Évite un timeout de 30-60 s quand Lightroom/le plugin sont fermés : le
    heartbeat (`bridge_connected`) est rafraîchi à chaque GET /jobs/pending.
    """
    if not job_queue.bridge_connected():
        raise ToolError(
            "Pont Lightroom non connecté. Vérifie que Lightroom Classic est ouvert "
            "avec le plugin ABELr chargé et connecté (menu « Démarrer / "
            "connecter l'application »), et que l'App tourne (python -m app.main)."
        )


async def run_job(
    job_type: JobType,
    payload: Optional[dict[str, Any]] = None,
    timeout: float = 30.0,
) -> JobResult:
    """Soumet un job, attend son résultat sur un thread worker, retourne le `JobResult`.

    Lève `ToolError` sur : file saturée (plugin déconnecté), timeout (pas de
    réponse du plugin), ou erreur remontée par le plugin (`status != 'ok'`).
    """
    def _blocking() -> Optional[JobResult]:
        try:
            job_id = job_queue.submit(job_type, payload)
        except RuntimeError as exc:  # file saturée (_MAX_PENDING) → pont inactif
            raise ToolError(str(exc)) from exc
        return job_queue.wait_result(job_id, timeout)

    result = await anyio.to_thread.run_sync(_blocking)
    if result is None:
        raise ToolError(
            f"Timeout : le plugin Lightroom n'a pas répondu au job "
            f"'{job_type.value}' en {timeout:g} s."
        )
    if result.status != "ok":
        raise ToolError(
            result.error or f"Le job '{job_type.value}' a échoué côté Lightroom."
        )
    return result
