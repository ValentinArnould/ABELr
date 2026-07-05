"""Modèles Pydantic — contrats JSON échangés entre l'App et le plugin Lr.

Clés JSON en snake_case. Les noms de paramètres develop SDK Lr restent en PascalCase
(ex. Exposure, Temperature) à l'intérieur du dict `develop`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class JobType(str, Enum):
    """Types de jobs envoyés au plugin via polling."""

    GET_SELECTED_PHOTOS = "get_selected_photos"
    GET_CATALOG_PHOTOS = "get_catalog_photos"  # toutes les photos du catalogue actif
    GET_THUMBNAILS = "get_thumbnails"           # miniatures JPEG de la sélection (→ analyse preview)
    RENDER_PROBE = "render_probe"               # applique des réglages temp → miniature → restaure (calage réponse)
    APPLY_ADJUSTMENTS = "apply_adjustments"
    TEST = "test"  # ping plugin : déclenche une popup Hello World côté Lr


class JobStatus(str, Enum):
    """Cycle de vie d'un job côté App."""

    PENDING = "pending"      # en attente que le plugin le récupère
    IN_PROGRESS = "in_progress"  # récupéré par le plugin, résultat attendu
    DONE = "done"            # résultat reçu
    FAILED = "failed"        # le plugin a signalé une erreur


class Job(BaseModel):
    """Job poussé dans la queue, récupéré par le plugin via GET /jobs/pending."""

    job_id: str
    type: JobType
    # Charge utile spécifique au type (ex. adjustments pour apply_adjustments).
    payload: dict[str, Any] = Field(default_factory=dict)


class ExifData(BaseModel):
    iso: Optional[int] = None
    aperture: Optional[float] = None
    shutter_speed: Optional[str] = None
    focal_length: Optional[float] = None
    camera: Optional[str] = None


class PhotoResult(BaseModel):
    """Données d'une photo retournées par le plugin."""

    photo_id: str
    path: str
    # Chemin du .lrcat actif — permet à l'App de localiser les bundles
    # Previews.lrdata / Smart Previews.lrdata associés à cette photo.
    catalog_path: Optional[str] = None
    exif: ExifData = Field(default_factory=ExifData)
    # Develop settings courants — clés PascalCase SDK Lr.
    current_develop: dict[str, Any] = Field(default_factory=dict)


class ThumbnailResult(BaseModel):
    """Miniature JPEG écrite par le plugin pour une photo (jobs get_thumbnails / render_probe)."""

    photo_id: str
    thumbnail_path: Optional[str] = None  # chemin absolu local du JPEG, ou None si erreur
    error: Optional[str] = None
    # Renseignés par render_probe : Temperature/Tint numériques relues après l'apply
    # (si le probe pose WhiteBalance='As Shot', c'est la valeur numérique de l'As Shot).
    asshot_temp: Optional[float] = None
    asshot_tint: Optional[float] = None


class JobResult(BaseModel):
    """Résultat soumis par le plugin via POST /jobs/{id}/result."""

    job_id: str
    status: str = "ok"  # "ok" | "error"
    error: Optional[str] = None
    photos: list[PhotoResult] = Field(default_factory=list)
    thumbnails: list[ThumbnailResult] = Field(default_factory=list)
    # Renseignés par le job apply_adjustments (diagnostic d'application).
    applied: Optional[int] = None
    matched: Optional[int] = None
    total: Optional[int] = None


class PhotoAdjustment(BaseModel):
    """Ajustements à appliquer à une photo — clés develop en PascalCase SDK."""

    photo_id: str
    develop: dict[str, Any]
