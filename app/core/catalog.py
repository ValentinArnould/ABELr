"""Localisation des données Lightroom sur disque + lecture seule du catalogue.

Un catalogue `Foo.lrcat` est accompagné, dans le même dossier, de deux bundles
`.lrdata` exploitables sans réexport (vérifié sur Lr Classic 13) :

    Foo.lrcat
    Foo Previews.lrdata/        JPEG du rendu LR (réglages appliqués)
      ├─ {u0}/{u0123}/{uuid}-{digest}_{2048|1024|512|320}   JPEG pur (offset 0)
      ├─ {u0}/{u0123}/{uuid}-{digest}.lrfprev               conteneur AgHg (niveau bas)
      └─ previews.db                                        index SQLite (Pyramid…)
    Foo Smart Previews.lrdata/   DNG lossy JPEG XL, RGB 16-bit linéaire ~2.5MP
      └─ {u0}/{u0123}/{uuid}.dng

⚠️ L'`uuid` des FICHIERS de preview n'est PAS `Adobe_images.id_global`. C'est un
identifiant propre au cache de previews, stocké dans `previews.db` :

    .lrcat       Adobe_images.id_global  → id_local      (ce que le plugin envoie
                                                           via getRawMetadata('uuid'))
    previews.db  ImageCacheEntry.imageId (= id_local) → uuid + digest

Le `uuid`/`digest` ainsi obtenus nomment les fichiers d'aperçu rendu, et le même
`uuid` nomme le DNG Smart Preview. Le sous-dossier est `{uuid[0]}/{uuid[:4]}`
(ex. `00BAACF9-…` → `0/00BA/`). La résolution complète vit dans `previews.py`
(`PreviewIndex`).

Le `.lrcat` et `previews.db` sont du SQLite standard : on les ouvre en lecture
seule immuable (`mode=ro&immutable=1`) pour ne poser aucun verrou même quand
Lightroom est ouvert.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


def preview_subdir(uuid: str) -> str:
    """Sous-chemin `{uuid[0]}/{uuid[:4]}` utilisé par les deux bundles .lrdata."""
    return f"{uuid[0]}/{uuid[:4]}"


@dataclass(frozen=True)
class CatalogPaths:
    """Chemins dérivés d'un `.lrcat` (les bundles peuvent ne pas exister)."""

    lrcat: Path
    previews: Path        # « Foo Previews.lrdata »
    smart_previews: Path  # « Foo Smart Previews.lrdata »

    @property
    def previews_db(self) -> Path:
        return self.previews / "previews.db"

    @property
    def has_previews(self) -> bool:
        return self.previews.is_dir()

    @property
    def has_smart_previews(self) -> bool:
        return self.smart_previews.is_dir()


def resolve_catalog(lrcat_path: str | Path) -> CatalogPaths:
    """Construit les chemins .lrdata à partir du chemin du `.lrcat`.

    Les bundles suivent la convention `{nom du catalogue} Previews.lrdata` et
    `{nom du catalogue} Smart Previews.lrdata` dans le dossier du catalogue.
    """
    lrcat = Path(lrcat_path)
    stem = lrcat.stem  # nom sans extension
    folder = lrcat.parent
    return CatalogPaths(
        lrcat=lrcat,
        previews=folder / f"{stem} Previews.lrdata",
        smart_previews=folder / f"{stem} Smart Previews.lrdata",
    )


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Ouvre un SQLite (.lrcat ou previews.db) en lecture seule, sans verrou.

    `immutable=1` promet à SQLite que le fichier ne change pas pendant la lecture :
    aucun lock posé, cohabite avec Lightroom ouvert. À n'utiliser que pour des
    lectures ponctuelles (snapshot).
    """
    uri = Path(db_path).as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def resolve_image_id(conn: sqlite3.Connection, id_global: str) -> int | None:
    """`Adobe_images.id_local` pour un `id_global` (uuid renvoyé par le plugin)."""
    row = conn.execute(
        "SELECT id_local FROM Adobe_images WHERE id_global = ?", (id_global,)
    ).fetchone()
    return int(row[0]) if row else None


def resolve_raw_path(conn: sqlite3.Connection, id_global: str) -> str | None:
    """Chemin absolu du RAW d'origine pour un `id_global`, via le .lrcat.

    Utile si le plugin n'a pas déjà fourni le chemin. Jointure
    RootFolder → Folder → File depuis l'image identifiée par son id_global.
    """
    row = conn.execute(
        """
        SELECT root.absolutePath || folder.pathFromRoot || file.originalFilename
        FROM Adobe_images img
        JOIN AgLibraryFile       file   ON file.id_local   = img.rootFile
        JOIN AgLibraryFolder     folder ON folder.id_local = file.folder
        JOIN AgLibraryRootFolder root   ON root.id_local   = folder.rootFolder
        WHERE img.id_global = ?
        """,
        (id_global,),
    ).fetchone()
    return row[0] if row else None
