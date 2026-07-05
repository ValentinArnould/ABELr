"""Stabilité des clés de fraîcheur du cache (`core.cache`).

Invariant central du mode neutre : `style_hash` doit rester **stable** quand
Temp/Tint/Exposure/HSL bougent (le probe les neutralise) et **changer** quand un
réglage de style (profil, tons, crop, Color Grading) bouge — sinon l'ancre neutre
est servie périmée (ou re-probée inutilement à chaque cycle).
"""

from __future__ import annotations

from app.core import cache


def test_raw_signature_missing_file():
    assert cache.raw_signature("does/not/exist.arw") == "0:0"


def test_raw_signature_encodes_size_mtime_and_version(tmp_path):
    f = tmp_path / "x.arw"
    f.write_bytes(b"hello")
    sig = cache.raw_signature(f)
    parts = sig.split(":")
    assert parts[0] == "5"  # taille en octets
    assert cache.ANALYSIS_VERSION in sig  # salage de version présent


def test_blob_hash_deterministic_and_salted():
    import hashlib

    h1 = cache.blob_hash(b"abc")
    assert h1 == cache.blob_hash(b"abc")
    assert h1 != cache.blob_hash(b"abd")
    # Le hash inclut le sel de version → différent du sha1 brut des octets.
    assert h1 != hashlib.sha1(b"abc").hexdigest()


def test_develop_hash_is_order_independent():
    a = cache.develop_hash({"Exposure2012": 0.5, "Tint": 3})
    b = cache.develop_hash({"Tint": 3, "Exposure2012": 0.5})
    assert a == b
    assert cache.develop_hash({}) == cache.develop_hash(None)


def test_style_hash_stable_under_neutralized_axes():
    base = {"CameraProfile": "Adobe Color", "Contrast2012": 10}
    ref = cache.style_hash(base)
    # Temp/Tint/Exposure + HSL : neutralisés par le probe → ne changent PAS l'ancre.
    for key, val in (
        ("Temperature", 6500),
        ("Tint", 12),
        ("Exposure2012", 1.3),
        ("SaturationAdjustmentRed", 40),
        ("HueAdjustmentBlue", -20),
        ("LuminanceAdjustmentGreen", 15),
    ):
        moved = dict(base)
        moved[key] = val
        assert cache.style_hash(moved) == ref, f"{key} ne doit pas changer style_hash"


def test_style_hash_changes_on_style_axes():
    base = {"CameraProfile": "Adobe Color", "Contrast2012": 10}
    ref = cache.style_hash(base)
    for key, val in (
        ("CameraProfile", "Adobe Standard"),
        ("Contrast2012", 25),
        ("Clarity2012", 30),
        ("CropLeft", 0.1),
        ("ColorGradeShadowHue", 200),
    ):
        moved = dict(base)
        moved[key] = val
        assert cache.style_hash(moved) != ref, f"{key} doit changer style_hash"


def test_style_hash_ignores_non_style_keys():
    base = {"Contrast2012": 10}
    with_noise = {"Contrast2012": 10, "SomeUnknownKey": 999, "Temperature": 5000}
    assert cache.style_hash(base) == cache.style_hash(with_noise)
