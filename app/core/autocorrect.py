"""Orchestration de la correction automatique par photo (exposition + WB + HSL).

Pur et testable (comme `exposure`/`hsl`/`wb_model`) : reçoit les mesures déjà
collectées par le worker GUI et renvoie un `PhotoAdjustment` par photo + un diagnostic.
Le worker Qt (`gui.autocorrect_worker`) se charge des I/O (jobs, décodage, parallélisme).

Modes de référence (décision utilisateur) :
- **seeds** : la sélection contient des photos déjà retouchées (`seeds.is_seed`) → elles
  servent de modèle de style ; les autres s'alignent dessus (les seeds NE sont PAS réécrites).
- **embedded** (forcé OU aucun seed) : chaque photo est recalée sur son **JPEG boîtier**
  (exposition/WB/couleurs appareil) + bridage de sursaturation.

Axes activables indépendamment. Les deltas HSL s'ajoutent aux valeurs de curseur courantes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import exposure as _exp
from . import hsl as _hsl
from . import regime as _regime
from . import seeds as _seeds
from . import wb_model as _wb
from .hsl import BandTarget
from .pipeline import RenderAnalysis
from .render_metrics import BandStats, ToneStats, band_is_reliable
from .response import ResponseModel
from ..server.models import PhotoAdjustment

DEFAULT_AXES = frozenset({"expo", "wb", "hsl"})


@dataclass
class PhotoMeasure:
    """Mesures d'une photo, collectées par le worker (rendu courant + RAW + boîtier)."""

    photo_id: str
    path: str
    current_develop: dict
    exif_camera: str | None
    analysis: RenderAnalysis                 # rendu courant (tone/neutral/bands)
    embedded_tone: ToneStats | None = None   # JPEG boîtier — clarté (cible expo embedded)
    embedded_bands: list[BandStats] | None = None  # JPEG boîtier — bandes (cible HSL embedded)
    asshot_rg: float | None = None
    asshot_bg: float | None = None


@dataclass
class PlanDiagnostics:
    mode: str                       # "seeds" | "embedded"
    n_seeds: int
    n_targets: int
    regime: str | None = None
    notes: list[str] = field(default_factory=list)


