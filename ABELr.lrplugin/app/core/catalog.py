"""Locating Lightroom data on disk + read-only catalog access.

A `Foo.lrcat` catalog is accompanied, in the same folder, by two `.lrdata`
bundles usable without re-export (verified on Lr Classic 13):

    Foo.lrcat
    Foo Previews.lrdata/        JPEG of the LR render (settings applied)
      ├─ {u0}/{u0123}/{uuid}-{digest}_{2048|1024|512|320}   plain JPEG (offset 0)
      ├─ {u0}/{u0123}/{uuid}-{digest}.lrfprev               AgHg container (low level)
      └─ previews.db                                        SQLite index (Pyramid…)
    Foo Smart Previews.lrdata/   lossy JPEG XL DNG, linear 16-bit RGB ~2.5MP
      └─ {u0}/{u0123}/{uuid}.dng

Warning: the preview FILES' `uuid` is NOT `Adobe_images.id_global`. It's an
identifier specific to the preview cache, stored in `previews.db`:

    .lrcat       Adobe_images.id_global  → id_local      (what the plugin sends
                                                           via getRawMetadata('uuid'))
    previews.db  ImageCacheEntry.imageId (= id_local) → uuid + digest

The `uuid`/`digest` obtained this way name the rendered preview files, and the
same `uuid` names the Smart Preview DNG. The subfolder is `{uuid[0]}/{uuid[:4]}`
(e.g. `00BAACF9-…` → `0/00BA/`). The full resolution lives in `previews.py`
(`PreviewIndex`).

`.lrcat` and `previews.db` are standard SQLite: we open them read-only and
immutable (`mode=ro&immutable=1`) so no lock is taken even while Lightroom is
open.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


def preview_subdir(uuid: str) -> str:
    """Subpath `{uuid[0]}/{uuid[:4]}` used by both .lrdata bundles."""
    return f"{uuid[0]}/{uuid[:4]}"


@dataclass(frozen=True)
class CatalogPaths:
    """Paths derived from a `.lrcat` (the bundles may not exist)."""

    lrcat: Path
    previews: Path        # "Foo Previews.lrdata"
    smart_previews: Path  # "Foo Smart Previews.lrdata"

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
    """Builds the .lrdata paths from the `.lrcat` path.

    The bundles follow the convention `{catalog name} Previews.lrdata` and
    `{catalog name} Smart Previews.lrdata` in the catalog's folder.
    """
    lrcat = Path(lrcat_path)
    stem = lrcat.stem  # name without extension
    folder = lrcat.parent
    return CatalogPaths(
        lrcat=lrcat,
        previews=folder / f"{stem} Previews.lrdata",
        smart_previews=folder / f"{stem} Smart Previews.lrdata",
    )


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Opens a SQLite file (.lrcat or previews.db) read-only, without locking.

    `immutable=1` promises SQLite that the file won't change during the read:
    no lock taken, coexists with Lightroom open. Only use for one-off reads
    (snapshot).
    """
    uri = Path(db_path).as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def resolve_image_id(conn: sqlite3.Connection, id_global: str) -> int | None:
    """`Adobe_images.id_local` for an `id_global` (uuid returned by the plugin)."""
    row = conn.execute(
        "SELECT id_local FROM Adobe_images WHERE id_global = ?", (id_global,)
    ).fetchone()
    return int(row[0]) if row else None


def resolve_raw_path(conn: sqlite3.Connection, id_global: str) -> str | None:
    """Absolute path of the original RAW for an `id_global`, via the .lrcat.

    Useful if the plugin hasn't already provided the path. Joins
    RootFolder → Folder → File from the image identified by its id_global.
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
