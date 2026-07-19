"""Sélection du canal de mesure du **rendu** (output-referred).

Trois canaux, choisis par fiabilité/rapidité (décision utilisateur) :

1. **requestJpegThumbnail** (job plugin `get_thumbnails` / `render_probe`) — rendu Lr
   frais, écrit en JPEG sur disque par le plugin. Reflète les réglages courants (ou
   sondés). Canal **prioritaire** ; c'est aussi le seul qui permet de SONDER la réponse.
2. **Previews.lrdata** (`previews.PreviewIndex`) — aperçu rendu déjà en cache. Gratuit,
   rapide, mais peut être périmé/absent. Repli passif.
3. **LrExportSession** — export plein rendu, lent/coûteux. Dernier recours (non câblé
   ici ; à activer côté plugin si le canal 1 s'avère renvoyer un cache périmé).

Ce module est **côté App** : il ne soumet pas de job lui-même (ça vit dans les workers
GUI via la queue). Il reçoit les entrées disponibles (un chemin de miniature déjà rendue
et/ou un `PreviewIndex`) et renvoie le meilleur RGB rendu décodé, prêt pour `render_metrics`.
Le décodage réutilise `previews.decode_rendered_preview` (gère JPEG brut et en-tête `.lrfprev`).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import numpy as np

from . import previews
from .previews import PreviewIndex


class RenderChannel(str, Enum):
    THUMBNAIL = "thumbnail"   # requestJpegThumbnail (frais, prioritaire)
    PREVIEW = "preview"       # Previews.lrdata (repli passif)
    EXPORT = "export"         # LrExportSession (dernier recours, non câblé)
    NONE = "none"             # aucun rendu disponible


def decode_jpeg_file(path: str | Path) -> np.ndarray:
    """Décode un JPEG rendu (miniature plugin ou fichier Previews.lrdata) en RGB uint8.

    Délègue à `previews.decode_rendered_preview` : gère le JPEG brut (offset 0) comme
    le conteneur `.lrfprev` (en-tête `AgHg`, recherche du marqueur SOI).
    """
    return previews.decode_rendered_preview(path)


def resolve_render_path(
    *,
    thumbnail_path: str | Path | None = None,
    preview_index: PreviewIndex | None = None,
    id_global: str | None = None,
) -> tuple[Path | None, RenderChannel]:
    """Localise le **fichier** de rendu (sans décoder) selon la priorité de canal.

    Pendant de `load_rendered` pour le pipeline **GPU** : on veut le chemin (pour en
    lire les octets et décoder sur GPU via nvJPEG), pas un array décodé CPU.
    Priorité : miniature fraîche (plugin) → aperçu Previews.lrdata → None.
    """
    if thumbnail_path is not None and Path(thumbnail_path).is_file():
        return Path(thumbnail_path), RenderChannel.THUMBNAIL
    if preview_index is not None and id_global:
        p = preview_index.rendered_path(id_global)
        if p is not None:
            return p, RenderChannel.PREVIEW
    return None, RenderChannel.NONE


def load_rendered(
    *,
    thumbnail_path: str | Path | None = None,
    preview_index: PreviewIndex | None = None,
    id_global: str | None = None,
) -> tuple[np.ndarray | None, RenderChannel]:
    """Retourne (RGB uint8 rendu, canal utilisé) selon ce qui est disponible.

    Priorité : miniature fraîche (plugin) → aperçu Previews.lrdata (passif) → rien.
    Le caller fournit `thumbnail_path` s'il a déjà fait rendre la photo par le plugin
    (job `get_thumbnails`/`render_probe`), et/ou un `PreviewIndex` + `id_global` pour
    le repli passif.
    """
    # 1. Miniature fraîche écrite par le plugin (canal prioritaire).
    if thumbnail_path is not None and Path(thumbnail_path).is_file():
        try:
            return decode_jpeg_file(thumbnail_path), RenderChannel.THUMBNAIL
        except ValueError:
            pass  # fichier illisible → on tente le repli

    # 2. Aperçu rendu déjà en cache (repli passif).
    if preview_index is not None and id_global:
        rgb = preview_index.load_rendered(id_global)
        if rgb is not None:
            return rgb, RenderChannel.PREVIEW

    # 3. LrExportSession : dernier recours, non câblé ici.
    return None, RenderChannel.NONE
