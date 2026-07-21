"""Pytest config — test net for the **pure** core functions (no GPU, no RAW).

These tests lock down the numerical invariants that the whole pipeline's
correctness depends on (colorimetry, cache key stability, k-NN aggregation,
calibrated response). They run in a few seconds, with no CUDA or `.ARW` file needed:
- run from the project root: `pytest app/tests -q`
- tests marked `@pytest.mark.gpu` are **skipped** if torch/CUDA is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The project root (parent of the `app` package) must be on sys.path for
# `from app.core import ...` regardless of the launch cwd.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "gpu: requires torch + CUDA (skipped if absent)"
    )


@pytest.fixture(scope="session")
def cuda_or_skip():
    """Skip the test if torch/CUDA is not available (GPU/CPU parity)."""
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    return torch
