"""Instance FastMCP + outils MCP Lightroom (Phase 1 : les 6 jobs existants).

Chaque outil = `require_bridge()` (préflight) puis `run_job(...)` (submit +
wait_result offloadés). Les docstrings des outils sont ce que Claude voit :
elles décrivent l'usage et les conventions (clés develop PascalCase, etc.).

Monté sur FastAPI dans `app/server/api.py` :
    http_app = mcp.streamable_http_app()   # crée le session manager (lazy)
    app.mount("/mcp", http_app)            # → http://127.0.0.1:5000/mcp
avec forward du lifespan (`async with mcp.session_manager.run()`).
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ..server.job_queue import job_queue
from ..server.models import JobType, PhotoResult, ThumbnailResult
from .tools import require_bridge, run_job

# stateless_http : pas de store de session à fuiter/planter (un seul client local).
# json_response : POST renvoie de l'application/json (pas un flux SSE) → transport simple.
# streamable_http_path='/' : monté à '/mcp', l'URL effective est exactement /mcp
# (sinon /mcp/mcp + redirections 307).
mcp = FastMCP(
    "Lightroom Classic (ABELr)",
    instructions=(
        "Pilote Adobe Lightroom Classic via le plugin ABELr. Prérequis : "
        "Lightroom ouvert + plugin connecté + App lancée. Récupère d'abord les "
        "photo_id via get_selected_photos, puis agis dessus (apply_adjustments…). "
        "Les clés de réglages develop sont des noms SDK Lr en PascalCase."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


# --------------------------------------------------------------------------- #
# Helpers de sérialisation (JobResult → dict compact pour la sortie MCP)
# --------------------------------------------------------------------------- #
def _photo_to_dict(p: PhotoResult, include_develop: bool) -> dict[str, Any]:
    d: dict[str, Any] = {
        "photo_id": p.photo_id,
        "path": p.path,
        "exif": p.exif.model_dump(exclude_none=True) if p.exif else {},
    }
    if include_develop:
        d["current_develop"] = p.current_develop
    return d


def _thumb_to_dict(t: ThumbnailResult) -> dict[str, Any]:
    d: dict[str, Any] = {"photo_id": t.photo_id, "thumbnail_path": t.thumbnail_path}
    if t.error:
        d["error"] = t.error
    if t.asshot_temp is not None:
        d["asshot_temp"] = t.asshot_temp
    if t.asshot_tint is not None:
        d["asshot_tint"] = t.asshot_tint
    if t.restore_error:
        d["restore_error"] = t.restore_error
    return d


# --------------------------------------------------------------------------- #
# Outils — introspection
# --------------------------------------------------------------------------- #
@mcp.tool()
async def bridge_status() -> dict:
    """État du pont plugin Lightroom (sans déclencher de job).

    Retourne {connected, last_poll_s_ago, pending_jobs}. `connected=false` ⇒
    Lightroom/plugin fermés ou App non lancée : les autres outils échoueront.
    """
    return {
        "connected": job_queue.bridge_connected(),
        "last_poll_s_ago": job_queue.seconds_since_poll(),
        "pending_jobs": job_queue.pending_count(),
    }


@mcp.tool()
async def ping() -> dict:
    """Ping le plugin (popup « Hello World » dans Lightroom). Vérifie le round-trip."""
    require_bridge()
    await run_job(JobType.TEST, None, timeout=15.0)
    return {"ok": True, "message": "Le plugin Lightroom a répondu."}


# --------------------------------------------------------------------------- #
# Outils — lecture
# --------------------------------------------------------------------------- #
@mcp.tool()
async def get_selected_photos(include_develop: bool = False, timeout: float = 30.0) -> dict:
    """Photos actuellement sélectionnées dans Lightroom (id, chemin, EXIF).

    include_develop=True ajoute les ~90 réglages develop courants par photo
    (PascalCase SDK) — volumineux : à laisser sur False pour de grandes sélections
    (limite de sortie MCP). Les photo_id servent aux autres outils (apply_adjustments…).
    """
    require_bridge()
    result = await run_job(JobType.GET_SELECTED_PHOTOS, None, timeout)
    photos = [_photo_to_dict(p, include_develop) for p in result.photos]
    return {"count": len(photos), "photos": photos}


@mcp.tool()
async def get_catalog_photos(include_develop: bool = False, timeout: float = 120.0) -> dict:
    """TOUTES les photos du catalogue actif (id, chemin, EXIF).

    Potentiellement énorme : évite include_develop=True ici. Préfère
    get_selected_photos quand tu ne veux qu'une partie du catalogue.
    """
    require_bridge()
    result = await run_job(JobType.GET_CATALOG_PHOTOS, None, timeout)
    photos = [_photo_to_dict(p, include_develop) for p in result.photos]
    return {"count": len(photos), "photos": photos}


@mcp.tool()
async def get_thumbnails(
    photo_ids: Optional[list[str]] = None,
    width: int = 512,
    height: int = 512,
    timeout: float = 120.0,
) -> dict:
    """Rend des miniatures JPEG de la sélection (ou de `photo_ids` précis).

    Retourne des **chemins de fichiers** locaux (jamais des données base64). Sans
    photo_ids, rend la sélection courante de Lightroom.
    """
    require_bridge()
    payload: dict[str, Any] = {"width": width, "height": height}
    if photo_ids:
        payload["photo_ids"] = photo_ids
    result = await run_job(JobType.GET_THUMBNAILS, payload, timeout)
    thumbs = [_thumb_to_dict(t) for t in result.thumbnails]
    return {"count": len(thumbs), "thumbnails": thumbs}


@mcp.tool()
async def render_probe(
    adjustments: list[dict[str, Any]],
    settle: float = 0.6,
    timeout: Optional[float] = None,
) -> dict:
    """Aperçu d'essai : applique des réglages TEMPORAIRES, rend une miniature, RESTAURE.

    Ne modifie pas durablement les photos (restaure l'état d'origine). `adjustments`
    = [{"photo_id": "...", "develop": {<clé PascalCase>: valeur, ...}}, ...].
    Utile pour prévisualiser un réglage avant apply_adjustments. Relit aussi
    Temperature/Tint après l'apply (valeur numérique de l'As Shot si
    WhiteBalance='As Shot'). `restore_error` non vide = la photo est restée en état
    modifié (à signaler).
    """
    require_bridge()
    if not adjustments:
        raise ToolError("Aucun ajustement fourni.")
    if timeout is None:
        timeout = max(30.0, 5.0 * len(adjustments))
    payload = {"adjustments": adjustments, "settle": settle}
    result = await run_job(JobType.RENDER_PROBE, payload, timeout)
    thumbs = [_thumb_to_dict(t) for t in result.thumbnails]
    return {"count": len(thumbs), "thumbnails": thumbs}


# --------------------------------------------------------------------------- #
# Outils — écriture
# --------------------------------------------------------------------------- #
@mcp.tool()
async def apply_adjustments(
    adjustments: list[dict[str, Any]],
    timeout: Optional[float] = None,
) -> dict:
    """Applique DURABLEMENT des réglages develop à des photos précises.

    `adjustments` = [{"photo_id": "<id>", "develop": {<clé>: valeur, ...}}, ...].
    Récupère les photo_id via get_selected_photos.

    Conventions clés develop (SDK Lr, PhotoProcess PV2012) :
    - Noms en PascalCase avec suffixe 2012 pour le tonal : Exposure2012,
      Contrast2012, Highlights2012, Shadows2012, Whites2012, Blacks2012.
    - Balance des blancs : poser WhiteBalance='Custom' pour que Temperature et Tint
      prennent effet (ex. {"WhiteBalance": "Custom", "Temperature": 5650, "Tint": -5}).
    - HSL/couleur/courbes : noms SDK exacts (ex. SaturationAdjustmentRed,
      LuminanceAdjustmentBlue…).

    Retourne {applied, matched, total} ; `warnings` si un apply partiel a échoué
    sur certaines photos.
    """
    require_bridge()
    if not adjustments:
        raise ToolError("Aucun ajustement fourni.")
    if timeout is None:
        timeout = max(60.0, 2.0 * len(adjustments))
    payload = {"adjustments": adjustments}
    result = await run_job(JobType.APPLY_ADJUSTMENTS, payload, timeout)
    out: dict[str, Any] = {
        "applied": result.applied,
        "matched": result.matched,
        "total": result.total,
    }
    if result.errors_summary:  # apply partiel — ne pas avaler (revue Fable 5 L-04)
        out["warnings"] = result.errors_summary
    return out


# --------------------------------------------------------------------------- #
# Helpers Phase 2
# --------------------------------------------------------------------------- #
def _applied_out(result: Any) -> dict[str, Any]:
    """Sortie standard des jobs batch Phase 2 : {applied, total, [warnings]}."""
    out: dict[str, Any] = {"applied": result.applied, "total": result.total}
    if result.errors_summary:
        out["warnings"] = result.errors_summary
    return out


# --------------------------------------------------------------------------- #
# Phase 2 — notes / flags / labels
# --------------------------------------------------------------------------- #
@mcp.tool()
async def set_rating(photo_ids: list[str], rating: int, timeout: float = 60.0) -> dict:
    """Note (étoiles) 0 à 5 pour les photos données (même note pour toutes).

    rating=0 efface les étoiles. Récupère les photo_id via get_selected_photos.
    """
    require_bridge()
    if not photo_ids:
        raise ToolError("Aucun photo_id fourni.")
    if not (0 <= rating <= 5):
        raise ToolError("rating doit être entre 0 et 5.")
    result = await run_job(
        JobType.SET_RATING, {"photo_ids": photo_ids, "rating": rating}, timeout
    )
    return _applied_out(result)


@mcp.tool()
async def set_flag_color(
    photo_ids: list[str],
    flag: Optional[str] = None,
    color: Optional[str] = None,
    timeout: float = 60.0,
) -> dict:
    """Drapeau (pick/reject) et/ou label couleur pour les photos données.

    flag : "pick" | "reject" | "none" (ou None pour ne pas toucher).
    color : "red" | "yellow" | "green" | "blue" | "purple" | "none" (ou None).
    Au moins l'un des deux doit être fourni.
    """
    require_bridge()
    if not photo_ids:
        raise ToolError("Aucun photo_id fourni.")
    if flag is None and color is None:
        raise ToolError("Fournis au moins flag ou color.")
    flags = {"pick", "reject", "none"}
    colors = {"red", "yellow", "green", "blue", "purple", "none"}
    if flag is not None and flag not in flags:
        raise ToolError(f"flag invalide : {flag}. Attendu : {sorted(flags)}.")
    if color is not None and color not in colors:
        raise ToolError(f"color invalide : {color}. Attendu : {sorted(colors)}.")
    payload: dict[str, Any] = {"photo_ids": photo_ids}
    if flag is not None:
        payload["flag"] = flag
    if color is not None:
        payload["color"] = color
    result = await run_job(JobType.SET_FLAG_COLOR, payload, timeout)
    return _applied_out(result)


@mcp.tool()
async def set_keywords(
    photo_ids: list[str],
    add: Optional[list[str]] = None,
    remove: Optional[list[str]] = None,
    timeout: float = 60.0,
) -> dict:
    """Ajoute et/ou retire des mots-clés (par nom) sur les photos données.

    `add` : noms de mots-clés à ajouter (créés s'ils n'existent pas, au niveau
    racine). `remove` : noms à retirer. Au moins l'un des deux non vide.
    """
    require_bridge()
    if not photo_ids:
        raise ToolError("Aucun photo_id fourni.")
    if not add and not remove:
        raise ToolError("Fournis au moins un mot-clé dans add ou remove.")
    payload = {"photo_ids": photo_ids, "add": add or [], "remove": remove or []}
    result = await run_job(JobType.SET_KEYWORDS, payload, timeout)
    return _applied_out(result)


# --------------------------------------------------------------------------- #
# Phase 2 — collections
# --------------------------------------------------------------------------- #
@mcp.tool()
async def list_collections(timeout: float = 30.0) -> dict:
    """Arbre des collections et ensembles de collections du catalogue.

    Retourne {collections: [ {name, id, kind: "collection"|"set", photo_count?,
    children: [...] } ]}. Utilise les id (ou noms) avec add_to_collection.
    """
    require_bridge()
    result = await run_job(JobType.LIST_COLLECTIONS, None, timeout)
    return result.data or {"collections": []}


@mcp.tool()
async def create_collection(
    name: str, parent: Optional[str] = None, timeout: float = 30.0
) -> dict:
    """Crée une collection (retourne l'existante si déjà présente).

    `parent` : nom ou id d'un ensemble de collections parent (None = racine).
    Retourne {name, id, created}.
    """
    require_bridge()
    if not name:
        raise ToolError("Nom de collection vide.")
    payload: dict[str, Any] = {"name": name}
    if parent is not None:
        payload["parent"] = parent
    result = await run_job(JobType.CREATE_COLLECTION, payload, timeout)
    return result.data or {}


@mcp.tool()
async def add_to_collection(
    collection: str, photo_ids: list[str], timeout: float = 60.0
) -> dict:
    """Ajoute des photos à une collection (identifiée par id ou nom).

    Récupère l'id/nom via list_collections, les photo_id via get_selected_photos.
    Retourne {applied, total, [warnings]}.
    """
    require_bridge()
    if not collection:
        raise ToolError("Collection non spécifiée.")
    if not photo_ids:
        raise ToolError("Aucun photo_id fourni.")
    result = await run_job(
        JobType.ADD_TO_COLLECTION,
        {"collection": collection, "photo_ids": photo_ids},
        timeout,
    )
    return _applied_out(result)


# --------------------------------------------------------------------------- #
# Phase 2 — presets develop
# --------------------------------------------------------------------------- #
@mcp.tool()
async def list_develop_presets(timeout: float = 30.0) -> dict:
    """Presets develop disponibles dans Lightroom.

    Retourne {presets: [ {name, uuid, folder} ]}. Applique avec apply_develop_preset.
    """
    require_bridge()
    result = await run_job(JobType.LIST_DEVELOP_PRESETS, None, timeout)
    return result.data or {"presets": []}


@mcp.tool()
async def apply_develop_preset(
    photo_ids: list[str], preset: str, timeout: Optional[float] = None
) -> dict:
    """Applique un preset develop (par uuid ou nom) aux photos données.

    Récupère uuid/nom via list_develop_presets. Préfère l'uuid (les noms peuvent
    ne pas être uniques). Retourne {applied, total, [warnings]}.
    """
    require_bridge()
    if not photo_ids:
        raise ToolError("Aucun photo_id fourni.")
    if not preset:
        raise ToolError("Preset non spécifié.")
    if timeout is None:
        timeout = max(60.0, 2.0 * len(photo_ids))
    result = await run_job(
        JobType.APPLY_DEVELOP_PRESET,
        {"photo_ids": photo_ids, "preset": preset},
        timeout,
    )
    return _applied_out(result)
