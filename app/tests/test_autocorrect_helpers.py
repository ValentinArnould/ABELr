"""Helpers purs de `core.autocorrect` : différence de teinte circulaire, aire de crop,
lecture robuste de réglage. Petits mais utilisés dans chaque plan de correction.
"""

from __future__ import annotations

import pytest

from app.core import autocorrect as ac


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (10.0, 20.0, -10.0),
        (20.0, 10.0, 10.0),
        (350.0, 10.0, -20.0),   # 350 est "avant" 10 sur le cercle
        (10.0, 350.0, 20.0),
        (0.0, 0.0, 0.0),
    ],
)
def test_hue_diff_circular(a, b, expected):
    assert ac._hue_diff(a, b) == pytest.approx(expected, abs=1e-9)


def test_hue_diff_in_range():
    # Implémentation `(a-b+180)%360-180` → intervalle [−180, 180) (l'antipode donne −180).
    for a in range(0, 360, 17):
        for b in range(0, 360, 23):
            d = ac._hue_diff(float(a), float(b))
            assert -180.0 <= d < 180.0


def test_crop_area_defaults_to_full_frame():
    assert ac._crop_area({}) == pytest.approx(1.0)


def test_crop_area_partial_and_inverted():
    assert ac._crop_area(
        {"CropLeft": 0.0, "CropRight": 0.5, "CropTop": 0.0, "CropBottom": 1.0}
    ) == pytest.approx(0.5)
    # Bornes inversées → aire écrêtée à 0, jamais négative.
    assert ac._crop_area(
        {"CropLeft": 0.8, "CropRight": 0.2, "CropTop": 0.0, "CropBottom": 1.0}
    ) == pytest.approx(0.0)


def test_f_reads_float_with_default():
    assert ac._f({"Exposure2012": "0.5"}, "Exposure2012") == pytest.approx(0.5)
    assert ac._f({}, "Missing", default=1.0) == pytest.approx(1.0)
    assert ac._f({"Bad": "not-a-number"}, "Bad", default=-1.0) == pytest.approx(-1.0)
    assert ac._f(None, "Any", default=2.0) == pytest.approx(2.0)


def test_calib_develop_dict_empty_without_calibration():
    from app.core.seed_match import SeedTarget

    t = SeedTarget(
        temperature=None, tint=None, tone=None, bands=None,
        shadow_tint=None, red_hue=None, red_saturation=None,
        green_hue=None, green_saturation=None, blue_hue=None, blue_saturation=None,
        n_matched=1, seed_ids=["s"],
    )
    assert t.has_calibration() is False
    assert ac._calib_develop_dict(t) == {}


def test_calib_develop_dict_writes_present_fields_clamped_and_rounded():
    from app.core.seed_match import SeedTarget

    t = SeedTarget(
        temperature=None, tint=None, tone=None, bands=None,
        shadow_tint=-12.4, red_hue=150.0, red_saturation=None,
        green_hue=0.0, green_saturation=-200.0, blue_hue=None, blue_saturation=None,
        n_matched=2, seed_ids=["a", "b"],
    )
    assert t.has_calibration() is True
    dev = ac._calib_develop_dict(t)
    assert dev == {
        "ShadowTint": -12,
        "RedHue": 100,          # écrêté à +100
        "GreenHue": 0,
        "GreenSaturation": -100,  # écrêté à -100
        "EnableCalibration": True,
    }
    # Champs absents chez la cible (RedSaturation/BlueHue/BlueSaturation) omis.
    assert "RedSaturation" not in dev
    assert "BlueHue" not in dev
