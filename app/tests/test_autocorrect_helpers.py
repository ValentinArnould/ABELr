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
