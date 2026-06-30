"""Cache SQLite des analyses/calculs — évite de re-décoder à chaque opération.

Un fichier `LrAutomation_cache.db` est créé **dans le dossier du catalogue actif**
(à côté du `.lrcat`, cf. `catalog.resolve_catalog`). Il est alimenté par tous les
calculs pixel : les opérations consultent d'abord le cache et ne re-décodent (GPU)
que les éléments manquants ou dont le contenu a changé.

Quatre tables, **clé commune `uuid`** (= `id_global` du catalogue Lr) :

| Table            | Source décodée            | Clé de fraîcheur (`*_hash`)        |
|------------------|---------------------------|------------------------------------|
| `LightroomPicture` | métadonnées catalogue   | `develop_hash` (réglages develop)  |
| `SourceRAW`        | pixels RAW (.ARW)       | `raw_hash` (taille+mtime du RAW)   |
| `InCameraJPEG`     | JPEG boîtier embarqué   | `jpeg_hash` (sha1 des octets)      |
| `PreviewJPEG`      | aperçu rendu Lr         | `preview_hash` (sha1 des octets)   |

Chaque table porte un `hash` propre : si le hash stocké == le hash courant, les
scalaires sont réutilisés tels quels (zéro décode) ; sinon la ligne est recalculée
et réécrite (`INSERT OR REPLACE`).

SQLite standard en lecture-écriture (WAL) — cohabite avec le `.lrcat` ouvert par
Lightroom (fichier distinct, aucun verrou sur le catalogue).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .analysis import ExposureStats
from .pipeline import RenderAnalysis
from .render_metrics import BandStats, NeutralStats, ToneStats

CACHE_FILENAME = "LrAutomation_cache.db"


# --------------------------------------------------------------------------- #
# Localisation + connexion
# --------------------------------------------------------------------------- #
def cache_path_for_catalog(catalog_path: str | Path) -> Path:
    """Chemin du `.db` cache : même dossier que le `.lrcat`."""
    return Path(catalog_path).parent / CACHE_FILENAME


def open_cache(catalog_path: str | Path) -> sqlite3.Connection:
    """Ouvre (crée si besoin) le cache en lecture-écriture et garantit le schéma.

    WAL + `synchronous=NORMAL` : écritures rapides, robustes au crash, sans bloquer
    les lectures. `check_same_thread=False` car le GUI et ses workers (QThread)
    peuvent y accéder ; chaque accès reste sérialisé par SQLite.
    """
    db = cache_path_for_catalog(catalog_path)
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS LightroomPicture (
            uuid            TEXT PRIMARY KEY,
            path            TEXT,
            catalog_path    TEXT,
            camera          TEXT,
            iso             INTEGER,
            aperture        REAL,
            shutter_speed   TEXT,
            focal_length    REAL,
            current_develop TEXT,     -- JSON
            develop_hash    TEXT,
            cached_at       REAL
        );

        CREATE TABLE IF NOT EXISTS SourceRAW (
            uuid               TEXT PRIMARY KEY,
            raw_hash           TEXT,
            asshot_rg          REAL,
            asshot_bg          REAL,
            mean_luma          REAL,
            median_luma        REAL,
            clipped_highlights REAL,
            clipped_shadows    REAL,
            grayworld_rg       REAL,
            grayworld_bg       REAL,
            cached_at          REAL
        );

        CREATE TABLE IF NOT EXISTS InCameraJPEG (
            uuid        TEXT PRIMARY KEY,
            jpeg_hash   TEXT,
            tone        TEXT,   -- JSON ToneStats
            bands       TEXT,   -- JSON list[BandStats]
            cached_at   REAL
        );

        CREATE TABLE IF NOT EXISTS PreviewJPEG (
            uuid          TEXT PRIMARY KEY,
            preview_hash  TEXT,
            tone          TEXT, -- JSON ToneStats
            neutral       TEXT, -- JSON NeutralStats
            bands         TEXT, -- JSON list[BandStats]
            cached_at     REAL
        );
        """
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Hash de fraîcheur — « le contenu de l'élément a-t-il changé ? »
# --------------------------------------------------------------------------- #
def raw_signature(path: str | Path) -> str:
    """Signature rapide d'un fichier RAW : `taille:mtime_ns` (pas de relecture).

    Suffisant pour détecter une réécriture du `.ARW` ; bien plus rapide qu'un
    sha256 du fichier entier (~50 Mo). Retourne `"0:0"` si le fichier est absent.
    """
    p = Path(path)
    try:
        st = p.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return "0:0"


def blob_hash(data: bytes) -> str:
    """sha1 des octets (JPEG boîtier / aperçu — petits → coût négligeable)."""
    return hashlib.sha1(data).hexdigest()


