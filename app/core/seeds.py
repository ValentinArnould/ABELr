"""Collecte des seeds et planification des corrections WB/expo.

Un *seed* = une photo que le photographe a corrigée à la main et qui sert de
référence pour calibrer l'event (cf. `core.wb_model`). On l'identifie soit par
sélection explicite dans le GUI, soit par heuristique (WhiteBalance = "Custom" =
WB personnalisée par l'utilisateur).

Flux : `collect_seeds` lit le réglage choisi + décode l'as-shot WB de chaque seed
→ `wb_model.calibrate` → `plan_adjustments` calcule Temperature/Tint/Exposure pour
les photos restantes (non-seeds) et produit les `PhotoAdjustment` à appliquer.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import raw, wb_model
from .wb_model import Seed, WBCalibration
from ..server.models import PhotoAdjustment, PhotoResult

# Valeurs de WhiteBalance (SDK Lr) indiquant une WB posée par l'utilisateur.
_USER_WB = {"Custom"}


def _develop_get(develop: dict, *keys):
    """Premier des `keys` présent dans le dict develop (gère alias PV)."""
    for k in keys:
        if develop.get(k) is not None:
            return develop[k]
    return None


def is_seed(current_develop: dict) -> bool:
    """Heuristique : la photo a-t-elle une WB posée à la main ?"""
    return str(current_develop.get("WhiteBalance", "")) in _USER_WB


def build_seed(photo: PhotoResult) -> Seed | None:
    """Construit un Seed depuis une PhotoResult (décode l'as-shot du RAW).

    Retourne None si Temperature absente (réglage illisible) ou RAW indécodable.
    """
    dev = photo.current_develop or {}
    temp = _develop_get(dev, "Temperature")
    if temp is None:
        return None
    try:
        rg, bg = raw.read_asshot_wb(photo.path)
    except Exception:
        return None
    return Seed(
        photo_id=photo.photo_id,
        asshot_rg=rg,
        asshot_bg=bg,
        temperature=float(temp),
        tint=float(_develop_get(dev, "Tint") or 0.0),
        exposure=float(_develop_get(dev, "Exposure2012", "Exposure") or 0.0),
    )


def collect_seeds(
    photos: list[PhotoResult],
    seed_ids: set[str] | None = None,
) -> tuple[list[Seed], list[PhotoResult]]:
    """Sépare seeds et photos à corriger.

    Si `seed_ids` est fourni, il prime (sélection explicite GUI) ; sinon on
    détecte par heuristique `is_seed`. Retourne (seeds, à_corriger). Les photos
    sans réglage lisible / RAW illisible sont écartées des seeds mais restent
    dans « à corriger ».
    """
    seeds: list[Seed] = []
    others: list[PhotoResult] = []
    for p in photos:
        chosen = (p.photo_id in seed_ids) if seed_ids is not None else is_seed(p.current_develop or {})
        if chosen:
            s = build_seed(p)
            if s is not None:
                seeds.append(s)
                continue  # seed valide : pas à corriger
        others.append(p)
    return seeds, others


def plan_adjustments(
    photos: list[PhotoResult],
    cal: WBCalibration,
    *,
    apply_exposure: bool = True,
    use_model: bool = True,
) -> list[PhotoAdjustment]:
    """Calcule les corrections WB (+ expo) pour des photos, depuis le modèle calibré.

    `use_model=True` (défaut, régime physique) : décode l'as-shot r/g de chaque RAW
    → Temperature prédite via slope·r/g+intercept.

    `use_model=False` (régime artistique) : Temperature fixe = médiane brute des seeds
    (`cal.median_temp_k`). Pas de lecture RAW nécessaire — plus rapide et correct quand
    l'as-shot n'a aucun pouvoir prédictif.

    `apply_exposure=False` pour ne corriger que la WB.
    """
    out: list[PhotoAdjustment] = []

    if not use_model:
        flat_temp = round(cal.median_temp_k, 0)
        for p in photos:
            develop: dict = {
                "Temperature": flat_temp,
                "Tint": round(cal.tint, 0),
                "WhiteBalance": "Custom",
            }
            if apply_exposure:
                develop["Exposure2012"] = round(cal.exposure, 2)
            out.append(PhotoAdjustment(photo_id=p.photo_id, develop=develop))
        return out

    for p in photos:
        try:
            rg, _bg = raw.read_asshot_wb(p.path)
        except Exception:
            continue
        develop: dict = {
            "Temperature": round(cal.predict_temperature(rg), 0),
            "Tint": round(cal.tint, 0),
            "WhiteBalance": "Custom",
        }
        if apply_exposure:
            develop["Exposure2012"] = round(cal.exposure, 2)
        out.append(PhotoAdjustment(photo_id=p.photo_id, develop=develop))
    return out
