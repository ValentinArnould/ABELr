"""Modèle de balance des blancs : Temperature physique + calibration par seeds.

Découverte validée (essais CGC 1004, St-Valentin, Yggdrasil — voir mémoire
projet) : sur un event *typique*, la Temperature choisie par le photographe suit
l'AWB boîtier de façon quasi-linéaire :

    Temperature ≈ SLOPE · (r/g as-shot) + intercept

- **SLOPE** est une propriété **physique du boîtier** (capteur + matrice), quasi
  identique d'un catalogue à l'autre pour un même modèle : mesurée 2436 / 2459 /
  2464 K par unité de r/g sur ILCE-7M4 → ~2450. Réutilisable sur tous les
  catalogues du même boîtier (un seul calibrage capteur).
- **intercept** = le biais de chaleur que le photographe veut pour CET event. Il
  ne généralise PAS entre events (généralisation croisée ≈ baseline) → on le
  calibre sur 5-8 *seeds* (photos corrigées à la main) du catalogue courant.
- **Tint** et **Exposure** sont quasi-constants sur un event typique → médiane des
  seeds suffit (σ Tint ≈ 4, σ Exposure ≈ 0.04 EV sur CGC).

Limite : si l'event impose une teinte artistique en ignorant l'AWB (régime
Yggdrasil), aucun modèle as-shot ne marche → `core.regime` le détecte et bascule
en repli (boucle fermée / manuel).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .render_metrics import NeutralStats
from .response import WBResponse

# Pente physique r/g → Temperature (K par unité de r/g), par modèle de boîtier.
# Mesurée empiriquement ; à étendre quand d'autres boîtiers sont calibrés.
CAMERA_SLOPE_RG: dict[str, float] = {
    "ILCE-7M4": 2450.0,
}
DEFAULT_SLOPE_RG = 2450.0

# Bornes physiques de Temperature (curseur Lr Camera Raw).
TEMP_MIN, TEMP_MAX = 2000.0, 12000.0


def slope_for_camera(camera: str | None) -> float:
    """Pente physique r/g→K du boîtier, ou défaut si modèle inconnu."""
    if camera and camera in CAMERA_SLOPE_RG:
        return CAMERA_SLOPE_RG[camera]
    return DEFAULT_SLOPE_RG


@dataclass
class Seed:
    """Photo de référence corrigée à la main : entrée as-shot + réglage choisi."""

    photo_id: str
    asshot_rg: float          # r/g du WB boîtier (entrée physique)
    asshot_bg: float          # b/g du WB boîtier
    temperature: float        # Temperature choisie par le photographe (K)
    tint: float               # Tint choisi
    exposure: float           # Exposure2012 choisi (EV)


@dataclass
class WBCalibration:
    """Modèle WB calibré sur les seeds d'un catalogue."""

    slope_rg: float           # pente physique utilisée (K / [r/g])
    intercept: float          # biais chaleur de l'event (K)
    tint: float               # Tint à appliquer (médiane seeds)
    exposure: float           # Exposure à appliquer (médiane seeds)
    n_seeds: int
    residual_k: float         # RMS des seeds autour de la droite (confiance)
    temp_spread_k: float      # dispersion des Temperature seeds (contexte)
    median_temp_k: float = 0.0  # médiane brute des Temperature seeds (repli artistique)

    def predict_temperature(self, asshot_rg: float) -> float:
        """Temperature prédite pour une photo depuis son r/g as-shot (bornée)."""
        t = self.slope_rg * asshot_rg + self.intercept
        return float(min(TEMP_MAX, max(TEMP_MIN, t)))


def calibrate(seeds: list[Seed], slope_rg: float = DEFAULT_SLOPE_RG) -> WBCalibration:
    """Calibre le modèle WB depuis les seeds (pente physique fixée).

    L'intercept = médiane(Temperature − slope·r/g) : robuste aux outliers et
    stable dès 3 seeds (la pente étant fixe, seul l'offset reste à estimer).
    Tint et Exposure = médianes. `residual_k` mesure si les seeds tombent bien sur
    une droite de pente `slope_rg` (petit = régime physique fiable).
    """
    if not seeds:
        raise ValueError("Aucun seed pour calibrer le modèle WB.")
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


# Fraction minimale de pixels neutres pour qu'un raffinement WB soit tenté.
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
    """Raffine (Temperature, Tint) prédits par le modèle seed avec le cast résiduel
    mesuré **sur les neutres du rendu** (`render_metrics.neutral_stats`).

    Ne s'active que si (1) assez de neutres fiables ET (2) réponse WB calibrée. Sinon
    on garde la prédiction seed — **jamais de gray-world global** (impasse n=1142).
    Delta borné et Temperature re-clampée aux bornes Lr. Retourne (temp, tint, raison).
    """
    if neutral.n_neutral == 0 or neutral.neutral_frac < min_neutral_frac:
        return temp, tint, "neutres insuffisants → prédiction seed conservée"
    if not wb.is_calibrated():
        return temp, tint, "réponse WB non calibrée → prédiction seed conservée"
    dtemp, dtint = wb.solve(neutral.a_bias, neutral.b_bias)
    dtemp = float(np.clip(dtemp, -max_dtemp_k, max_dtemp_k))
    dtint = float(np.clip(dtint, -max_dtint, max_dtint))
    new_temp = float(min(TEMP_MAX, max(TEMP_MIN, temp + dtemp)))
    # Tint borné aux limites Lr ±150 (revue Fable 5 A-06), symétrique de Temperature.
    new_tint = float(min(150.0, max(-150.0, tint + dtint)))
    return (
        new_temp,
        new_tint,
        f"neutres {neutral.neutral_frac:.3f} (a*={neutral.a_bias:+.1f}, b*={neutral.b_bias:+.1f}) "
        f"→ ΔTemp={dtemp:+.0f}K ΔTint={dtint:+.1f}",
    )
