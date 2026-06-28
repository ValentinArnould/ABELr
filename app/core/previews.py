"""Décodage des deux sources d'analyse Lightroom sans réexport RAW.

1. Aperçu rendu (« Previews.lrdata ») — JPEG du rendu LR, **réglages appliqués**.
   Idéal pour vérifier le RÉSULTAT d'une correction. Décodage ~5-20 ms.
   En Lr 13 chaque niveau de pyramide est un fichier `{uuid}-{digest}_{taille}`
   (JPEG brut, offset 0). Un conteneur `{uuid}-{digest}.lrfprev` (en-tête `AgHg`)
   porte le plus petit niveau ; le JPEG y commence après l'en-tête.

2. Smart Preview (« Smart Previews.lrdata ») — DNG lossy **JPEG XL**, RGB 16-bit
   linéaire ~2.5MP, **avant réglages**. Idéal pour exposition / balance des blancs
   brutes. Décodage ~100 ms via tifffile + imagecodecs (libjxl). rawpy/LibRaw ne
   sait PAS lire ces DNG (tuiles JXL, compression 52546).

Le `uuid` qui nomme ces fichiers n'est PAS `id_global` (ce que le plugin envoie),
mais l'identifiant de cache de `previews.db`. `PreviewIndex` fait le pont :
`id_global` → (uuid, digest) → chemins. Toutes les fonctions retournent du RGB.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import numpy as np

from . import catalog
from .catalog import CatalogPaths, preview_subdir

# Suffixe de niveau des fichiers d'aperçu rendu : « …_2048 », « …_320 ».
_LEVEL_RE = re.compile(r"_(\d+)$")
# Magic de début de flux JPEG (Start Of Image).
_JPEG_SOI = b"\xff\xd8\xff"


# --------------------------------------------------------------------------- #
# Aperçu rendu (Previews.lrdata) — JPEG, réglages LR appliqués
# --------------------------------------------------------------------------- #
def find_rendered_preview(paths: CatalogPaths, uuid: str) -> Path | None:
    """Fichier d'aperçu rendu de plus haute résolution pour le `uuid` de cache.

    Cherche dans `{uuid[0]}/{uuid[:4]}/` tous les `{uuid}-*` et retient le
    niveau `_{taille}` le plus grand. Repli sur `.lrfprev` si aucun niveau
    numéroté n'est présent. `uuid` = preview-uuid (cf. `PreviewIndex`), pas id_global.
    """
    folder = paths.previews / preview_subdir(uuid)
    if not folder.is_dir():
        return None

    best: tuple[int, Path] | None = None
    fallback: Path | None = None
    for f in folder.glob(f"{uuid}-*"):
        m = _LEVEL_RE.search(f.name)
        if m:
            size = int(m.group(1))
            if best is None or size > best[0]:
                best = (size, f)
        elif f.suffix == ".lrfprev":
            fallback = f
    if best is not None:
        return best[1]
    return fallback


def decode_rendered_preview(path: str | Path) -> np.ndarray:
    """Décode un fichier d'aperçu rendu en RGB uint8 (HxWx3).

    Gère le JPEG brut (offset 0) comme le conteneur `.lrfprev` (`AgHg`) en
    repérant le marqueur SOI. Lève ValueError si aucun JPEG / décodage échoué.
    """
    import cv2

    data = Path(path).read_bytes()
    start = 0 if data[:3] == _JPEG_SOI else data.find(_JPEG_SOI)
    if start == -1:
        raise ValueError(f"Aucun flux JPEG dans {path}")
    arr = np.frombuffer(data, np.uint8, offset=start)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Échec décodage JPEG : {path}")
    return img[:, :, ::-1]  # BGR -> RGB


# --------------------------------------------------------------------------- #
# Smart Preview (Smart Previews.lrdata) — DNG JPEG XL 16-bit linéaire
# --------------------------------------------------------------------------- #
def smart_preview_path(paths: CatalogPaths, uuid: str) -> Path | None:
    """Chemin déterministe du DNG Smart Preview pour le `uuid` de cache, ou None."""
    p = paths.smart_previews / preview_subdir(uuid) / f"{uuid}.dng"
    return p if p.is_file() else None


def decode_smart_preview(path: str | Path, normalize: bool = False) -> np.ndarray:
    """Décode le Smart Preview (DNG JXL) en RGB.

    Retourne le SubIFD pleine résolution (~2560 px de côté long), RGB 16-bit
    linéaire. `normalize=True` renvoie du float32 0-1 (pratique pour l'analyse
    en espace linéaire : exposition, balance des blancs).

    Nécessite `tifffile` + `imagecodecs` (décodeur JPEG XL).
    """
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        main = tif.pages[0]
        # L'image utile est en SubIFD (la page principale = thumbnail YCbCr).
        candidates = list(main.pages) or [main]
        page = max(candidates, key=lambda p: p.imagelength * p.imagewidth)
        arr = page.asarray()  # uint16 HxWx3, linéaire

    if normalize:
        return arr.astype(np.float32) / float(np.iinfo(arr.dtype).max)
    return arr


# --------------------------------------------------------------------------- #
# Résolution id_global → fichiers de preview (pont .lrcat + previews.db)
# --------------------------------------------------------------------------- #
class PreviewIndex:
    """Résout l'`id_global` (envoyé par le plugin) vers les fichiers de preview.

    Ouvre `.lrcat` et `previews.db` en lecture seule une seule fois — pensé pour
    le batch (500-1000 photos). À fermer via `close()` ou comme context manager.
    """

    def __init__(self, lrcat_path: str | Path) -> None:
        self.paths: CatalogPaths = catalog.resolve_catalog(lrcat_path)
        self._cat: sqlite3.Connection = catalog.open_readonly(self.paths.lrcat)
        self._pv: sqlite3.Connection | None = (
            catalog.open_readonly(self.paths.previews_db)
            if self.paths.previews_db.is_file()
            else None
        )

    def __enter__(self) -> "PreviewIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._cat.close()
        if self._pv is not None:
            self._pv.close()

    def preview_key(self, id_global: str) -> tuple[str, str] | None:
        """(uuid de cache, digest) pour un `id_global`, ou None si pas de preview.

        id_global → id_local (.lrcat) → ImageCacheEntry.uuid/digest (previews.db).
        """
        if self._pv is None:
            return None
        image_id = catalog.resolve_image_id(self._cat, id_global)
        if image_id is None:
            return None
        row = self._pv.execute(
            "SELECT uuid, digest FROM ImageCacheEntry WHERE imageId = ?",
            (image_id,),
        ).fetchone()
        return (row[0], row[1]) if row else None

    # -- Aperçu rendu (réglages appliqués) ---------------------------------- #
    def rendered_path(self, id_global: str) -> Path | None:
        key = self.preview_key(id_global)
        return find_rendered_preview(self.paths, key[0]) if key else None

    def load_rendered(self, id_global: str) -> np.ndarray | None:
        f = self.rendered_path(id_global)
        return decode_rendered_preview(f) if f is not None else None

    # -- Smart Preview (avant réglages, 16-bit linéaire) -------------------- #
    def smart_path(self, id_global: str) -> Path | None:
        key = self.preview_key(id_global)
        return smart_preview_path(self.paths, key[0]) if key else None

    def load_smart(self, id_global: str, normalize: bool = False) -> np.ndarray | None:
        f = self.smart_path(id_global)
        return decode_smart_preview(f, normalize=normalize) if f is not None else None
