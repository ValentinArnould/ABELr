"""Colorimetric invariants (`core.color`) — ProPhoto luminance + sRGB curve.

If these conversions drift, the whole exposure/WB measurement is silently thrown off.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core import color


def test_prophoto_y_weights_sum_to_one():
    # The Y row of ProPhoto(D50)→XYZ must sum to ~1 (white → Y=1, D50 normalized).
    assert color.PROPHOTO_TO_Y.sum() == pytest.approx(1.0, abs=1e-4)


def test_luminance_white_and_black():
    white = np.ones((2, 2, 3), np.float32)
    black = np.zeros((2, 2, 3), np.float32)
    assert color.luminance(white) == pytest.approx(np.ones((2, 2)), abs=1e-4)
    assert color.luminance(black) == pytest.approx(np.zeros((2, 2)), abs=1e-6)


def test_luminance_shape_reduces_channel():
    img = np.random.rand(4, 5, 3).astype(np.float32)
    assert color.luminance(img).shape == (4, 5)


def test_luminance_is_linear_additive():
    a = np.random.rand(3, 3, 3).astype(np.float32)
    b = np.random.rand(3, 3, 3).astype(np.float32)
    lhs = color.luminance(a + b)
    rhs = color.luminance(a) + color.luminance(b)
    assert lhs == pytest.approx(rhs, abs=1e-4)


def test_linear_to_srgb_endpoints_and_monotonic():
    assert float(color.linear_to_srgb(np.array(0.0))) == pytest.approx(0.0, abs=1e-6)
    assert float(color.linear_to_srgb(np.array(1.0))) == pytest.approx(1.0, abs=1e-6)
    xs = np.linspace(0, 1, 50)
    ys = color.linear_to_srgb(xs)
    assert np.all(np.diff(ys) >= -1e-9)  # monotonically increasing


def test_linear_to_srgb_clips_out_of_range():
    # Inputs outside [0,1] clamped before transfer.
    assert float(color.linear_to_srgb(np.array(-0.5))) == pytest.approx(0.0, abs=1e-6)
    assert float(color.linear_to_srgb(np.array(2.0))) == pytest.approx(1.0, abs=1e-6)


def test_prophoto_to_srgb_u8_white_black():
    white = np.ones((1, 1, 3), np.float32)
    black = np.zeros((1, 1, 3), np.float32)
    out_w = color.prophoto_linear_to_srgb_u8(white)
    out_b = color.prophoto_linear_to_srgb_u8(black)
    assert out_w.dtype == np.uint8 and out_b.dtype == np.uint8
    # ProPhoto white → near-white sRGB (254: Bradford D50→D65 adaptation + rounding).
    assert np.all(out_w >= 253)
    assert np.all(out_b == 0)
