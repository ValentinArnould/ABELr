"""Rendus neutres d'ancrage (« Neutral Preview ») — worker Qt + fonction réutilisable.

Rend chaque photo dans son **style courant** (profil DCP + tons + Color Grading)
mais avec **WB As Shot**, **Exposure2012=0** et **les 24 curseurs HSL à zéro**,
puis mesure ce rendu neutre (GPU, dual global+sharp) et le cache dans
`NeutralPreviewJPEG`. Les HSL sont neutralisés pour que l'ancre soit indépendante
des corrections HSL appliquées ensuite (sinon chaque Apply HSL invaliderait
`hash_style` → re-probe complet à chaque cycle).

But : ancre déterministe du mode embedded — le delta JPEG boîtier − rendu neutre
donne des réglages **absolus** (idempotents), sans dépendre du rendu courant.

Mécanisme : job plugin `render_probe` (`Thumbnails.fetchProbe` : apply → render →
**restore**), soumis **par lots** (`chunk_size`) pour garder le heartbeat du pont
vivant et borner la fenêtre où les photos sont en état neutre. Clé de fraîcheur :
`hash_style` (cf. `cache.style_hash`) — recalcul seulement si le style change,
pas si Temp/Exposure/HSL bougent.

Garde anti-probe-périmé : si une photo a un `Exposure2012` courant marqué (≥ 0.3)
mais que son ancre « neutre » mesure la même clarté que son dernier aperçu rendu
connu, le probe a probablement servi un aperçu en cache (pas re-rendu) → retry
unique avec un settle long, puis **échec explicite** (une ancre suspecte n'est
JAMAIS cachée — elle empoisonnerait tous les calculs jusqu'au changement de style).

Le probe relit Temperature/Tint APRÈS l'apply de `WhiteBalance='As Shot'` :
c'est la valeur numérique de l'As Shot (cachée en `wb_asshot_temp/tint`), base
d'une correction WB absolue. Cette lecture vérifie de facto l'hypothèse
« applyDevelopSettings{WhiteBalance='As Shot'} réinitialise Temp/Tint ».
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QThread, Signal

from ..core import cache as cachemod, gpu, gpu_jpeg, render_metrics, render_metrics_gpu
from ..server.job_queue import job_queue
from ..server.models import JobType, PhotoResult, ThumbnailResult

_log = logging.getLogger("lr_automation.neutral_preview")

# Réglages du rendu neutre : WB boîtier + exposition à plat + HSL neutralisés,
# le reste du style (profil DCP, tons, Color Grading, crop) intact.
_NEUTRAL_DEVELOP: dict[str, object] = {
    "WhiteBalance": "As Shot",
    "Exposure2012": 0.0,
    **{
        f"{prefix}Adjustment{band}": 0
        for prefix in ("Hue", "Saturation", "Luminance")
        for band in render_metrics.BAND_NAMES
    },
}

# Budget temps par photo (apply + settle + render + restore côté plugin).
_SECONDS_PER_PHOTO = 4.0
_MIN_TIMEOUT = 30.0
# Taille des lots render_probe : le dispatch plugin est synchrone dans pollOnce,
# un gros lot bloquerait le heartbeat plusieurs minutes et élargirait la fenêtre
# où les photos restent en état neutre en cas de crash Lr.
_CHUNK_SIZE = 16
# Délai laissé à Lr pour régénérer l'aperçu après l'apply du probe (secondes) ;
# settle long utilisé au retry si l'ancre semble périmée.
DEFAULT_SETTLE = 0.6
_RETRY_SETTLE = 2.0
# Garde anti-probe-périmé : |Exposure2012| courant à partir duquel l'ancre DOIT
# différer du dernier aperçu connu, et écart L* en dessous duquel elle est suspecte.
_SUSPECT_MIN_EXPO = 0.3
_SUSPECT_MAX_DELTA_L = 2.0


def _probe_chunk(
    chunk: list[PhotoResult], settle: float, timeout: float
) -> dict[str, tuple[ThumbnailResult, object]]:
    """Soumet un job render_probe pour `chunk`, décode+mesure les miniatures (GPU).

    Retourne {photo_id: (ThumbnailResult, RenderAnalysisDual)} — photos sans
    miniature exploitables absentes. Lève RuntimeError si le plugin ne répond pas.
    """
    adjustments = [
        {"photo_id": p.photo_id, "develop": dict(_NEUTRAL_DEVELOP)} for p in chunk
    ]
    job_id = job_queue.submit(
        JobType.RENDER_PROBE, {"adjustments": adjustments, "settle": settle}
    )
    result = job_queue.wait_result(job_id, timeout)
    if result is None:
        raise RuntimeError(
            "Timeout — le plugin Lr n'a pas renvoyé les rendus neutres "
            "(Lightroom ouvert et pont connecté ?)."
        )
    out: dict[str, tuple[ThumbnailResult, object]] = {}
    for t in result.thumbnails:
        if not t.thumbnail_path:
            continue
        chw = gpu_jpeg.decode_file(t.thumbnail_path)
        if chw is None:
            continue
        out[t.photo_id] = (t, render_metrics_gpu.analyze_rendered_gpu_dual(chw))
    return out


def _anchor_suspect(p: PhotoResult, dual, conn) -> bool:
    """True si l'ancre « neutre » ressemble au dernier aperçu rendu connu alors que
    l'Exposure2012 courant est loin de 0 → le probe a probablement rendu du périmé."""
    try:
        expo = abs(float((p.current_develop or {}).get("Exposure2012") or 0.0))
    except (TypeError, ValueError):
        return False
    if expo < _SUSPECT_MIN_EXPO or conn is None:
        return False
    if dual.sharp is None or dual.sharp.tone is None:
        return False
    try:
        prev = cachemod.get_preview_jpeg_latest(conn, p.photo_id)
    except Exception:
        # Ne PAS avaler : sans lecture cache on ne peut pas innocenter l'ancre,
        # et une ancre suspecte cachée empoisonne le mode embedded jusqu'au
        # changement de style (revue Fable 5 B-03) → traiter comme suspecte.
        _log.exception("lecture cache impossible pendant _anchor_suspect (%s)", p.photo_id)
        return True
    if prev is None or prev.tone is None:
        return False
    return abs(dual.sharp.tone.median_l - prev.tone.median_l) < _SUSPECT_MAX_DELTA_L


