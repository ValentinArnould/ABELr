"""Orchestration de la correction automatique par photo (exposition + WB + HSL).

Pur et testable (comme `exposure`/`hsl`/`seed_match`) : reçoit les mesures déjà
collectées par le worker GUI (+ le pool de seeds déjà construit depuis le cache)
et renvoie un `PhotoAdjustment` par photo + un diagnostic. Le worker Qt
(`gui.autocorrect_worker`) se charge des I/O (jobs, décodage, cache, parallélisme).

Modes de référence (décision utilisateur) :
- **seeds** : un pool de seeds exploitables existe (marqués explicitement, cf.
  `cache.is_seed` — plus l'heuristique `WhiteBalance=="Custom"`) → pour chaque
  photo cible, on cherche les seeds dont l'analyse RAW (zone nette) est la plus
  proche (`core.seed_match`), et on utilise leur aperçu rendu déjà retouché
  comme référence de style. Les seeds eux-mêmes ne sont JAMAIS réécrits.
- **embedded** (forcé OU aucun seed exploitable) : chaque photo est recalée sur
  son **JPEG boîtier** (exposition/WB/couleurs appareil).

Axes activables indépendamment. Les deltas HSL s'ajoutent aux valeurs de curseur
courantes (`m.current_develop`) — qui doivent être fournies à jour par le worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import hsl as _hsl
from . import exposure as _exp
from . import seed_match
from . import wb_model as _wb
from .hsl import BandTarget
from .pipeline import RenderAnalysis
from .render_metrics import BandStats, ToneStats, band_is_reliable
from .response import ResponseModel
from .seed_match import SeedTarget, SeedVector
from ..server.models import PhotoAdjustment

DEFAULT_AXES = frozenset({"expo", "wb", "hsl"})


@dataclass
class PhotoMeasure:
    """Mesures d'une photo, collectées par le worker (rendu courant + RAW + boîtier)."""

    photo_id: str
    path: str
    current_develop: dict
    exif_camera: str | None
    analysis: RenderAnalysis                 # rendu courant (tone/neutral/bands), zone nette
    is_seed: bool = False                    # marquage explicite (cache.is_seed)
    raw_tone: ToneStats | None = None        # RAW source, zone nette — clé du matching k-NN
    embedded_tone: ToneStats | None = None   # JPEG boîtier — clarté (cible expo embedded)
    embedded_bands: list[BandStats] | None = None  # JPEG boîtier — bandes (cible HSL embedded)
    asshot_rg: float | None = None
    asshot_bg: float | None = None
    profile_capture: str | None = None       # profil créatif boîtier (filtre k-NN)
    ev100: float | None = None               # contexte scène (diagnostic)


@dataclass
class PlanDiagnostics:
    mode: str                       # "seeds" | "embedded"
    n_seeds: int
    n_targets: int
    notes: list[str] = field(default_factory=list)


def _f(dev: dict, key: str, default: float = 0.0) -> float:
    v = (dev or {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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


def _band_targets_from_seed_match(t: SeedTarget | None) -> dict[str, BandTarget]:
    """Cibles HSL = bandes agrégées des seeds les plus proches (déjà pondérées)."""
    out: dict[str, BandTarget] = {}
    if t is None or not t.bands:
        return out
    for b in t.bands:
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
    seed_pool: list[SeedVector] | None = None,
) -> tuple[list[PhotoAdjustment], PlanDiagnostics]:
    """Planifie la correction par photo. Voir le docstring du module pour les modes."""
    seed_pool = seed_pool or []
    targets = [m for m in measures if not m.is_seed]
    mode_embedded = forced_embedded or not seed_pool

    dev_by_id: dict[str, dict] = {m.photo_id: {} for m in targets}
    diag = PlanDiagnostics(
        mode="embedded" if mode_embedded else "seeds",
        n_seeds=len(seed_pool),
        n_targets=len(targets),
    )
    if mode_embedded:
        reason = "case cochée" if forced_embedded else "aucun seed exploitable"
        diag.notes.insert(0, f"mode JPEG embarqué ({reason})")
    else:
        diag.notes.insert(0, f"mode seeds — pool de {len(seed_pool)} seed(s)")

    # Cible k-NN par photo, calculée une fois et réutilisée par les 3 axes.
    match_cache: dict[str, SeedTarget | None] = {}

    def _match(m: PhotoMeasure) -> SeedTarget | None:
        if m.photo_id not in match_cache:
            query = SeedVector(
                photo_id=m.photo_id,
                asshot_rg=m.asshot_rg,
                asshot_bg=m.asshot_bg,
                raw_median_l=m.raw_tone.median_l if m.raw_tone else None,
                temperature=None, tint=None, preview_tone=None, preview_bands=None,
                profile_capture=m.profile_capture, ev100=m.ev100,
            )
            match_cache[m.photo_id] = seed_match.match_target(query, seed_pool)
        return match_cache[m.photo_id]

    # ---- Exposition --------------------------------------------------------
    if "expo" in axes:
        samples = []
        n_resolved = 0
        for m in targets:
            if mode_embedded:
                desired = m.embedded_tone.median_l if m.embedded_tone else None
            else:
                t = _match(m)
                desired = t.tone.median_l if (t and t.tone) else None
            if desired is not None:
                n_resolved += 1
            samples.append(
                _exp.ExposureSample(
                    m.photo_id, m.analysis.tone.median_l, _f(m.current_develop, "Exposure2012"),
                    desired_l=desired,
                    clipped_hi=m.analysis.tone.clipped_hi, clipped_lo=m.analysis.tone.clipped_lo,
                )
            )
        for adj in _exp.plan_from_render(samples, model.exposure if model else None):
            dev_by_id[adj.photo_id].update(adj.develop)
        diag.notes.append(f"expo: {n_resolved}/{len(targets)} cible(s) résolue(s)")

    # ---- Balance des blancs -----------------------------------------------
    if "wb" in axes:
        if mode_embedded:
            for m in targets:
                dev_by_id[m.photo_id]["WhiteBalance"] = "As Shot"
            diag.notes.append("wb: As Shot (réf appareil)")
        else:
            n_wb = 0
            wbresp = model.wb if model else None
            for m in targets:
                t = _match(m)
                if t is None or t.temperature is None:
                    continue
                temp = t.temperature
                tint = t.tint if t.tint is not None else 0.0
                if wbresp is not None:
                    temp, tint, _ = _wb.refine_temp_tint(temp, tint, m.analysis.neutral, wbresp)
                dev_by_id[m.photo_id].update(
                    WhiteBalance="Custom", Temperature=round(temp), Tint=round(tint)
                )
                n_wb += 1
            diag.notes.append(f"wb: {n_wb}/{len(targets)} photo(s) matchée(s) (k-NN seeds)")

    # ---- HSL ---------------------------------------------------------------
    if "hsl" in axes:
        n_hsl = 0
        for m in targets:
            tgs = _embedded_band_targets(m) if mode_embedded else _band_targets_from_seed_match(_match(m))
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
