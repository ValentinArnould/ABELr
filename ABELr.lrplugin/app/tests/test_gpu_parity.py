"""GPU/CPU parity of render metrics — skeleton marked `gpu`.

Skipped if torch/CUDA is absent (see the `cuda_or_skip` fixture). Verifies that the
torch port (`render_metrics_gpu`) reproduces the numpy version (`render_metrics`) on
a small synthetic render — without depending on a real `.ARW`. Extends the pattern
from `tools/validate_gpu_vs_libraw.py` into a deterministic unit test.
"""

from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.gpu
def test_tone_stats_gpu_matches_cpu(cuda_or_skip):
    torch = cuda_or_skip
    from app.core import render_metrics as rm
    from app.core import render_metrics_gpu as rmg

    rng = np.random.default_rng(0)
    rgb = (rng.random((64, 64, 3)) * 255).astype(np.uint8)

    cpu = rm.tone_stats(rgb)
    chw = torch.from_numpy(rgb).permute(2, 0, 1).to("cuda")
    gpu = rmg.analyze_rendered_gpu_dual(chw).glob.tone

    assert gpu is not None
    assert gpu.median_l == pytest.approx(cpu.median_l, abs=1.0)