def ensure_neutral_previews(
    photos: list[PhotoResult],
    conn,
    *,
    progress: Callable[[str], None] | None = None,
    progress_count: Callable[[int, int], None] | None = None,
    chunk_size: int = _CHUNK_SIZE,
    settle: float = DEFAULT_SETTLE,
) -> tuple[dict[str, dict], int]:
    """Garantit un rendu neutre à jour (cache `NeutralPreviewJPEG`) pour chaque photo.

    Hits cache (`hash_style` à jour) servis sans I/O ; les manques déclenchent des
    jobs plugin `render_probe` par lots, décodés et mesurés sur GPU. Retourne
    `(by_id, n_refreshed)` : `by_id[uuid]` = dict `cache.get_neutral_preview`
    (sharp/glob/asshot_temp/asshot_tint/mask_sharp_frac), photos sans miniature
    absentes du dict ; `n_refreshed` = nombre d'ancres recalculées via le plugin.

    Lève RuntimeError si le plugin ne répond pas ou si une ancre reste suspecte
    (probe périmé) après retry — dans ce cas rien n'est caché pour ces photos.
    """
    say = progress or (lambda _msg: None)
    tick = progress_count or (lambda _done, _total: None)
    out: dict[str, dict] = {}
    todo: list[PhotoResult] = []
    style_by_id: dict[str, str] = {}
    for p in photos:
        hs = cachemod.style_hash(p.current_develop or {})
        style_by_id[p.photo_id] = hs
        cached = cachemod.get_neutral_preview(conn, p.photo_id, hs) if conn is not None else None
        if cached is not None:
            out[p.photo_id] = cached
        else:
            todo.append(p)

    n_refreshed = 0
    step = max(1, chunk_size)
    for start in range(0, len(todo), step):
        chunk = todo[start:start + step]
        say(
            f"Rendu neutre {min(start + len(chunk), len(todo))}/{len(todo)} "
            f"photo(s) dans Lightroom…"
        )
        tick(start, len(todo))
        timeout = max(_MIN_TIMEOUT, _SECONDS_PER_PHOTO * len(chunk))
        got = _probe_chunk(chunk, settle, timeout)

        # Restore échoué côté plugin (revue Fable 5 L-03) : la photo est restée en
        # état NEUTRE dans Lightroom — signal fort, à afficher, jamais silencieux.
        restore_failed = [t.photo_id[:8] for (t, _d) in got.values() if t.restore_error]
        if restore_failed:
            msg = (
                f"ATTENTION : restore échoué pour {len(restore_failed)} photo(s) — "
                f"laissées en état neutre dans Lr : {', '.join(restore_failed)}"
            )
            _log.error(msg)
            say(msg)

        # Garde anti-probe-périmé : retry unique avec settle long, puis échec dur.
        by_id = {p.photo_id: p for p in chunk}
        suspects = [
            by_id[pid] for pid, (_t, dual) in got.items()
            if _anchor_suspect(by_id[pid], dual, conn)
        ]
        if suspects:
            say(
                f"Ancre(s) suspecte(s) ({len(suspects)}) — nouveau rendu avec délai "
                f"long ({_RETRY_SETTLE:g}s)…"
            )
            retry_timeout = max(_MIN_TIMEOUT, (_SECONDS_PER_PHOTO + _RETRY_SETTLE) * len(suspects))
            got.update(_probe_chunk(suspects, _RETRY_SETTLE, retry_timeout))
            still = [
                p.photo_id[:8] for p in suspects
                if p.photo_id in got and _anchor_suspect(p, got[p.photo_id][1], conn)
            ]
            if still:
                raise RuntimeError(
                    "Rendu neutre périmé malgré le retry (requestJpegThumbnail sert un "
                    f"cache) pour : {', '.join(still)}. Rien n'a été caché — repli "
                    "LrExportSession à câbler côté plugin si cela persiste."
                )

        for p in chunk:
            hit = got.get(p.photo_id)
            if hit is None:
                continue
            t, dual = hit
            hs = style_by_id[p.photo_id]
            if conn is not None:
                try:
                    cachemod.put_neutral_preview(
                        conn, p.photo_id, hs,
                        sharp=dual.sharp, glob=dual.glob,
                        mask_sharp_frac=dual.mask_sharp_frac,
                        asshot_temp=t.asshot_temp, asshot_tint=t.asshot_tint,
                        commit=False,
                    )
                except Exception:
                    _log.exception("put_neutral_preview a échoué (%s)", p.photo_id)
            out[p.photo_id] = {
                "sharp": dual.sharp, "glob": dual.glob,
                "asshot_temp": t.asshot_temp, "asshot_tint": t.asshot_tint,
                "mask_sharp_frac": dual.mask_sharp_frac,
            }
            n_refreshed += 1
        if conn is not None:
            try:
                conn.commit()  # P-07 : un commit par lot, pas par photo
            except Exception:
                _log.exception("commit du lot neutral impossible")
        tick(min(start + len(chunk), len(todo)), len(todo))
    return out, n_refreshed


