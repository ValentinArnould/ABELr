"""Modèle de réponse calibrée (`core.response`) — inversion ∂rendu/∂curseur.

Ces fonctions traduisent un écart mesuré en delta de curseur : un signe ou une
division instable ici corromprait chaque correction. Pur (pas d'I/O disque testé).
"""

from __future__ import annotations

import pytest

from app.core import response as rsp


# --- Exposition -------------------------------------------------------------- #
def test_exposure_falls_back_to_nominal_when_uncalibrated():
    er = rsp.ExposureResponse()
    assert er.slope_at(50.0) == pytest.approx(rsp.NOMINAL_DL_DEV)
    # ΔEV pour +17 L* au prior 17 L*/EV = +1 EV.
    assert er.solve_dev(50.0, 67.0) == pytest.approx(1.0, abs=1e-6)


def test_exposure_slope_from_samples():
    er = rsp.ExposureResponse(ev=[-1.0, 0.0, 1.0], lstar=[33.0, 50.0, 67.0])
    assert er.slope_at(50.0) == pytest.approx(17.0, abs=1e-6)
    assert er.solve_dev(50.0, 67.0) == pytest.approx(1.0, abs=1e-6)


def test_exposure_slope_never_below_one():
    flat = rsp.ExposureResponse(ev=[0.0, 1.0], lstar=[50.0, 50.0])
    assert flat.slope_at(50.0) >= 1.0
    assert flat.slope_at(10.0) >= 1.0


# --- Balance des blancs ------------------------------------------------------ #
def test_wb_uncalibrated_returns_zero():
    wb = rsp.WBResponse()
    assert not wb.is_calibrated()
    assert wb.solve(2.0, 3.0) == (0.0, 0.0)


def test_wb_identity_jacobian_inverts_sign():
    # J = [[1,0],[0,1]] (a* ~ Temp/100, b* ~ Tint) → annule un biais mesuré.
    wb = rsp.WBResponse(da_dtemp=1.0, db_dtemp=0.0, da_dtint=0.0, db_dtint=1.0)
    assert wb.is_calibrated()
    dtemp, dtint = wb.solve(2.0, 3.0)
    assert dtemp == pytest.approx(-200.0)  # (−a_bias)·100 K
    assert dtint == pytest.approx(-3.0)


def test_wb_singular_jacobian_returns_zero():
    wb = rsp.WBResponse(da_dtemp=1.0, db_dtemp=1.0, da_dtint=1.0, db_dtint=1.0)  # det=0
    assert wb.solve(2.0, 3.0) == (0.0, 0.0)


# --- Sondage HSL : fit de pente (H2) ----------------------------------------- #
def test_fit_linear_response_recovers_known_slope():
    # Delta curseur connu → pente attendue : mesuré = 0.6*delta + décalage constant
    # (décalage absorbé par l'ordonnée libre, seule la pente doit être retrouvée).
    deltas = [-15.0, -8.0, 0.0, 8.0, 15.0]
    measured = [5.0 + 0.6 * d for d in deltas]
    assert rsp.fit_linear_response(deltas, measured) == pytest.approx(0.6, abs=1e-9)


def test_fit_linear_response_negative_slope():
    deltas = [-10.0, -5.0, 5.0, 10.0]
    measured = [-0.35 * d for d in deltas]
    assert rsp.fit_linear_response(deltas, measured) == pytest.approx(-0.35, abs=1e-9)


def test_fit_linear_response_needs_at_least_two_samples():
    assert rsp.fit_linear_response([5.0], [3.0]) == 0.0
    assert rsp.fit_linear_response([], []) == 0.0


def test_fit_linear_response_zero_when_deltas_not_dispersed():
    # Tous les deltas sondés identiques → pente non identifiable.
    assert rsp.fit_linear_response([8.0, 8.0, 8.0], [1.0, 2.0, 3.0]) == 0.0


def test_fit_linear_response_mismatched_lengths_returns_zero():
    assert rsp.fit_linear_response([1.0, 2.0], [1.0]) == 0.0


# --- Modèle complet + clés de cache ----------------------------------------- #
def test_band_fallback_is_default_response():
    m = rsp.ResponseModel(camera="ILCE-7M4", profile="Adobe Color")
    b = m.band("Red")
    assert (b.dchroma_dsat, b.dl_dlum, b.dhue_dhue) == (0.0, 0.0, 0.0)


def test_key_and_cache_file_sanitize_separators():
    assert rsp._key(None, None) == "unknown|unknown"
    # Le séparateur '|' interne est neutralisé dans les composantes.
    assert rsp._key("a|b", "c") == "a_b|c"
    f = rsp._cache_file("ILCE 7M4", "Adobe/Color")
    assert f.suffix == ".json"
    assert "|" not in f.name and "/" not in f.name and " " not in f.name
