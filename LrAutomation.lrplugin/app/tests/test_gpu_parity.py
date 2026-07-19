"""Parité GPU/CPU des métriques de rendu — squelette marqué `gpu`.

Skippé si torch/CUDA absent (cf. fixture `cuda_or_skip`). Vérifie que le portage
torch (`render_metrics_gpu`) reproduit la version numpy (`render_metrics`) sur un
petit rendu synthétique — sans dépendre d'un vrai `.ARW`. Étend le pattern de
`tools/validate_gpu_vs_libraw.py` en test unitaire déterministe.
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
