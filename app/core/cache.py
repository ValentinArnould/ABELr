"""Cache SQLite des analyses/calculs — évite de re-décoder à chaque opération.

Un fichier `LrAutomation_cache.db` est créé **dans le dossier du catalogue actif**
(à côté du `.lrcat`, cf. `catalog.resolve_catalog`). Il est alimenté par tous les
calculs pixel : les opérations consultent d'abord le cache et ne re-décodent (GPU)
que les éléments manquants ou dont le contenu a changé.

Cinq tables, **clé commune `uuid`** (= `id_global` du catalogue Lr) :

| Table                | Source décodée              | Clé de fraîcheur (`hash_*`)        |
|----------------------|-----------------------------|------------------------------------|
| `LightroomPicture`   | métadonnées catalogue       | `hash_develop` (réglages develop)  |
| `SourceRAW`          | pixels RAW (.ARW)           | `hash_raw` (taille+mtime du RAW)   |
| `InCameraJPEG`       | JPEG boîtier embarqué       | `hash_jpeg` (sha1 des octets)      |
| `PreviewJPEG`        | aperçu rendu Lr             | `hash_preview` (sha1 des octets)   |
| `NeutralPreviewJPEG` | rendu neutre (As Shot/Exp0) | `hash_style` (sous-ensemble style) |

**Nomenclature unifiée des colonnes** (on ignore la rétrocompatibilité) :
famille en préfixe (`luma_`/`wb_`/`tone_`/`neutral_`/`hsl_`/`delta_`/`mask_`/
`exif_`/`profile_`/`hash_`), **portée en suffixe** `_global`/`_sharp` (jamais nue).
Les mesures existent en paire **global** (frame entier) + **sharp** (zone nette,
`core.sharpness`) partout où c'est pertinent — le delta global↔sharp révèle
contre-jour / cast fond≠sujet, et le global sert de repli si le masque net dégénère.

Contrôle de version par `PRAGMA user_version` : si le schéma stocké ne correspond
pas à `SCHEMA_VERSION`, toutes les tables sont **supprimées et recréées** (pas de
migration ligne à ligne — le cache est reconstruit depuis les RAW).

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

# Version du **schéma** (structure des tables). Un changement de structure
# déclenche un DROP+recreate via `PRAGMA user_version`.
SCHEMA_VERSION = 4

# Salée dans les hash de fraîcheur (`raw_signature`, `blob_hash`, `style_hash`) :
# un changement d'algorithme de mesure (nouvelles paires global/sharp, deltas…)
# doit invalider tout le contenu caché sans migration — bump quand le calcul change.
ANALYSIS_VERSION = "v4-neutral-anchor"

# Sous-ensemble "style" des réglages develop = tout ce qui affecte le rendu NEUTRE
# (probe `render_probe` : WB As Shot + Exposure2012=0 + **HSL 24 à zéro**, le reste
# intact). Clé de fraîcheur du NeutralPreview (`hash_style`).
#
# - Les 24 clés HSL sont **exclues** : le probe les neutralise, donc un Apply HSL
#   ne doit PAS invalider l'ancre (sinon re-probe complet à chaque cycle).
# - Les réglages de ton (Contrast/Highlights/…/Dehaze/Vibrance/Saturation) et le
#   crop sont **inclus** : ils ne sont pas neutralisés par le probe et changent le
#   rendu neutre — sans eux l'ancre serait périmée silencieusement.
# - Temp/Tint/Exposure restent hors clé (neutralisés par le probe).
_STYLE_KEYS = (
    "CameraProfile", "ProcessVersion",
    "Contrast2012", "Highlights2012", "Shadows2012", "Whites2012", "Blacks2012",
    "Clarity2012", "Dehaze", "Vibrance", "Saturation",
    "CropLeft", "CropRight", "CropTop", "CropBottom", "CropAngle",
    "ColorGradeShadowHue", "ColorGradeShadowSat", "ColorGradeShadowLum",
    "ColorGradeMidtoneHue", "ColorGradeMidtoneSat", "ColorGradeMidtoneLum",
    "ColorGradeHighlightHue", "ColorGradeHighlightSat", "ColorGradeHighlightLum",
    "ColorGradeGlobalHue", "ColorGradeGlobalSat", "ColorGradeGlobalLum",
    "ColorGradeBlending", "ColorGradeBalance",
)


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
    _ensure_schema(conn)
    return conn


_TABLES = (
    "LightroomPicture", "SourceRAW", "InCameraJPEG", "PreviewJPEG", "NeutralPreviewJPEG",
)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée le schéma ; DROP+recreate si `user_version` ≠ `SCHEMA_VERSION`.

    On abandonne la migration incrémentale : le cache étant intégralement
    reconstruit depuis les RAW/JPEG, un changement de structure jette et recrée
    les tables (bien plus simple et sûr qu'une suite d'`ALTER`).
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        for t in _TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        _init_schema(conn)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    else:
        _init_schema(conn)  # CREATE IF NOT EXISTS — no-op si déjà présent


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS LightroomPicture (
            uuid              TEXT PRIMARY KEY,
            path              TEXT,
            catalog_path      TEXT,
            exif_camera       TEXT,
            exif_iso          INTEGER,
            exif_aperture     REAL,
            exif_shutter      TEXT,
            exif_focal_length REAL,
            profile_capture   TEXT,      -- profil créatif boîtier (IN/SH/ST/VV2…)
            profile_dcp       TEXT,      -- CameraProfile Lr, extrait à plat
            develop_json      TEXT,      -- JSON snapshot develop courant
            hash_develop      TEXT,
            hash_style        TEXT,      -- sous-ensemble style (cf. _STYLE_KEYS)
            is_seed           INTEGER DEFAULT 0,
            cached_at         REAL
        );

        CREATE TABLE IF NOT EXISTS SourceRAW (
            uuid                   TEXT PRIMARY KEY,
            hash_raw               TEXT,
            wb_asshot_rg           REAL,
            wb_asshot_bg           REAL,
            luma_mean_global       REAL,
            luma_median_global     REAL,
            luma_clip_hi_global    REAL,
            luma_clip_lo_global    REAL,
            luma_mean_sharp        REAL,
            luma_median_sharp      REAL,
            luma_clip_hi_sharp     REAL,
            luma_clip_lo_sharp     REAL,
            wb_grayworld_rg_global REAL,
            wb_grayworld_bg_global REAL,
            wb_grayworld_rg_sharp  REAL,
            wb_grayworld_bg_sharp  REAL,
            mask_sharp_frac        REAL,
            ev100                  REAL,
            profile_capture        TEXT,
            tone_sharp             TEXT,  -- JSON ToneStats (zone nette)
            hsl_sharp              TEXT,  -- JSON list[BandStats] (zone nette)
            cached_at              REAL
        );

        CREATE TABLE IF NOT EXISTS InCameraJPEG (
            uuid              TEXT PRIMARY KEY,
            hash_jpeg         TEXT,
            tone_sharp        TEXT,
            neutral_sharp     TEXT,
            hsl_sharp         TEXT,
            tone_global       TEXT,
            neutral_global    TEXT,
            hsl_global        TEXT,
            mask_sharp_frac   REAL,
            profile_capture   TEXT,
            delta_luma_median REAL,   -- vs SourceRAW.luma_median_sharp (même uuid)
            delta_wb_cast_a   REAL,
            delta_wb_cast_b   REAL,
            delta_hsl         TEXT,   -- JSON list de deltas par bande
            cached_at         REAL
        );

        CREATE TABLE IF NOT EXISTS PreviewJPEG (
            uuid            TEXT PRIMARY KEY,
            hash_preview    TEXT,
            tone_sharp      TEXT,
            neutral_sharp   TEXT,
            hsl_sharp       TEXT,
            tone_global     TEXT,
            neutral_global  TEXT,
            hsl_global      TEXT,
            mask_sharp_frac REAL,
            cached_at       REAL
        );

        CREATE TABLE IF NOT EXISTS NeutralPreviewJPEG (
            uuid            TEXT PRIMARY KEY,
            hash_style      TEXT,
            tone_sharp      TEXT,
            neutral_sharp   TEXT,
            hsl_sharp       TEXT,
            tone_global     TEXT,
            neutral_global  TEXT,
            hsl_global      TEXT,
            mask_sharp_frac REAL,
            wb_asshot_temp  REAL,   -- Temperature numérique lue après WhiteBalance='As Shot'
            wb_asshot_tint  REAL,   -- Tint numérique idem (base d'une correction WB absolue)
            cached_at       REAL
        );

        CREATE INDEX IF NOT EXISTS idx_picture_is_seed
            ON LightroomPicture(is_seed);
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
        return f"{st.st_size}:{st.st_mtime_ns}:{ANALYSIS_VERSION}"
    except OSError:
        return "0:0"


def blob_hash(data: bytes) -> str:
    """sha1 des octets (JPEG boîtier / aperçu — petits → coût négligeable)."""
    return hashlib.sha1(data + ANALYSIS_VERSION.encode("utf-8")).hexdigest()


def develop_hash(develop: dict[str, Any] | None) -> str:
    """Hash stable d'un dict de réglages develop (détecte une retouche manuelle)."""
    payload = json.dumps(develop or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def style_hash(develop: dict[str, Any] | None) -> str:
    """Hash du sous-ensemble **style** des réglages (cf. `_STYLE_KEYS`).

    Clé de fraîcheur du rendu neutre : ne change que si le profil DCP / HSL /
    Color Grading change — insensible à Temp/Tint/Exposure (qu'on neutralise).
    """
    dev = develop or {}
    subset = {k: dev[k] for k in _STYLE_KEYS if k in dev}
    payload = json.dumps(subset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1((payload + ANALYSIS_VERSION).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# (dé)sérialisation des dataclasses ↔ JSON
# --------------------------------------------------------------------------- #
def _tone_to_json(t: ToneStats | None) -> str | None:
    return json.dumps(asdict(t)) if t is not None else None


def _tone_from_json(s: str | None) -> ToneStats | None:
    return ToneStats(**json.loads(s)) if s else None


def _neutral_to_json(n: NeutralStats | None) -> str | None:
    return json.dumps(asdict(n)) if n is not None else None


def _neutral_from_json(s: str | None) -> NeutralStats | None:
    return NeutralStats(**json.loads(s)) if s else None


def _bands_to_json(bands: list[BandStats] | None) -> str | None:
    return json.dumps([asdict(b) for b in bands]) if bands is not None else None


def _bands_from_json(s: str | None) -> list[BandStats] | None:
    return [BandStats(**d) for d in json.loads(s)] if s else None


def _analysis_from_row(row: sqlite3.Row, scope: str) -> RenderAnalysis | None:
    """Reconstruit une RenderAnalysis depuis les colonnes `tone_<scope>` etc."""
    tone = _tone_from_json(row[f"tone_{scope}"])
    neutral = _neutral_from_json(row[f"neutral_{scope}"])
    bands = _bands_from_json(row[f"hsl_{scope}"])
    if tone is None and neutral is None and bands is None:
        return None
    return RenderAnalysis(tone=tone, neutral=neutral, bands=bands)


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
    profile_capture: str | None = None,
) -> None:
    """Insère/MAJ la ligne d'ancrage (métadonnées + snapshot develop).

    UPSERT (pas `INSERT OR REPLACE`) : préserve `is_seed`, sinon chaque réanalyse
    écraserait le marquage seed avec la valeur par défaut. `profile_dcp` et
    `hash_style` sont dérivés du snapshot develop.
    """
    exif = exif or {}
    dev = current_develop or {}
    conn.execute(
        """INSERT INTO LightroomPicture
           (uuid, path, catalog_path, exif_camera, exif_iso, exif_aperture,
            exif_shutter, exif_focal_length, profile_capture, profile_dcp,
            develop_json, hash_develop, hash_style, cached_at, is_seed)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 0)
           ON CONFLICT(uuid) DO UPDATE SET
               path=excluded.path, catalog_path=excluded.catalog_path,
               exif_camera=excluded.exif_camera, exif_iso=excluded.exif_iso,
               exif_aperture=excluded.exif_aperture, exif_shutter=excluded.exif_shutter,
               exif_focal_length=excluded.exif_focal_length,
               profile_capture=COALESCE(excluded.profile_capture, LightroomPicture.profile_capture),
               profile_dcp=excluded.profile_dcp, develop_json=excluded.develop_json,
               hash_develop=excluded.hash_develop, hash_style=excluded.hash_style,
               cached_at=excluded.cached_at""",
        (
            uuid, path, catalog_path, exif.get("camera"), exif.get("iso"),
            exif.get("aperture"), exif.get("shutter_speed"), exif.get("focal_length"),
            profile_capture, dev.get("CameraProfile"),
            json.dumps(dev), develop_hash(dev), style_hash(dev), time.time(),
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Seeds — marquage explicite (remplace l'heuristique WhiteBalance=="Custom")
# --------------------------------------------------------------------------- #
def set_seed(conn: sqlite3.Connection, uuid: str, value: bool) -> None:
    """Marque/démarque une photo comme seed. Crée la ligne d'ancrage si absente."""
    conn.execute(
        "INSERT INTO LightroomPicture (uuid, is_seed, cached_at) VALUES (?,?,?) "
        "ON CONFLICT(uuid) DO UPDATE SET is_seed=excluded.is_seed",
        (uuid, int(value), time.time()),
    )
    conn.commit()


def is_seed(conn: sqlite3.Connection, uuid: str) -> bool:
    row = conn.execute(
        "SELECT is_seed FROM LightroomPicture WHERE uuid=?", (uuid,)
    ).fetchone()
    return bool(row["is_seed"]) if row is not None else False


def list_seed_uuids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT uuid FROM LightroomPicture WHERE is_seed=1").fetchall()
    return [r["uuid"] for r in rows]


def get_picture(conn: sqlite3.Connection, uuid: str) -> Optional[dict[str, Any]]:
    """Ligne `LightroomPicture` (path, current_develop, is_seed…), sans contrôle de fraîcheur."""
    row = conn.execute("SELECT * FROM LightroomPicture WHERE uuid=?", (uuid,)).fetchone()
    if row is None:
        return None
    return {
        "uuid": row["uuid"], "path": row["path"], "catalog_path": row["catalog_path"],
        "current_develop": json.loads(row["develop_json"]) if row["develop_json"] else {},
        "profile_capture": row["profile_capture"], "profile_dcp": row["profile_dcp"],
        "hash_style": row["hash_style"], "is_seed": bool(row["is_seed"]),
    }


def get_source_raw_latest(conn: sqlite3.Connection, uuid: str) -> Optional[dict[str, Any]]:
    """Dernière analyse RAW connue pour `uuid`, **sans vérifier le hash de fraîcheur**.

    Utilisé pour les seeds (vecteur k-NN) : le RAW d'un seed change rarement après
    son marquage, et exiger une réanalyse à chaque correspondance serait coûteux.
    """
    row = conn.execute(
        "SELECT * FROM SourceRAW WHERE uuid=? ORDER BY cached_at DESC LIMIT 1", (uuid,)
    ).fetchone()
    return _source_raw_dict(row) if row is not None else None


def get_preview_jpeg_latest(conn: sqlite3.Connection, uuid: str) -> Optional[RenderAnalysis]:
    """Dernier aperçu rendu connu pour `uuid` (zone nette), sans vérifier le hash
    de fraîcheur (référence de style d'un seed — cf. `get_source_raw_latest`)."""
    row = conn.execute(
        "SELECT * FROM PreviewJPEG WHERE uuid=? ORDER BY cached_at DESC LIMIT 1", (uuid,)
    ).fetchone()
    return _analysis_from_row(row, "sharp") if row is not None else None


# --------------------------------------------------------------------------- #
# SourceRAW (pixels RAW : expo + as-shot WB + gray-world, global + zone nette)
# --------------------------------------------------------------------------- #
def _source_raw_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Mappe une ligne SourceRAW → dict consommateur.

    Clés **stables** pour les consommateurs existants (`seed_match`, worker) :
    `asshot_rg`/`asshot_bg`/`tone`/`bands`/`exposure`/`grayworld_rg`/`grayworld_bg`
    (= mesures globales, comportement historique) + clés `*_sharp` neuves.
    """
    exposure_global = (
        ExposureStats(
            row["luma_mean_global"], row["luma_median_global"],
            row["luma_clip_hi_global"], row["luma_clip_lo_global"],
        )
        if row["luma_mean_global"] is not None
        else None
    )
    exposure_sharp = (
        ExposureStats(
            row["luma_mean_sharp"], row["luma_median_sharp"],
            row["luma_clip_hi_sharp"], row["luma_clip_lo_sharp"],
        )
        if row["luma_mean_sharp"] is not None
        else None
    )
    return {
        "asshot_rg": row["wb_asshot_rg"], "asshot_bg": row["wb_asshot_bg"],
        "exposure": exposure_global, "exposure_sharp": exposure_sharp,
        "grayworld_rg": row["wb_grayworld_rg_global"], "grayworld_bg": row["wb_grayworld_bg_global"],
        "grayworld_rg_sharp": row["wb_grayworld_rg_sharp"],
        "grayworld_bg_sharp": row["wb_grayworld_bg_sharp"],
        "mask_sharp_frac": row["mask_sharp_frac"], "ev100": row["ev100"],
        "profile_capture": row["profile_capture"],
        "tone": _tone_from_json(row["tone_sharp"]),
        "bands": _bands_from_json(row["hsl_sharp"]),
    }


def get_source_raw(
    conn: sqlite3.Connection, uuid: str, hash_raw: str
) -> Optional[dict[str, Any]]:
    """Renvoie les scalaires RAW cachés si le `hash_raw` correspond, sinon None."""
    row = conn.execute(
        "SELECT * FROM SourceRAW WHERE uuid=? AND hash_raw=?", (uuid, hash_raw)
    ).fetchone()
    return _source_raw_dict(row) if row is not None else None


def put_source_raw(
    conn: sqlite3.Connection,
    uuid: str,
    hash_raw: str,
    *,
    asshot_rg: float | None,
    asshot_bg: float | None,
    exposure_global: ExposureStats | None = None,
    exposure_sharp: ExposureStats | None = None,
    grayworld_global: tuple[float, float] | None = None,
    grayworld_sharp: tuple[float, float] | None = None,
    mask_sharp_frac: float | None = None,
    ev100: float | None = None,
    profile_capture: str | None = None,
    tone: ToneStats | None = None,
    bands: list[BandStats] | None = None,
) -> None:
    """Écrit la ligne SourceRAW (paires global + zone nette). Tous les champs de
    mesure sont optionnels (l'autocorrect peut n'écrire que la WB as-shot)."""
    gg = grayworld_global or (None, None)
    gs = grayworld_sharp or (None, None)
    conn.execute(
        """INSERT OR REPLACE INTO SourceRAW
           (uuid, hash_raw, wb_asshot_rg, wb_asshot_bg,
            luma_mean_global, luma_median_global, luma_clip_hi_global, luma_clip_lo_global,
            luma_mean_sharp, luma_median_sharp, luma_clip_hi_sharp, luma_clip_lo_sharp,
            wb_grayworld_rg_global, wb_grayworld_bg_global,
            wb_grayworld_rg_sharp, wb_grayworld_bg_sharp,
            mask_sharp_frac, ev100, profile_capture, tone_sharp, hsl_sharp, cached_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid, hash_raw, asshot_rg, asshot_bg,
            exposure_global.mean_luma if exposure_global else None,
            exposure_global.median_luma if exposure_global else None,
            exposure_global.clipped_highlights if exposure_global else None,
            exposure_global.clipped_shadows if exposure_global else None,
            exposure_sharp.mean_luma if exposure_sharp else None,
            exposure_sharp.median_luma if exposure_sharp else None,
            exposure_sharp.clipped_highlights if exposure_sharp else None,
            exposure_sharp.clipped_shadows if exposure_sharp else None,
            gg[0], gg[1], gs[0], gs[1],
            mask_sharp_frac, ev100, profile_capture,
            _tone_to_json(tone), _bands_to_json(bands), time.time(),
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# InCameraJPEG (JPEG boîtier : tone/neutral/bandes global + sharp + deltas vs RAW)
# --------------------------------------------------------------------------- #
def _delta_bands_to_json(deltas: list[dict[str, Any]] | None) -> str | None:
    return json.dumps(deltas) if deltas is not None else None


def get_in_camera_jpeg(
    conn: sqlite3.Connection, uuid: str, hash_jpeg: str
) -> Optional[dict[str, Any]]:
    """Ligne InCameraJPEG cachée si `hash_jpeg` correspond, sinon None.

    Retourne un dict : `sharp`/`global` (RenderAnalysis), deltas, `mask_sharp_frac`,
    `profile_capture`. La clé `tone`/`bands` (zone nette) est aussi exposée pour les
    consommateurs qui n'utilisent que la cible embedded.
    """
    row = conn.execute(
        "SELECT * FROM InCameraJPEG WHERE uuid=? AND hash_jpeg=?", (uuid, hash_jpeg)
    ).fetchone()
    if row is None:
        return None
    sharp = _analysis_from_row(row, "sharp")
    return {
        "sharp": sharp,
        "global": _analysis_from_row(row, "global"),
        "tone": sharp.tone if sharp else None,
        "bands": sharp.bands if sharp else None,
        "mask_sharp_frac": row["mask_sharp_frac"],
        "profile_capture": row["profile_capture"],
        "delta_luma_median": row["delta_luma_median"],
        "delta_wb_cast_a": row["delta_wb_cast_a"],
        "delta_wb_cast_b": row["delta_wb_cast_b"],
        "delta_hsl": json.loads(row["delta_hsl"]) if row["delta_hsl"] else None,
    }


def put_in_camera_jpeg(
    conn: sqlite3.Connection,
    uuid: str,
    hash_jpeg: str,
    *,
    sharp: RenderAnalysis | None,
    glob: RenderAnalysis | None = None,
    mask_sharp_frac: float | None = None,
    profile_capture: str | None = None,
    delta_luma_median: float | None = None,
    delta_wb_cast_a: float | None = None,
    delta_wb_cast_b: float | None = None,
    delta_hsl: list[dict[str, Any]] | None = None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO InCameraJPEG
           (uuid, hash_jpeg, tone_sharp, neutral_sharp, hsl_sharp,
            tone_global, neutral_global, hsl_global, mask_sharp_frac, profile_capture,
            delta_luma_median, delta_wb_cast_a, delta_wb_cast_b, delta_hsl, cached_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid, hash_jpeg,
            _tone_to_json(sharp.tone) if sharp else None,
            _neutral_to_json(sharp.neutral) if sharp else None,
            _bands_to_json(sharp.bands) if sharp else None,
            _tone_to_json(glob.tone) if glob else None,
            _neutral_to_json(glob.neutral) if glob else None,
            _bands_to_json(glob.bands) if glob else None,
            mask_sharp_frac, profile_capture,
            delta_luma_median, delta_wb_cast_a, delta_wb_cast_b,
            _delta_bands_to_json(delta_hsl), time.time(),
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# PreviewJPEG / NeutralPreviewJPEG (rendus : analyse complète global + sharp)
# --------------------------------------------------------------------------- #
def _put_render_dual(
    conn: sqlite3.Connection,
    table: str,
    uuid: str,
    key_col: str,
    key_val: str,
    sharp: RenderAnalysis | None,
    glob: RenderAnalysis | None,
    mask_sharp_frac: float | None,
) -> None:
    conn.execute(
        f"""INSERT OR REPLACE INTO {table}
            (uuid, {key_col}, tone_sharp, neutral_sharp, hsl_sharp,
             tone_global, neutral_global, hsl_global, mask_sharp_frac, cached_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid, key_val,
            _tone_to_json(sharp.tone) if sharp else None,
            _neutral_to_json(sharp.neutral) if sharp else None,
            _bands_to_json(sharp.bands) if sharp else None,
            _tone_to_json(glob.tone) if glob else None,
            _neutral_to_json(glob.neutral) if glob else None,
            _bands_to_json(glob.bands) if glob else None,
            mask_sharp_frac, time.time(),
        ),
    )
    conn.commit()


def get_preview_jpeg(
    conn: sqlite3.Connection, uuid: str, hash_preview: str
) -> Optional[RenderAnalysis]:
    """Aperçu rendu (zone nette) si `hash_preview` correspond, sinon None."""
    row = conn.execute(
        "SELECT * FROM PreviewJPEG WHERE uuid=? AND hash_preview=?", (uuid, hash_preview)
    ).fetchone()
    return _analysis_from_row(row, "sharp") if row is not None else None


def put_preview_jpeg(
    conn: sqlite3.Connection,
    uuid: str,
    hash_preview: str,
    *,
    sharp: RenderAnalysis,
    glob: RenderAnalysis | None = None,
    mask_sharp_frac: float | None = None,
) -> None:
    _put_render_dual(conn, "PreviewJPEG", uuid, "hash_preview", hash_preview,
                     sharp, glob, mask_sharp_frac)


def get_neutral_preview(
    conn: sqlite3.Connection, uuid: str, hash_style: str
) -> Optional[dict[str, Any]]:
    """Rendu neutre (WB As Shot / Exp 0 / HSL 0, style intact) si `hash_style` correspond.

    Retourne un dict : `sharp`/`glob` (RenderAnalysis), `asshot_temp`/`asshot_tint`
    (WB numérique de l'As Shot, lue par le plugin pendant le probe), `mask_sharp_frac`.
    """
    row = conn.execute(
        "SELECT * FROM NeutralPreviewJPEG WHERE uuid=? AND hash_style=?", (uuid, hash_style)
    ).fetchone()
    if row is None:
        return None
    return {
        "sharp": _analysis_from_row(row, "sharp"),
        "glob": _analysis_from_row(row, "global"),
        "asshot_temp": row["wb_asshot_temp"],
        "asshot_tint": row["wb_asshot_tint"],
        "mask_sharp_frac": row["mask_sharp_frac"],
    }


def put_neutral_preview(
    conn: sqlite3.Connection,
    uuid: str,
    hash_style: str,
    *,
    sharp: RenderAnalysis,
    glob: RenderAnalysis | None = None,
    mask_sharp_frac: float | None = None,
    asshot_temp: float | None = None,
    asshot_tint: float | None = None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO NeutralPreviewJPEG
           (uuid, hash_style, tone_sharp, neutral_sharp, hsl_sharp,
            tone_global, neutral_global, hsl_global, mask_sharp_frac,
            wb_asshot_temp, wb_asshot_tint, cached_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid, hash_style,
            _tone_to_json(sharp.tone) if sharp else None,
            _neutral_to_json(sharp.neutral) if sharp else None,
            _bands_to_json(sharp.bands) if sharp else None,
            _tone_to_json(glob.tone) if glob else None,
            _neutral_to_json(glob.neutral) if glob else None,
            _bands_to_json(glob.bands) if glob else None,
            mask_sharp_frac, asshot_temp, asshot_tint, time.time(),
        ),
    )
    conn.commit()


def get_bias_pool(
    conn: sqlite3.Connection, hash_style: str, profile_capture: str | None
) -> list[dict[str, Any]]:
    """Pool de calibration du **biais de profil** : photos du cache partageant le
    couple (profil créatif boîtier, style Lr) et disposant à la fois de la cible
    (`InCameraJPEG`) et de l'ancre (`NeutralPreviewJPEG` au `hash_style` demandé).

    Retourne, par photo : `uuid`, `t_sharp`/`t_global` (JPEG boîtier) et
    `n_sharp`/`n_global` (rendu neutre) en RenderAnalysis. Le biais = médiane
    robuste des deltas T−N sur ce pool (calculée par `core.autocorrect`).
    """
    if profile_capture is None:
        where_prof, args = "j.profile_capture IS NULL", (hash_style,)
    else:
        where_prof, args = "j.profile_capture = ?", (hash_style, profile_capture)
    rows = conn.execute(
        f"""SELECT j.uuid AS uuid,
                   j.tone_sharp AS jt_sharp, j.neutral_sharp AS jn_sharp, j.hsl_sharp AS jb_sharp,
                   j.tone_global AS jt_global, j.neutral_global AS jn_global, j.hsl_global AS jb_global,
                   n.tone_sharp AS nt_sharp, n.neutral_sharp AS nn_sharp, n.hsl_sharp AS nb_sharp,
                   n.tone_global AS nt_global, n.neutral_global AS nn_global, n.hsl_global AS nb_global
            FROM NeutralPreviewJPEG n
            JOIN InCameraJPEG j ON j.uuid = n.uuid
            WHERE n.hash_style = ? AND {where_prof}""",
        args,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        def _ra(prefix: str, scope: str) -> RenderAnalysis | None:
            tone = _tone_from_json(r[f"{prefix}t_{scope}"])
            neutral = _neutral_from_json(r[f"{prefix}n_{scope}"])
            bands = _bands_from_json(r[f"{prefix}b_{scope}"])
            if tone is None and neutral is None and bands is None:
                return None
            return RenderAnalysis(tone=tone, neutral=neutral, bands=bands)
        out.append({
            "uuid": r["uuid"],
            "t_sharp": _ra("j", "sharp"), "t_global": _ra("j", "global"),
            "n_sharp": _ra("n", "sharp"), "n_global": _ra("n", "global"),
        })
    return out