class NeutralPreviewWorker(QThread):
    """Génère/rafraîchit les rendus neutres d'ancrage pour la sélection (pré-chauffage)."""

    finished_result = Signal(str)   # message de résumé
    progress = Signal(str)
    progress_count = Signal(int, int)  # (fait, total) → barre de chargement déterminée
    failed = Signal(str)

    def __init__(self, photos: list[PhotoResult]) -> None:
        super().__init__()
        self._photos = photos

    def run(self) -> None:
        conn = None
        try:
            photos = self._photos
            if not photos:
                self.failed.emit("Aucune photo sélectionnée.")
                return
            try:
                gpu.require_cuda()
            except Exception as exc:
                self.failed.emit(str(exc))
                return

            catalog_path = next((p.catalog_path for p in photos if p.catalog_path), None)
            conn = cachemod.open_cache(catalog_path) if catalog_path else None

            by_id, n_refreshed = ensure_neutral_previews(
                photos, conn, progress=self.progress.emit,
                progress_count=self.progress_count.emit,
            )
            n_missing = len(photos) - len(by_id)
            if n_refreshed == 0 and n_missing == 0:
                self.finished_result.emit(
                    f"Rendus neutres déjà à jour ({len(photos)} photo(s), cache)."
                )
            else:
                self.finished_result.emit(
                    f"Rendus neutres calibrés : {n_refreshed} recalculé(s), "
                    f"{len(by_id)}/{len(photos)} disponible(s)"
                    + (f" ({n_missing} sans miniature)." if n_missing else ".")
                )
        except Exception as exc:  # garde-fou
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
