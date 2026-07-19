"""Étalonnage HSL par bande — réduire la sursaturation, ajuster la luminance,
recentrer la teinte (objectif utilisateur).

Mesure sur le **rendu** (`render_metrics.band_stats`, 8 bandes Lr) ; le RAW peut
servir de **garde** (ne réduire la saturation d'une bande que si le RAW confirme
qu'elle est vraiment chargée à la capture, pas un artefact du profil). Inversion
mesure→curseur via la **réponse calibrée** par bande (`core.response.BandResponse`) ;
sans calibration, on applique un nudge conservateur borné (heuristique transparente).

Curseurs émis (noms SDK, valeurs absolues -100…+100) : `SaturationAdjustment<Bande>`,
`LuminanceAdjustment<Bande>`, `HueAdjustment<Bande>`. Les deltas calculés sont **ajoutés
à la valeur courante** du curseur (le rendu mesuré reflète déjà les curseurs actuels).

⚠️ **Expérimental** : à valider par vérité terrain (script `tools/validate_hsl.py`)
avant de faire confiance. Garde-fous serrés par défaut (bandes peuplées, deltas plafonnés,
zone morte) pour ne jamais sur-corriger.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import render_metrics
from .render_metrics import BandStats
from .response import BandResponse, ResponseModel

# --------------------------------------------------------------------------- #
# Garde-fous et gains nominaux (heuristiques, à remplacer par le sondage)
# --------------------------------------------------------------------------- #
# Population minimale d'une bande pour la corriger.
_MIN_FRAC = render_metrics._BAND_MIN_FRAC
# Zones mortes : en deçà, on ne touche pas (anti micro-correction).
_DEADBAND_CHROMA = 4.0   # ΔC* CIELAB
_DEADBAND_L = 3.0        # ΔL*
_DEADBAND_HUE = 6.0      # degrés
# Plafonds de delta de curseur (unités Lr).
_MAX_SAT = 25
_MAX_LUM = 20
_MAX_HUE = 15
# Fraction de pixels quasi-saturés (S≥0.97) déclenchant une réduction même sans
# référence de chroma (sursaturation « dure »).
_SAT_CLIP_TRIGGER = 0.05

# Gains nominaux quand la réponse n'est PAS calibrée (heuristiques bornées) :
# +1 unité de SaturationAdjustment ≈ +0.6 C* ; +1 Luminance ≈ +0.4 L* ;
# +1 Hue ≈ +0.35° (ordres de grandeur Lr typiques — à confirmer par sondage).
_NOM_DCHROMA_DSAT = 0.6
_NOM_DL_DLUM = 0.4
_NOM_DHUE_DHUE = 0.35


@dataclass
class BandTarget:
    """Référence d'une bande (médiane des seeds / image de référence). Champs optionnels.

    raw_oversat : si False, le RAW NE confirme PAS la charge de cette bande → on
    s'interdit d'en réduire la saturation (évite de corriger un effet de profil).
    None = pas d'info RAW (on ne bloque pas).
    """

    name: str
    chroma: float | None = None
    lstar: float | None = None
    hue: float | None = None
    raw_oversat: bool | None = None


def raw_confirms_oversat(raw_band: BandStats | None, min_frac: float = _MIN_FRAC) -> bool | None:
    """Le RAW (zone nette) confirme-t-il qu'une bande est réellement chargée à la
    capture ? Peuple `BandTarget.raw_oversat` (garde anti-sur-correction : ne pas
    réduire une saturation que le rendu montre mais que le RAW dément).

    None : pas de mesure RAW pour cette bande, ou population RAW insuffisante
    (`min_frac`) → aucune info, on ne bloque pas (comportement historique).
    True/False sinon, sur le même seuil de sursaturation dure (`_SAT_CLIP_TRIGGER`,
    pixels quasi-saturés S≥0.97) que celui utilisé sur le rendu — évidence directe
    du capteur, pas un seuil de chroma inventé.
    """
    if raw_band is None or raw_band.frac < min_frac:
        return None
    return raw_band.sat_clip_frac >= _SAT_CLIP_TRIGGER


@dataclass
class HslCorrection:
    """Delta de curseurs HSL décidé pour une bande (diagnostic + application)."""

    name: str
    d_saturation: int = 0
    d_luminance: int = 0
    d_hue: int = 0
    reason: str = ""


def _hue_diff(a: float, b: float) -> float:
    """Différence circulaire signée a−b dans (−180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


def _clamp(v: float, lo: int, hi: int) -> int:
    return int(round(max(lo, min(hi, v))))


def plan_band(
    stats: BandStats,
    target: BandTarget | None,
    resp: BandResponse,
    *,
    min_frac: float = _MIN_FRAC,
) -> HslCorrection | None:
    """Décide les deltas HSL d'une bande, ou None si rien à faire / bande non fiable."""
    if not render_metrics.band_is_reliable(stats, min_frac):
        return None

    reasons: list[str] = []
    d_sat = d_lum = d_hue = 0

    # --- Saturation : RÉDUCTION SEULE de la sursaturation --------------------
    # Objectif utilisateur : « surtout réduire la saturation excessive ». On ne
    # rehausse JAMAIS la saturation (sinon on copierait la sursaturation d'un JPEG
    # boîtier punchy) → on agit uniquement quand le rendu est PLUS saturé que la
    # référence (excès positif), et le delta est borné à ≤ 0.
    target_chroma = target.chroma if target else None
    excess = 0.0
    if target_chroma is not None:
        excess = stats.median_chroma - target_chroma  # >0 = trop saturé vs référence
    # Sursaturation dure (pixels écrêtés en S) → réduire même sans référence.
    if stats.sat_clip_frac >= _SAT_CLIP_TRIGGER:
        excess = max(excess, _DEADBAND_CHROMA + 1.0)
        reasons.append(f"sat_clip={stats.sat_clip_frac:.2f}")
    # Le RAW peut interdire une réduction (bande non chargée à la capture).
    raw_blocks = target is not None and target.raw_oversat is False
    if excess >= _DEADBAND_CHROMA and not raw_blocks:
        gain = resp.dchroma_dsat if abs(resp.dchroma_dsat) > 1e-9 else _NOM_DCHROMA_DSAT
        d_sat = _clamp(-excess / gain, -_MAX_SAT, 0)  # réduction seule (≤ 0)
        if d_sat:
            reasons.append(f"ΔC*={excess:+.1f}→sat{d_sat:+d}")

    # --- Luminance : rapprocher de la clarté de référence --------------------
    if target and target.lstar is not None:
        dl = target.lstar - stats.median_l
        if abs(dl) >= _DEADBAND_L:
            gain = resp.dl_dlum if abs(resp.dl_dlum) > 1e-9 else _NOM_DL_DLUM
            d_lum = _clamp(dl / gain, -_MAX_LUM, _MAX_LUM)
            if d_lum:
                reasons.append(f"ΔL*={dl:+.1f}→lum{d_lum:+d}")

    # --- Teinte : recentrer la dérive ----------------------------------------
    if target and target.hue is not None:
        dh = _hue_diff(target.hue, stats.median_hue)  # ce qu'il faut ajouter à la teinte
        if abs(dh) >= _DEADBAND_HUE:
            gain = resp.dhue_dhue if abs(resp.dhue_dhue) > 1e-9 else _NOM_DHUE_DHUE
            d_hue = _clamp(dh / gain, -_MAX_HUE, _MAX_HUE)
            if d_hue:
                reasons.append(f"Δhue={dh:+.1f}°→hue{d_hue:+d}")

    if not (d_sat or d_lum or d_hue):
        return None
    return HslCorrection(stats.name, d_sat, d_lum, d_hue, ", ".join(reasons))


def plan_hsl(
    band_stats: list[BandStats],
    targets: dict[str, BandTarget] | None,
    model: ResponseModel | None = None,
    *,
    min_frac: float = _MIN_FRAC,
) -> tuple[dict[str, int], list[HslCorrection]]:
    """Planifie les corrections HSL de toutes les bandes.

    Retourne (develop_delta, corrections) où `develop_delta` est un dict de clés SDK
    (`SaturationAdjustment<Bande>`, etc.) → **delta** à ajouter à la valeur courante.
    Le worker fait la somme avec les valeurs courantes avant d'envoyer le job.
    """
    targets = targets or {}
    develop: dict[str, int] = {}
    corrections: list[HslCorrection] = []
    for stats in band_stats:
        resp = model.band(stats.name) if model else BandResponse()
        corr = plan_band(stats, targets.get(stats.name), resp, min_frac=min_frac)
        if corr is None:
            continue
        corrections.append(corr)
        if corr.d_saturation:
            develop[f"SaturationAdjustment{stats.name}"] = corr.d_saturation
        if corr.d_luminance:
            develop[f"LuminanceAdjustment{stats.name}"] = corr.d_luminance
        if corr.d_hue:
            develop[f"HueAdjustment{stats.name}"] = corr.d_hue
    return develop, corrections
