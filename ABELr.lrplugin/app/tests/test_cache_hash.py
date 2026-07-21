"""Stability of cache freshness keys (`core.cache`).

Central invariant of neutral mode: `style_hash` must stay **stable** when
Temp/Tint/Exposure/HSL move (the probe neutralizes them) and **change** when a
style setting (profile, tone, crop, Color Grading) moves — otherwise the neutral
anchor is served stale (or needlessly re-probed on every cycle).
"""

from __future__ import annotations

from app.core import cache


def test_raw_signature_missing_file():
    # Missing-file fallback: also salted with ANALYSIS_VERSION (Fable 5 review DB-04).
    sig = cache.raw_signature("does/not/exist.arw")
    assert sig.startswith("0:0")
    assert cache.ANALYSIS_VERSION in sig


def test_raw_signature_encodes_size_mtime_and_version(tmp_path):
    f = tmp_path / "x.arw"
    f.write_bytes(b"hello")
    sig = cache.raw_signature(f)
    parts = sig.split(":")
    assert parts[0] == "5"  # size in bytes
    assert cache.ANALYSIS_VERSION in sig  # version salt present


def test_develop_hash_is_order_independent():
    a = cache.develop_hash({"Exposure2012": 0.5, "Tint": 3})
    b = cache.develop_hash({"Tint": 3, "Exposure2012": 0.5})
    assert a == b
    assert cache.develop_hash({}) == cache.develop_hash(None)


def test_style_hash_stable_under_neutralized_axes():
    base = {"CameraProfile": "Adobe Color", "Contrast2012": 10}
    ref = cache.style_hash(base)
    # Temp/Tint/Exposure + HSL: neutralized by the probe → must NOT change the anchor.
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
        assert cache.style_hash(moved) == ref, f"{key} must not change style_hash"


def test_style_hash_changes_on_style_axes():
    base = {"CameraProfile": "Adobe Color", "Contrast2012": 10}
    ref = cache.style_hash(base)
    for key, val in (
        ("CameraProfile", "Adobe Standard"),
        ("Contrast2012", 25),
        ("Clarity2012", 30),
        ("CropLeft", 0.1),
        # Keys added by the Fable 5 review DB-01 (real SDK names — the old
        # ColorGradeShadowHue etc. did not exist).
        ("SplitToningShadowHue", 200),
        ("ColorGradeMidtoneHue", 120),
        ("Texture", 30),
        ("ParametricShadows", -20),
        ("ToneCurvePV2012", [0, 0, 128, 140, 255, 255]),
    ):
        moved = dict(base)
        moved[key] = val
        assert cache.style_hash(moved) != ref, f"{key} must change style_hash"


def test_style_hash_ignores_non_style_keys():
    base = {"Contrast2012": 10}
    with_noise = {"Contrast2012": 10, "SomeUnknownKey": 999, "Temperature": 5000}
    assert cache.style_hash(base) == cache.style_hash(with_noise)
