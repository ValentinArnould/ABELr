"""Équilibrage d'exposition — deux chemins.

**Primaire (espace rendu)** : l'exposition perçue vit dans le rendu (profil DCP +
courbe + curseurs), pas en scène-linéaire. On mesure la clarté CIE **L*** du rendu
(`render_metrics.tone_stats`), on vise la **médiane L* des seeds** (repli : clarté du
JPEG boîtier), et on traduit l'écart en ΔExposure2012 via la **réponse calibrée**
`∂L*/∂EV` (`core.response`). Un garde-fou **headroom** (clipping RAW) borne la poussée.
→ `build_target` + `plan_from_render`. C'est le chemin tenant compte du profil et de
tous les réglages appliqués (puisqu'on mesure le rendu qui les contient déjà).

**Repli/legacy (médiane EV)** : médiane des `Exposure2012` des seeds, appliquée telle
quelle. Valable seulement si contenu et base d'exposition homogènes sur l'event ;
conservé comme repli rapide sans rendu. → `collect_exposures` + `calibrate` + `plan_adjustments`.

Seed = `core.seeds.is_seed` (WB Custom) ou sélection explicite, cohérent avec la WB.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import seeds as _seeds
from .response import ExposureResponse
from ..server.models import PhotoAdjustment, PhotoResult


# =========================================================================== #
# Chemin primaire — espace rendu (cible L* + réponse ∂L*/∂EV + headroom)
# =========================================================================== #
# Seuils de clipping RAW (fractions) au-delà desquels on bride la correction.
_HI_LIMIT = 0.02
_LO_LIMIT = 0.02
# Pas d'exposition maximal par photo (sécurité contre une mesure aberrante).
_MAX_STEP_EV = 2.0
# Minimum de seeds pour fixer la cible sur les seeds plutôt que sur le JPEG boîtier.
_MIN_SEEDS = 3


@dataclass
class ExposureSample:
    """Mesure d'une photo (seed ou cible), fournie par le worker.

    `current_l`  : médiane L* du **rendu LR** courant.
    `embedded_l` : médiane L* du **JPEG boîtier** (exposition métrée par l'appareil
                   pour CETTE scène) — référence appariée au contenu. None si absente.
    """

    photo_id: str
    current_l: float
    current_exposure: float
    embedded_l: float | None = None
    clipped_hi: float = 0.0   # fraction hautes lumières écrêtées (RAW) — headroom
    clipped_lo: float = 0.0   # fraction ombres bouchées (RAW) — headroom


@dataclass
class ExposureTarget:
    """Cible d'exposition de l'event.

    Mode **offset** (robuste, par défaut quand le JPEG boîtier est dispo) : on vise un
    écart constant rendu−boîtier `target_offset` ; la cible L* de chaque photo devient
    `embedded_l + target_offset` → **appariée au contenu** (le boîtier a métré chaque
    scène). Évite d'aplatir les différences de luminosité légitimes entre scènes.

    Mode **absolu** (repli quand aucun JPEG boîtier) : `target_l` constant pour toutes.
    Moins fiable car contaminé par le contenu (validé : la L* rendue corrèle à 0.92 avec
    la L* boîtier sur CGC → la dispersion est surtout du contenu, pas de la dérive).
    """

    target_offset: float | None   # mode offset : médiane des (rendu−boîtier) des seeds
    target_l: float | None        # mode absolu : médiane L* rendue des seeds
    source: str                   # "seeds" | "camera" | "absolute"
    n_seeds: int


def build_target(
    seed_samples: list[ExposureSample],
    fallback_lstars: list[float] | None = None,
    min_seeds: int = _MIN_SEEDS,
) -> ExposureTarget:
    """Construit la cible d'exposition à partir des seeds.

    Priorité (décision utilisateur : seeds d'abord, JPEG boîtier ensuite) :
    1. Seeds avec JPEG boîtier → `target_offset = médiane(rendu − boîtier)` des seeds.
    2. Pas de seed mais boîtier dispo sur les cibles → `target_offset = 0` (« recaler
       chaque photo sur l'exposition métrée par l'appareil »).
    3. Aucun boîtier → mode absolu : `target_l = médiane(L* rendue des seeds)` (ou
       `fallback_lstars`). Lève ValueError si rien.
    """
    seed_offsets = [s.current_l - s.embedded_l for s in seed_samples if s.embedded_l is not None]
    if seed_offsets:
        return ExposureTarget(float(np.median(seed_offsets)), None, "seeds", len(seed_offsets))

    seed_ls = [s.current_l for s in seed_samples]
    if not seed_samples:
        # Pas de seed : si le boîtier sera dispo sur les cibles, viser offset 0 (recaler caméra).
        if fallback_lstars:
            return ExposureTarget(None, float(np.median(fallback_lstars)), "absolute", 0)
        return ExposureTarget(0.0, None, "camera", 0)
    if seed_ls:  # seeds sans boîtier → cible absolue sur leur L* rendue
        return ExposureTarget(None, float(np.median(seed_ls)), "absolute", len(seed_ls))
    if fallback_lstars:
        return ExposureTarget(None, float(np.median(fallback_lstars)), "absolute", 0)
    raise ValueError("Aucune cible d'exposition : ni seed mesuré, ni JPEG boîtier.")


def _headroom_factor(clip: float, limit: float) -> float:
    """Facteur [0, 1] : 1 sous le seuil, décroît jusqu'à 0 à 2× le seuil."""
    if clip <= limit:
        return 1.0
    return max(0.0, 1.0 - (clip - limit) / limit)


def _desired_l(sample: ExposureSample, target: ExposureTarget) -> float:
    """Clarté L* visée pour cette photo (appariée au contenu si possible)."""
    if target.target_offset is not None and sample.embedded_l is not None:
        return sample.embedded_l + target.target_offset
    if target.target_l is not None:
        return target.target_l
    # offset défini mais photo sans boîtier → pas de cible appariée : ne rien changer.
    return sample.current_l


def plan_from_render(
    samples: list[ExposureSample],
    target: ExposureTarget,
    resp: ExposureResponse | None = None,
    max_step_ev: float = _MAX_STEP_EV,
    hi_limit: float = _HI_LIMIT,
    lo_limit: float = _LO_LIMIT,
) -> list[PhotoAdjustment]:
    """Planifie Exposure2012 pour amener chaque photo à sa clarté **cible appariée**.

    Cible par photo = `embedded_l + target_offset` (contenu apparié) ou `target_l`
    (absolu, repli). ΔEV = `resp.solve_dev(L* courant → cible)`, borné à ±`max_step_ev`,
    puis atténué par le headroom RAW (ne pousse pas vers un clipping déjà présent). Le
    nouvel Exposure2012 cumule le delta sur la valeur courante (le rendu mesuré reflète
    déjà l'Exposure2012 courant).
    """
    resp = resp or ExposureResponse()
    out: list[PhotoAdjustment] = []
    for s in samples:
        desired = _desired_l(s, target)
        dev = resp.solve_dev(s.current_l, desired)
        dev = float(np.clip(dev, -max_step_ev, max_step_ev))
        if dev > 0:
            dev *= _headroom_factor(s.clipped_hi, hi_limit)
        elif dev < 0:
            dev *= _headroom_factor(s.clipped_lo, lo_limit)
        new_ev = round(s.current_exposure + dev, 2)
        out.append(PhotoAdjustment(photo_id=s.photo_id, develop={"Exposure2012": new_ev}))
    return out


# =========================================================================== #
# Chemin legacy/repli — médiane des Exposure2012 des seeds (sans rendu)
# =========================================================================== #
@dataclass
class ExposureCalibration:
    """Exposition calibrée sur les seeds (médiane des Exposure2012)."""

    exposure: float
    spread_ev: float
    n_seeds: int


def _exposure_of(develop: dict) -> float | None:
    for k in ("Exposure2012", "Exposure"):
        v = develop.get(k)
        if v is not None:
            return float(v)
    return None


def collect_exposures(
    photos: list[PhotoResult],
    seed_ids: set[str] | None = None,
) -> tuple[list[float], list[PhotoResult]]:
    """Sépare (valeurs EV des seeds, photos à corriger). Pas de décodage RAW."""
    exposures: list[float] = []
    others: list[PhotoResult] = []
    for p in photos:
        dev = p.current_develop or {}
        chosen = (p.photo_id in seed_ids) if seed_ids is not None else _seeds.is_seed(dev)
        ev = _exposure_of(dev)
        if chosen and ev is not None:
            exposures.append(ev)
        else:
            others.append(p)
    return exposures, others


def calibrate(exposures: list[float]) -> ExposureCalibration:
    """Calibre l'exposition depuis les EV des seeds (médiane robuste)."""
    if not exposures:
        raise ValueError(
            "Aucun seed d'exposition. Corrigez l'exposition d'au moins une photo "
            "(WhiteBalance = Custom) ou sélectionnez-la comme référence."
        )
    arr = np.asarray(exposures, np.float64)
    return ExposureCalibration(
        exposure=float(np.median(arr)),
        spread_ev=float(np.std(arr)) if len(arr) > 1 else 0.0,
        n_seeds=len(arr),
    )


def plan_adjustments(
    photos: list[PhotoResult],
    cal: ExposureCalibration,
) -> list[PhotoAdjustment]:
    """Applique l'exposition calibrée (constante) à chaque photo cible (repli sans rendu)."""
    ev = round(cal.exposure, 2)
    return [
        PhotoAdjustment(photo_id=p.photo_id, develop={"Exposure2012": ev})
        for p in photos
    ]