def _f(dev: dict, key: str, default: float = 0.0) -> float:
    v = (dev or {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _circular_mean_deg(values: list[float]) -> float:
    """Moyenne circulaire de teintes (degrés) — correcte au passage 0/360."""
    if not values:
        return 0.0
    ang = [math.radians(v) for v in values]
    s = sum(math.sin(a) for a in ang)
    c = sum(math.cos(a) for a in ang)
    return math.degrees(math.atan2(s, c)) % 360.0


def _seed_band_targets(seed_ms: list[PhotoMeasure]) -> dict[str, BandTarget]:
    """Cibles HSL par bande = médiane (clarté/chroma) + moyenne circulaire (hue) des seeds."""
    acc: dict[str, dict[str, list[float]]] = {}
    for m in seed_ms:
        for b in m.analysis.bands:
            if not band_is_reliable(b):
                continue
            d = acc.setdefault(b.name, {"chroma": [], "l": [], "hue": []})
            d["chroma"].append(b.median_chroma)
            d["l"].append(b.median_l)
            d["hue"].append(b.median_hue)
    out: dict[str, BandTarget] = {}
    for name, d in acc.items():
        ch = sorted(d["chroma"])
        ls = sorted(d["l"])
        out[name] = BandTarget(
            name=name,
            chroma=ch[len(ch) // 2],
            lstar=ls[len(ls) // 2],
            hue=_circular_mean_deg(d["hue"]),
        )
    return out


def _embedded_band_targets(m: PhotoMeasure) -> dict[str, BandTarget]:
    """Cibles HSL = bandes du JPEG boîtier de la photo (réduire vers le rendu appareil)."""
    out: dict[str, BandTarget] = {}
    for b in m.embedded_bands or []:
        if not band_is_reliable(b):
            continue
        out[b.name] = BandTarget(
            name=b.name, chroma=b.median_chroma, lstar=b.median_l, hue=b.median_hue
        )
    return out


def plan(
    measures: list[PhotoMeasure],
    *,
    axes: frozenset[str] = DEFAULT_AXES,
    forced_embedded: bool = False,
    model: ResponseModel | None = None,
    camera: str | None = None,
) -> tuple[list[PhotoAdjustment], PlanDiagnostics]:
    """Planifie la correction par photo. Voir le docstring du module pour les modes."""
    seed_ids = {m.photo_id for m in measures if _seeds.is_seed(m.current_develop or {})}
    seed_ms = [m for m in measures if m.photo_id in seed_ids]
    mode_embedded = forced_embedded or not seed_ms
    targets = measures if mode_embedded else [m for m in measures if m.photo_id not in seed_ids]

    dev_by_id: dict[str, dict] = {m.photo_id: {} for m in targets}
    diag = PlanDiagnostics(
        mode="embedded" if mode_embedded else "seeds",
        n_seeds=len(seed_ms),
        n_targets=len(targets),
    )

    # ---- Exposition --------------------------------------------------------
    if "expo" in axes:
        seed_samples = [] if mode_embedded else [
            _exp.ExposureSample(
                m.photo_id, m.analysis.tone.median_l, _f(m.current_develop, "Exposure2012"),
                embedded_l=(m.embedded_tone.median_l if m.embedded_tone else None),
            )
            for m in seed_ms
        ]
        try:
            tgt = _exp.build_target(seed_samples)
            samples = [
                _exp.ExposureSample(
                    m.photo_id, m.analysis.tone.median_l, _f(m.current_develop, "Exposure2012"),
                    embedded_l=(m.embedded_tone.median_l if m.embedded_tone else None),
                    clipped_hi=m.analysis.tone.clipped_hi, clipped_lo=m.analysis.tone.clipped_lo,
                )
                for m in targets
            ]
            for adj in _exp.plan_from_render(samples, tgt, model.exposure if model else None):
                dev_by_id[adj.photo_id].update(adj.develop)
            off = "—" if tgt.target_offset is None else f"{tgt.target_offset:+.1f}"
            diag.notes.append(f"expo: réf {tgt.source}, offset L* {off}")
        except ValueError as exc:
            diag.notes.append(f"expo ignorée: {exc}")

    # ---- Balance des blancs -----------------------------------------------
    if "wb" in axes:
        if mode_embedded:
            for m in targets:
                dev_by_id[m.photo_id]["WhiteBalance"] = "As Shot"
            diag.notes.append("wb: As Shot (réf appareil)")
        else:
            seeds_list = [
                _wb.Seed(
                    m.photo_id, m.asshot_rg, m.asshot_bg or 0.0,
                    _f(m.current_develop, "Temperature", 5500), _f(m.current_develop, "Tint"),
                    _f(m.current_develop, "Exposure2012"),
                )
                for m in seed_ms if m.asshot_rg is not None
            ]
            if seeds_list:
                cam = camera or (measures[0].exif_camera if measures else None)
                cal = _wb.calibrate(seeds_list, _wb.slope_for_camera(cam))
                rep = _regime.detect(cal)
                diag.regime = rep.regime.value
                use_model = rep.regime is not _regime.Regime.ARTISTIC
                wbresp = model.wb if model else None
                for m in targets:
                    if use_model and m.asshot_rg is not None:
                        temp = cal.predict_temperature(m.asshot_rg)
                    else:
                        temp = cal.median_temp_k
                    tint = cal.tint
                    if wbresp is not None:
                        temp, tint, _ = _wb.refine_temp_tint(temp, tint, m.analysis.neutral, wbresp)
                    dev_by_id[m.photo_id].update(
                        WhiteBalance="Custom", Temperature=round(temp), Tint=round(tint)
                    )
                diag.notes.append(f"wb: modèle seeds, régime {rep.regime.value}")
            else:
                diag.notes.append("wb ignorée: aucun seed exploitable (as-shot manquant)")

    # ---- HSL ---------------------------------------------------------------
    if "hsl" in axes:
        seed_targets = None if mode_embedded else _seed_band_targets(seed_ms)
        n_hsl = 0
        for m in targets:
            tgs = _embedded_band_targets(m) if mode_embedded else seed_targets
            deltas, _corrs = _hsl.plan_hsl(m.analysis.bands, tgs, model)
            for key, d in deltas.items():
                cur = _f(m.current_develop, key, 0.0)
                dev_by_id[m.photo_id][key] = int(max(-100, min(100, round(cur + d))))
            if deltas:
                n_hsl += 1
        diag.notes.append(f"hsl: {n_hsl}/{len(targets)} photo(s) ajustée(s)")

    adjustments = [
        PhotoAdjustment(photo_id=pid, develop=dev) for pid, dev in dev_by_id.items() if dev
    ]
    return adjustments, diag
