"""Detects an event's WB regime — decides whether the physical model is trustworthy.

Two observed regimes (project memory):
- **Physical** (typical event, e.g. CGC): Temperature follows the camera AWB;
  the seeds fall on the slope·(r/g)+intercept line with a small residual. The
  `wb_model` applies → reliable automatic corrections.
- **Artistic** (e.g. Yggdrasil, an imposed uniform look): as-shot has no
  predictive power, the seed residual ≈ raw dispersion. No as-shot model
  works → fallback (closed loop / manual).

Discriminator: **residual / spread of the seed Temperatures**. It's NOT the
absolute residual that separates the regimes (CGC 357K and Yggdrasil 425K are
close), but the share of variance the slope explains:
- CGC: residual 357K / spread 1171K ≈ 0.30 → the slope explains most of it → physical.
- Yggdrasil: 425K / 548K ≈ 0.78 → the slope explains nothing (≈ baseline) → artistic.
On few seeds the ratio is noisy → label + numbers, not a hard binary decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .wb_model import WBCalibration

# Residual/spread ratio below which the slope explains most of it (physical).
RATIO_OK = 0.50
# Ratio above which the slope explains almost nothing (artistic).
RATIO_BAD = 0.70
# Seed Temperature spread (K) below which the seeds don't exercise the slope
# (lighting too uniform): the ratio isn't reliable, only the intercept counts.
MIN_SPREAD_K = 150.0
# Below this number of seeds, the regime is deemed uncertain (residual too noisy).
MIN_SEEDS_FOR_REGIME = 4


class Regime(str, Enum):
    PHYSICS = "physics"        # reliable as-shot model → auto
    UNCERTAIN = "uncertain"    # few seeds / mid residual → apply + verify
    ARTISTIC = "artistic"      # as-shot has no power → manual/closed-loop fallback


@dataclass
class RegimeReport:
    regime: Regime
    residual_k: float
    n_seeds: int
    message: str

    @property
    def apply_exposure(self) -> bool:
        """Only apply the modeled exposure outside the artistic regime."""
        return self.regime is not Regime.ARTISTIC


def detect(cal: WBCalibration) -> RegimeReport:
    """Classifies the regime from the WB calibration (seed residual/spread ratio)."""
    n, res, spread = cal.n_seeds, cal.residual_k, cal.temp_spread_k

    if n < MIN_SEEDS_FOR_REGIME:
        return RegimeReport(
            Regime.UNCERTAIN, res, n,
            f"Few seeds ({n}): intercept calibrated but regime uncertain. "
            f"Add seeds covering the lighting conditions, check the result.",
        )
    if spread < MIN_SPREAD_K:
        return RegimeReport(
            Regime.UNCERTAIN, res, n,
            f"Seeds too uniform (spread {spread:.0f}K): the slope isn't "
            f"exercised. Intercept applied, add seeds from varied lighting.",
        )
    ratio = res / spread
    if ratio <= RATIO_OK:
        return RegimeReport(
            Regime.PHYSICS, res, n,
            f"Physical regime (residual/spread {ratio:.2f} ≤ {RATIO_OK}): the slope "
            f"explains the WB, as-shot corrections are reliable.",
        )
    if ratio >= RATIO_BAD:
        return RegimeReport(
            Regime.ARTISTIC, res, n,
            f"Artistic regime (residual/spread {ratio:.2f} ≥ {RATIO_BAD}): "
            f"as-shot doesn't predict the chosen WB. WB alone applied with "
            f"caution; exposure and exceptions need manual handling.",
        )
    return RegimeReport(
        Regime.UNCERTAIN, res, n,
        f"Uncertain regime (residual/spread {ratio:.2f}): corrections "
        f"applied, check for outliers.",
    )
