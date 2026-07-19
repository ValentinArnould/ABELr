"""White balance model: physical Temperature + seed-based calibration.

Validated finding: on a *typical* event, the Temperature chosen by the photographer
follows the camera's AWB in a near-linear way:

    Temperature ≈ SLOPE · (as-shot r/g) + intercept

- **SLOPE** is a **physical property of the camera body** (sensor + matrix), nearly
  identical from one catalog to another for the same model: measured at 2436 / 2459 /
  2464 K per unit of r/g on the ILCE-7M4 → ~2450. Reusable across all catalogs of
  the same camera body (a single sensor calibration).
- **intercept** = the warmth bias the photographer wants for THIS event. It does
  NOT generalize across events (cross-event generalization ≈ baseline) → it is
  calibrated on 5-8 *seeds* (manually corrected photos) of the current catalog.
- **Tint** and **Exposure** are nearly constant on a typical event → seed median
  is enough (σ Tint ≈ 4, σ Exposure ≈ 0.04 EV on CGC).

Limitation: if the event imposes an artistic tint that ignores the AWB (Yggdrasil
regime), no as-shot model works → `core.regime` detects this and switches to a
fallback (closed loop / manual).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .render_metrics import NeutralStats
from .response import WBResponse

# Physical r/g → Temperature slope (K per unit of r/g), per camera model.
# Measured empirically; extend as other camera bodies are calibrated.
CAMERA_SLOPE_RG: dict[str, float] = {
    "ILCE-7M4": 2450.0,
}
DEFAULT_SLOPE_RG = 2450.0

# Physical Temperature bounds (Lr Camera Raw slider).
TEMP_MIN, TEMP_MAX = 2000.0, 12000.0


def slope_for_camera(camera: str | None) -> float:
    """Physical r/g→K slope of the camera body, or default if the model is unknown."""
    if camera and camera in CAMERA_SLOPE_RG:
        return CAMERA_SLOPE_RG[camera]
    return DEFAULT_SLOPE_RG


@dataclass
class Seed:
    """Manually corrected reference photo: as-shot input + chosen setting."""

    photo_id: str
    asshot_rg: float          # r/g of the camera WB (physical input)
    asshot_bg: float          # b/g of the camera WB
    temperature: float        # Temperature chosen by the photographer (K)
    tint: float               # Chosen Tint
    exposure: float           # Chosen Exposure2012 (EV)


@dataclass
class WBCalibration:
    """WB model calibrated on a catalog's seeds."""

    slope_rg: float           # physical slope used (K / [r/g])
    intercept: float          # event's warmth bias (K)
    tint: float               # Tint to apply (seed median)
    exposure: float           # Exposure to apply (seed median)
    n_seeds: int
    residual_k: float         # RMS of seeds around the line (confidence)
    temp_spread_k: float      # spread of seed Temperatures (context)
    median_temp_k: float = 0.0  # raw median of seed Temperatures (artistic fallback)

    def predict_temperature(self, asshot_rg: float) -> float:
        """Predicted Temperature for a photo from its as-shot r/g (bounded)."""
        t = self.slope_rg * asshot_rg + self.intercept
        return float(min(TEMP_MAX, max(TEMP_MIN, t)))


def calibrate(seeds: list[Seed], slope_rg: float = DEFAULT_SLOPE_RG) -> WBCalibration:
    """Calibrates the WB model from the seeds (physical slope fixed).

    intercept = median(Temperature − slope·r/g): robust to outliers and
    stable from 3 seeds onward (since the slope is fixed, only the offset needs
    estimating). Tint and Exposure = medians. `residual_k` measures whether the
    seeds fall well on a line of slope `slope_rg` (small = reliable physical regime).
    """
    if not seeds:
        raise ValueError("No seed to calibrate the WB model.")
    rg = np.array([s.asshot_rg for s in seeds], np.float64)
    temp = np.array([s.temperature for s in seeds], np.float64)
    tint = np.array([s.tint for s in seeds], np.float64)
    exp = np.array([s.exposure for s in seeds], np.float64)

    offsets = temp - slope_rg * rg
    intercept = float(np.median(offsets))
    pred = slope_rg * rg + intercept
    residual = float(np.sqrt(np.mean((temp - pred) ** 2))) if len(seeds) > 1 else 0.0
    spread = float(np.std(temp)) if len(seeds) > 1 else 0.0

    return WBCalibration(
        slope_rg=slope_rg,
        intercept=intercept,
        tint=float(np.median(tint)),
        exposure=float(np.median(exp)),
        n_seeds=len(seeds),
        residual_k=residual,
        temp_spread_k=spread,
        median_temp_k=float(np.median(temp)),
    )


# Minimum fraction of neutral pixels for a WB refinement to be attempted.
MIN_NEUTRAL_FRAC = 0.005


def refine_temp_tint(
    temp: float,
    tint: float,
    neutral: NeutralStats,
    wb: WBResponse,
    *,
    min_neutral_frac: float = MIN_NEUTRAL_FRAC,
    max_dtemp_k: float = 600.0,
    max_dtint: float = 10.0,
) -> tuple[float, float, str]:
    """Refines (Temperature, Tint) predicted by the seed model with the residual cast
    measured **on the render's neutrals** (`render_metrics.neutral_stats`).

    Only activates if (1) enough reliable neutrals AND (2) calibrated WB response.
    Otherwise keeps the seed prediction — **never a global gray-world** (n=1142
    dead end). Delta is bounded and Temperature re-clamped to Lr bounds. Returns
    (temp, tint, reason).
    """
    if neutral.n_neutral == 0 or neutral.neutral_frac < min_neutral_frac:
        return temp, tint, "insufficient neutrals → seed prediction kept"
    if not wb.is_calibrated():
        return temp, tint, "WB response not calibrated → seed prediction kept"
    dtemp, dtint = wb.solve(neutral.a_bias, neutral.b_bias)
    dtemp = float(np.clip(dtemp, -max_dtemp_k, max_dtemp_k))
    dtint = float(np.clip(dtint, -max_dtint, max_dtint))
    new_temp = float(min(TEMP_MAX, max(TEMP_MIN, temp + dtemp)))
    # Tint bounded to Lr limits ±150 (Fable 5 review A-06), symmetric with Temperature.
    new_tint = float(min(150.0, max(-150.0, tint + dtint)))
    return (
        new_temp,
        new_tint,
        f"neutrals {neutral.neutral_frac:.3f} (a*={neutral.a_bias:+.1f}, b*={neutral.b_bias:+.1f}) "
        f"→ ΔTemp={dtemp:+.0f}K ΔTint={dtint:+.1f}",
    )
