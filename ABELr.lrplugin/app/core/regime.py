"""Détection du régime WB d'un event — décide si le modèle physique est fiable.

Deux régimes observés (mémoire projet) :
- **Physique** (event typique, ex. CGC) : la Temperature suit l'AWB boîtier ; les
  seeds tombent sur la droite pente·(r/g)+intercept avec un faible résidu. Le
  modèle `wb_model` s'applique → corrections automatiques fiables.
- **Artistique** (ex. Yggdrasil, look uniforme imposé) : l'as-shot n'a aucun
  pouvoir, le résidu des seeds ≈ dispersion brute. Aucun modèle as-shot ne marche
  → repli (boucle fermée / manuel).

Discriminateur : **résidu / étalement des Temperature seeds**. Ce n'est PAS le
résidu absolu qui sépare les régimes (CGC 357K et Yggdrasil 425K sont proches),
mais la part de variance que la pente explique :
- CGC : résidu 357K / spread 1171K ≈ 0.30 → la pente explique l'essentiel → physique.
- Yggdrasil : 425K / 548K ≈ 0.78 → la pente n'explique rien (≈ baseline) → artistique.
Sur peu de seeds le ratio est bruité → label + chiffres, pas décision binaire dure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .wb_model import WBCalibration

# Ratio résidu/étalement en-dessous duquel la pente explique l'essentiel (physique).
RATIO_OK = 0.50
# Ratio au-dessus duquel la pente n'explique presque rien (artistique).
RATIO_BAD = 0.70
# Étalement de Temperature seed (K) en-dessous duquel les seeds ne testent pas la
# pente (éclairage trop homogène) : le ratio n'est pas fiable, intercept seul vaut.
MIN_SPREAD_K = 150.0
# En-dessous de ce nombre de seeds, régime jugé incertain (résidu trop bruité).
MIN_SEEDS_FOR_REGIME = 4


class Regime(str, Enum):
    PHYSICS = "physics"        # modèle as-shot fiable → auto
    UNCERTAIN = "uncertain"    # peu de seeds / résidu moyen → appliquer + vérifier
    ARTISTIC = "artistic"      # as-shot sans pouvoir → repli manuel/boucle fermée


@dataclass
class RegimeReport:
    regime: Regime
    residual_k: float
    n_seeds: int
    message: str

    @property
    def apply_exposure(self) -> bool:
        """N'appliquer l'expo modélisée que hors régime artistique."""
        return self.regime is not Regime.ARTISTIC


def detect(cal: WBCalibration) -> RegimeReport:
    """Classe le régime depuis la calibration WB (ratio résidu/étalement seeds)."""
    n, res, spread = cal.n_seeds, cal.residual_k, cal.temp_spread_k

    if n < MIN_SEEDS_FOR_REGIME:
        return RegimeReport(
            Regime.UNCERTAIN, res, n,
            f"Peu de seeds ({n}) : intercept calibré mais régime incertain. "
            f"Ajoutez des seeds couvrant les éclairages, vérifiez le résultat.",
        )
    if spread < MIN_SPREAD_K:
        return RegimeReport(
            Regime.UNCERTAIN, res, n,
            f"Seeds trop homogènes (étalement {spread:.0f}K) : la pente n'est pas "
            f"testée. Intercept appliqué, ajoutez des seeds d'éclairages variés.",
        )
    ratio = res / spread
    if ratio <= RATIO_OK:
        return RegimeReport(
            Regime.PHYSICS, res, n,
            f"Régime physique (résidu/étalement {ratio:.2f} ≤ {RATIO_OK}) : la pente "
            f"explique la WB, corrections as-shot fiables.",
        )
    if ratio >= RATIO_BAD:
        return RegimeReport(
            Regime.ARTISTIC, res, n,
            f"Régime artistique (résidu/étalement {ratio:.2f} ≥ {RATIO_BAD}) : "
            f"l'as-shot ne prédit pas la WB choisie. WB seule appliquée avec "
            f"prudence ; expo et exceptions à traiter à la main.",
        )
    return RegimeReport(
        Regime.UNCERTAIN, res, n,
        f"Régime incertain (résidu/étalement {ratio:.2f}) : corrections "
        f"appliquées, vérifier les outliers.",
    )
