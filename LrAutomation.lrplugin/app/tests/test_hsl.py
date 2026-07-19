"""Étape H1 du PLAN — garde `raw_oversat` câblée (anti-sur-correction saturation).

`plan_band` doit bloquer une réduction de saturation quand le RAW infirme
explicitement la sursaturation (`raw_oversat=False`), et se comporter comme avant
(aucun blocage) quand l'info RAW est absente (`raw_oversat=None`, comportement
historique) ou confirmée (`raw_oversat=True`).
"""

from __future__ import annotations

from app.core.hsl import BandTarget, plan_band, raw_confirms_oversat
from app.core.render_metrics import BandStats
from app.core.response import BandResponse


def _band(
    name: str = "Red",
    *,
    frac: float = 0.5,
    median_chroma: float = 40.0,
    median_hue: float = 0.0,
    sat_clip_frac: float = 0.0,
    median_sat: float = 0.5,
    median_l: float = 50.0,
) -> BandStats:
    return BandStats(
        name=name, frac=frac, median_hue=median_hue, median_chroma=median_chroma,
        median_sat=median_sat, sat_clip_frac=sat_clip_frac, median_l=median_l,
    )


def test_plan_band_reduces_saturation_when_excess_and_no_raw_info():
    # Comportement historique : pas d'info RAW (raw_oversat=None) → pas de blocage.
    stats = _band(median_chroma=40.0)
    target = BandTarget(name="Red", chroma=20.0, raw_oversat=None)
    corr = plan_band(stats, target, BandResponse())
    assert corr is not None
    assert corr.d_saturation < 0


def test_plan_band_reduces_saturation_when_raw_confirms_oversat():
    stats = _band(median_chroma=40.0)
    target = BandTarget(name="Red", chroma=20.0, raw_oversat=True)
    corr = plan_band(stats, target, BandResponse())
    assert corr is not None
    assert corr.d_saturation < 0


def test_plan_band_blocks_reduction_when_raw_denies_oversat():
    # Même excès de chroma mesuré que les cas ci-dessus, mais le RAW infirme →
    # la garde doit bloquer la réduction de saturation (pas de sur-correction).
    stats = _band(median_chroma=40.0)
    target = BandTarget(name="Red", chroma=20.0, raw_oversat=False)
    corr = plan_band(stats, target, BandResponse())
    assert corr is None or corr.d_saturation == 0


def test_plan_band_hard_clip_trigger_also_blocked_by_raw_denial():
    # Sursaturation "dure" détectée sur le rendu (sat_clip_frac élevé, sans cible de
    # chroma) : la garde raw_oversat=False doit bloquer même ce déclencheur.
    stats = _band(median_chroma=10.0, sat_clip_frac=0.5)
    target = BandTarget(name="Red", chroma=None, raw_oversat=False)
    corr = plan_band(stats, target, BandResponse())
    assert corr is None or corr.d_saturation == 0


def test_raw_confirms_oversat_none_without_raw_band():
    assert raw_confirms_oversat(None) is None


def test_raw_confirms_oversat_none_when_band_underpopulated():
    # frac sous le seuil minimal → pas assez de pixels RAW pour se prononcer.
    band = _band(frac=0.001, sat_clip_frac=0.9)
    assert raw_confirms_oversat(band) is None


def test_raw_confirms_oversat_true_on_hard_clip():
    band = _band(frac=0.5, sat_clip_frac=0.10)
    assert raw_confirms_oversat(band) is True


def test_raw_confirms_oversat_false_without_hard_clip():
    band = _band(frac=0.5, sat_clip_frac=0.0)
    assert raw_confirms_oversat(band) is False


# --------------------------------------------------------------------------- #
# H3 (PLAN) — transplant embedded (`BandTarget.embedded_raw=True`) plafonne plus
# strictement les deltas de luminance/teinte (pas de garde "réduction seule"
# possible sur ces axes comme pour la saturation).
# --------------------------------------------------------------------------- #
def test_plan_band_embedded_raw_caps_luminance_delta_tighter():
    # Cible JPEG boîtier très décalée en L* (+80) : sans le plafond dédié, le delta
    # de luminance grimperait à _MAX_LUM (20). Avec embedded_raw=True, il doit
    # rester au plafond strict (_MAX_LUM_EMBEDDED_RAW = 10).
    stats = _band(median_l=20.0, median_chroma=20.0)
    target_loose = BandTarget(name="Red", lstar=100.0, embedded_raw=False)
    target_strict = BandTarget(name="Red", lstar=100.0, embedded_raw=True)
    corr_loose = plan_band(stats, target_loose, BandResponse())
    corr_strict = plan_band(stats, target_strict, BandResponse())
    assert corr_loose.d_luminance == 20
    assert corr_strict.d_luminance == 10


def test_plan_band_embedded_raw_caps_hue_delta_tighter():
    stats = _band(median_hue=0.0, median_chroma=20.0)
    target_loose = BandTarget(name="Red", hue=170.0, embedded_raw=False)
    target_strict = BandTarget(name="Red", hue=170.0, embedded_raw=True)
    corr_loose = plan_band(stats, target_loose, BandResponse())
    corr_strict = plan_band(stats, target_strict, BandResponse())
    assert corr_loose.d_hue == 15
    assert corr_strict.d_hue == 8


def test_plan_band_embedded_raw_default_false_unchanged_behavior():
    # Comportement historique préservé : `embedded_raw` par défaut à False, plafond
    # nominal inchangé (pas de régression sur les cibles non-embedded/seed-match).
    stats = _band(median_l=20.0, median_chroma=20.0)
    target = BandTarget(name="Red", lstar=100.0)
    corr = plan_band(stats, target, BandResponse())
    assert corr.d_luminance == 20
