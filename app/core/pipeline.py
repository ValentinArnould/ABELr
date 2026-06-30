"""Composition de l'analyse en espace rendu — point d'entrée unique.

`analyze_rendered` mesure tone (exposition), neutral (cast WB) et bandes (HSL) en
**une seule conversion CIELAB** partagée (3× moins de calcul qu'en appelant les trois
séparément). C'est l'API que le worker GUI appelle sur chaque RGB rendu décodé par
`core.measure`, avant d'appeler les planificateurs `exposure` / `wb_model.refine_temp_tint`
/ `hsl`.

Le reste de l'orchestration (soumettre les jobs `get_thumbnails`/`render_probe`,
construire les cibles à partir des seeds) vit dans le worker, car il dépend de la
queue de jobs et du plugin. Ce module reste pur et testable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import render_metrics
from .render_metrics import BandStats, NeutralStats, ToneStats


@dataclass
class RenderAnalysis:
    """Mesures complètes d'un rendu (exposition + WB + HSL), une passe CIELAB."""

    tone: ToneStats              # clarté L* → exposition
    neutral: NeutralStats        # cast a*/b* sur neutres → raffinement WB
    bands: list[BandStats]       # stats par bande HSL → étalonnage HSL


def analyze_rendered(rgb_u8: np.ndarray) -> RenderAnalysis:
    """Analyse un RGB uint8 sRGB rendu en une seule conversion Lab partagée."""
    lab = render_metrics.srgb_u8_to_lab(rgb_u8)
    return RenderAnalysis(
        tone=render_metrics.tone_stats(rgb_u8, lab),
        neutral=render_metrics.neutral_stats(lab),
        bands=render_metrics.band_stats(rgb_u8, lab),
    )


def band_map(analysis: RenderAnalysis) -> dict[str, BandStats]:
    """Accès par nom de bande (Red, Orange, …)."""
    return {b.name: b for b in analysis.bands}
