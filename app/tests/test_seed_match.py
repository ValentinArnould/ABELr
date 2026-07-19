"""Matching k-NN sur seeds (`core.seed_match`) — distance normalisée, sélection des
plus proches, agrégation pondérée 1/distance. Pur, sans DB ni RAW (on construit des
`SeedVector` synthétiques).
"""

from __future__ import annotations

import pytest

from app.core import seed_match as sm
from app.core.render_metrics import BandStats, ToneStats


def _tone(median_l: float) -> ToneStats:
    return ToneStats(median_l, median_l, median_l - 5, median_l + 5, 0.0, 0.0, 1.0)


def _seed(pid, rg, bg, l, temp=5500.0, tint=0.0, tone_l=50.0, profile=None, **calib):
    return sm.SeedVector(
        photo_id=pid, asshot_rg=rg, asshot_bg=bg, raw_median_l=l,
        temperature=temp, tint=tint, preview_tone=_tone(tone_l),
        preview_bands=None, profile_capture=profile, **calib,
    )


def test_distance_identical_is_zero():
    a = _seed("a", 0.5, 0.6, 40.0)
    b = _seed("b", 0.5, 0.6, 40.0)
    scale = {"asshot_rg": 1.0, "asshot_bg": 1.0, "raw_median_l": 1.0}
    assert sm._distance(a, b, scale) == pytest.approx(0.0)


def test_distance_ignores_missing_feature():
    a = _seed("a", 0.5, None, 40.0)
    b = _seed("b", 0.5, 0.6, 40.0)  # bg présent d'un seul côté → ignoré
    scale = {"asshot_rg": 1.0, "asshot_bg": 1.0, "raw_median_l": 1.0}
    assert sm._distance(a, b, scale) == pytest.approx(0.0)


def test_k_nearest_excludes_self():
    target = _seed("t", 0.5, 0.5, 50.0)
    pool = [target, _seed("a", 0.6, 0.5, 50.0), _seed("b", 0.9, 0.9, 90.0)]
    matches = sm.k_nearest(target, pool)
    assert all(m.photo_id != "t" for m, _ in matches)


def test_k_nearest_exact_match_returns_single():
    target = _seed("t", 0.5, 0.5, 50.0)
    twin = _seed("twin", 0.5, 0.5, 50.0)   # identique → distance ~0
    far = _seed("far", 5.0, 5.0, 5.0)
    matches = sm.k_nearest(target, [twin, far])
    assert len(matches) == 1
    assert matches[0][0].photo_id == "twin"


def test_k_nearest_orders_by_distance():
    target = _seed("t", 0.5, 0.5, 50.0)
    near = _seed("near", 0.55, 0.5, 50.0)
    mid = _seed("mid", 0.7, 0.5, 50.0)
    far = _seed("far", 0.9, 0.5, 90.0)
    matches = sm.k_nearest(target, [far, mid, near], k=3)
    ids = [m.photo_id for m, _ in matches]
    assert ids[0] == "near"  # le plus proche en tête


def test_weighted_mean_and_empty():
    assert sm._weighted([]) is None
    assert sm._weighted([(10.0, 1.0), (20.0, 1.0)]) == pytest.approx(15.0)
    assert sm._weighted([(10.0, 3.0), (20.0, 1.0)]) == pytest.approx(12.5)


def _circ_close(deg: float, target: float, tol: float = 1e-4) -> bool:
    d = abs((deg - target + 180.0) % 360.0 - 180.0)
    return d < tol


def test_circular_mean_deg():
    # Résultat dans [0,360) : 10 et 350 → moyenne circulaire ≡ 0 (peut sortir 360.0).
    assert _circ_close(sm._circular_mean_deg([10.0, 350.0]), 0.0)
    assert sm._circular_mean_deg([0.0, 90.0]) == pytest.approx(45.0, abs=1e-6)
    assert sm._circular_mean_deg([]) == pytest.approx(0.0)


def test_target_from_seeds_none_on_empty():
    assert sm.target_from_seeds([]) is None


def test_target_from_seeds_weights_nearer_seed():
    near = (_seed("near", 0.5, 0.5, 50.0, temp=6000.0), 0.001)  # poids ~1000
    far = (_seed("far", 0.9, 0.9, 90.0, temp=4000.0), 1.0)      # poids ~1
    tgt = sm.target_from_seeds([near, far])
    assert tgt is not None
    assert tgt.n_matched == 2
    assert tgt.temperature > 5900.0  # dominé par le seed proche (6000)


def test_filter_by_profile_soft():
    target = _seed("t", 0.5, 0.5, 50.0, profile="VV2")
    same = _seed("a", 0.5, 0.5, 50.0, profile="VV2")
    other = _seed("b", 0.5, 0.5, 50.0, profile="STD")
    # Même profil disponible → pool restreint.
    assert sm._filter_by_profile(target, [same, other]) == [same]
    # Aucun même-profil → repli sur le pool complet (jamais vide).
    only_other = [other]
    assert sm._filter_by_profile(target, only_other) == only_other
    # Cible sans profil → pool complet.
    no_prof = _seed("t2", 0.5, 0.5, 50.0, profile=None)
    assert sm._filter_by_profile(no_prof, [same, other]) == [same, other]


def test_target_from_seeds_no_calibration_when_seeds_lack_it():
    a = (_seed("a", 0.5, 0.5, 50.0), 1.0)
    b = (_seed("b", 0.5, 0.5, 50.0), 1.0)
    tgt = sm.target_from_seeds([a, b])
    assert tgt is not None
    assert tgt.has_calibration() is False


def test_target_from_seeds_aggregates_calibration_weighted():
    near = (_seed("near", 0.5, 0.5, 50.0, shadow_tint=-10.0, red_hue=20.0), 0.001)  # poids ~1000
    far = (_seed("far", 0.9, 0.9, 90.0, shadow_tint=10.0, red_hue=-20.0), 1.0)      # poids ~1
    tgt = sm.target_from_seeds([near, far])
    assert tgt is not None
    assert tgt.has_calibration() is True
    assert tgt.shadow_tint < -9.0  # dominé par le seed proche
    assert tgt.red_hue > 19.0
    # Champs non seedés par personne restent None (pas de 0 imposé).
    assert tgt.blue_hue is None


def test_target_from_seeds_calibration_partial_across_seeds():
    # Un seul des deux seeds porte GreenSaturation → seul lui contribue.
    a = (_seed("a", 0.5, 0.5, 50.0, green_saturation=30.0), 1.0)
    b = (_seed("b", 0.5, 0.5, 50.0), 1.0)
    tgt = sm.target_from_seeds([a, b])
    assert tgt is not None
    assert tgt.green_saturation == pytest.approx(30.0)


def test_weighted_bands_averages_reliable_only():
    def band(name, frac, hue, chroma):
        return BandStats(name, frac, hue, chroma, 0.3, 0.0, 50.0)

    # frac 0.5 fiable, frac 0.0 ignoré (band_is_reliable min 0.01).
    s1 = _seed("s1", 0.5, 0.5, 50.0)
    s1.preview_bands = [band("Red", 0.5, 10.0, 20.0)]
    s2 = _seed("s2", 0.5, 0.5, 50.0)
    s2.preview_bands = [band("Red", 0.0, 999.0, 999.0)]  # non fiable → exclu
    tgt = sm.target_from_seeds([(s1, 0.001), (s2, 0.002)])
    assert tgt is not None and tgt.bands is not None
    red = next(b for b in tgt.bands if b.name == "Red")
    assert red.median_chroma == pytest.approx(20.0)  # seul s1 compte