def develop_hash(develop: dict[str, Any] | None) -> str:
    """Hash stable d'un dict de réglages develop (détecte une retouche manuelle)."""
    payload = json.dumps(develop or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# (dé)sérialisation des dataclasses ↔ JSON
# --------------------------------------------------------------------------- #
def _tone_to_json(t: ToneStats) -> str:
    return json.dumps(asdict(t))


def _tone_from_json(s: str) -> ToneStats:
    return ToneStats(**json.loads(s))


def _neutral_to_json(n: NeutralStats) -> str:
    return json.dumps(asdict(n))


def _neutral_from_json(s: str) -> NeutralStats:
    return NeutralStats(**json.loads(s))


def _bands_to_json(bands: list[BandStats]) -> str:
    return json.dumps([asdict(b) for b in bands])


def _bands_from_json(s: str) -> list[BandStats]:
    return [BandStats(**d) for d in json.loads(s)]


# --------------------------------------------------------------------------- #
# LightroomPicture (ancre)
# --------------------------------------------------------------------------- #
def put_picture(
    conn: sqlite3.Connection,
    uuid: str,
    *,
    path: str | None,
    catalog_path: str | None,
    exif: dict[str, Any] | None,
    current_develop: dict[str, Any] | None,
) -> None:
    """Insère/MAJ la ligne d'ancrage (métadonnées + snapshot develop)."""
    exif = exif or {}
    conn.execute(
        """INSERT OR REPLACE INTO LightroomPicture
           (uuid, path, catalog_path, camera, iso, aperture, shutter_speed,
            focal_length, current_develop, develop_hash, cached_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid, path, catalog_path, exif.get("camera"), exif.get("iso"),
            exif.get("aperture"), exif.get("shutter_speed"), exif.get("focal_length"),
            json.dumps(current_develop or {}), develop_hash(current_develop), time.time(),
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# SourceRAW (pixels RAW : expo + as-shot WB + gray-world)
# --------------------------------------------------------------------------- #
def get_source_raw(
    conn: sqlite3.Connection, uuid: str, raw_hash: str
) -> Optional[dict[str, Any]]:
    """Renvoie les scalaires RAW cachés si le `raw_hash` correspond, sinon None.

    `exposure` / `grayworld_*` valent None si la ligne ne porte que la WB as-shot
    (écrite par l'autocorrect, qui ne décode pas les pixels RAW). L'analyse pixel
    complète (`gpu_raw` via `analysis_worker`) renseigne tous les champs.
    """
    row = conn.execute(
        "SELECT * FROM SourceRAW WHERE uuid=? AND raw_hash=?", (uuid, raw_hash)
    ).fetchone()
    if row is None:
        return None
    exposure = (
        ExposureStats(
            row["mean_luma"], row["median_luma"],
            row["clipped_highlights"], row["clipped_shadows"],
        )
        if row["mean_luma"] is not None
        else None
    )
    return {
        "asshot_rg": row["asshot_rg"], "asshot_bg": row["asshot_bg"],
        "exposure": exposure,
        "grayworld_rg": row["grayworld_rg"], "grayworld_bg": row["grayworld_bg"],
    }


def put_source_raw(
    conn: sqlite3.Connection,
    uuid: str,
    raw_hash: str,
    *,
    asshot_rg: float | None,
    asshot_bg: float | None,
    exposure: ExposureStats | None = None,
    grayworld_rg: float | None = None,
    grayworld_bg: float | None = None,
) -> None:
    """Écrit la ligne SourceRAW. `exposure`/`grayworld_*` optionnels (autocorrect =
    WB as-shot seule ; analysis = analyse pixel complète)."""
    conn.execute(
        """INSERT OR REPLACE INTO SourceRAW
           (uuid, raw_hash, asshot_rg, asshot_bg, mean_luma, median_luma,
            clipped_highlights, clipped_shadows, grayworld_rg, grayworld_bg, cached_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid, raw_hash, asshot_rg, asshot_bg,
            exposure.mean_luma if exposure else None,
            exposure.median_luma if exposure else None,
            exposure.clipped_highlights if exposure else None,
            exposure.clipped_shadows if exposure else None,
            grayworld_rg, grayworld_bg, time.time(),
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# InCameraJPEG (JPEG boîtier : tone + bandes)
# --------------------------------------------------------------------------- #
def get_in_camera_jpeg(
    conn: sqlite3.Connection, uuid: str, jpeg_hash: str
) -> Optional[tuple[ToneStats, list[BandStats]]]:
    row = conn.execute(
        "SELECT tone, bands FROM InCameraJPEG WHERE uuid=? AND jpeg_hash=?",
        (uuid, jpeg_hash),
    ).fetchone()
    if row is None:
        return None
    return _tone_from_json(row["tone"]), _bands_from_json(row["bands"])


def put_in_camera_jpeg(
    conn: sqlite3.Connection,
    uuid: str,
    jpeg_hash: str,
    *,
    tone: ToneStats,
    bands: list[BandStats],
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO InCameraJPEG (uuid, jpeg_hash, tone, bands, cached_at)
           VALUES (?,?,?,?,?)""",
        (uuid, jpeg_hash, _tone_to_json(tone), _bands_to_json(bands), time.time()),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# PreviewJPEG (aperçu rendu : analyse complète tone+neutral+bandes)
# --------------------------------------------------------------------------- #
def get_preview_jpeg(
    conn: sqlite3.Connection, uuid: str, preview_hash: str
) -> Optional[RenderAnalysis]:
    row = conn.execute(
        "SELECT tone, neutral, bands FROM PreviewJPEG WHERE uuid=? AND preview_hash=?",
        (uuid, preview_hash),
    ).fetchone()
    if row is None:
        return None
    return RenderAnalysis(
        tone=_tone_from_json(row["tone"]),
        neutral=_neutral_from_json(row["neutral"]),
        bands=_bands_from_json(row["bands"]),
    )


def put_preview_jpeg(
    conn: sqlite3.Connection,
    uuid: str,
    preview_hash: str,
    *,
    analysis: RenderAnalysis,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO PreviewJPEG
           (uuid, preview_hash, tone, neutral, bands, cached_at) VALUES (?,?,?,?,?,?)""",
        (
            uuid, preview_hash, _tone_to_json(analysis.tone),
            _neutral_to_json(analysis.neutral), _bands_to_json(analysis.bands), time.time(),
        ),
    )
    conn.commit()
