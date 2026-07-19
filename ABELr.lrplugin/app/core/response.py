"""Réponse calibrée ∂rendu/∂curseur — modèle inversé, caché par (caméra, profil).

La chaîne de rendu Lr est non linéaire et dépend du profil DCP. On **ne la modélise
pas** : on **mesure** comment le rendu bouge quand on bouge un curseur (sondage
`render_probe` : appliquer un delta → re-render → mesurer en espace L*a*b*), puis on
**inverse** pour traduire un écart mesuré en delta de curseur.

Pourquoi par (caméra, profil) : la réponse `∂L*/∂EV`, `∂(a*,b*)/∂(Temp,Tint)` et le
Jacobien HSL dépendent du profil. Calibrée **une fois** par profil sur quelques sondes
(comme la pente WB 2450 l'a été), **cachée sur disque** → la correction par photo reste
un seul `apply`. La boucle fermée par photo (re-mesure) reste possible quand la haute
précision l'exige.

Ce module est pur (modèle + fit + inversion + cache). L'orchestration du sondage
(soumettre les jobs `render_probe`, lire les miniatures) vit dans un worker/outil.

⚠️ Les valeurs **nominales** ci-dessous sont des *priors physiques transparents* (dérivés,
non inventés), utilisés tant qu'aucune calibration n'existe. Elles doivent être
remplacées par la mesure (sondage) — voir scripts de validation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Cache disque des modèles de réponse, clé "caméra|profil".
_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "response_cache"


# --------------------------------------------------------------------------- #
# Prior nominal d'exposition — dérivation transparente (à raffiner par sondage)
# --------------------------------------------------------------------------- #
# Exposure2012 ≈ gain scène-linéaire en stops avant courbe. Sur un ton moyen 18 %
# (lin 0.184 → L* 50), +1 EV double le linéaire (0.368) → L* = 116·(0.368^(1/3)) − 16
# ≈ 67. Pente locale ≈ 17 L*/EV près des tons moyens. La pente DIMINUE vers les hautes
# lumières (roll-off) et AUGMENTE dans les ombres ; ce scalaire n'est qu'un prior médian.
NOMINAL_DL_DEV = 17.0  # L* par stop, près des tons moyens — prior, pas une vérité profil.


@dataclass
class ExposureResponse:
    """Réponse clarté L* ↔ Exposure2012, mesurée sur une photo de référence.

    `ev`/`lstar` : échantillons sondés (deltas EV appliqués → médiane L* rendue mesurée),
    triés par EV croissant. Si vide → on retombe sur `NOMINAL_DL_DEV`.
    """

    ev: list[float] = field(default_factory=list)
    lstar: list[float] = field(default_factory=list)

    def _sorted(self) -> tuple[list[float], list[float]]:
        if not self.ev:
            return [], []
        pairs = sorted(zip(self.ev, self.lstar))
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def slope_at(self, l_value: float) -> float:
        """Pente locale ∂L*/∂EV à la clarté `l_value` (différence finie sur la courbe sondée).

        Retombe sur `NOMINAL_DL_DEV` si moins de 2 échantillons. Toujours ≥ une petite
        valeur positive (la clarté croît avec l'EV) pour éviter une division instable.
        """
        ev, ls = self._sorted()
        if len(ev) < 2:
            return NOMINAL_DL_DEV
        # Segment de courbe dont l'intervalle L* contient l_value (sinon segment le plus proche).
        best_slope = NOMINAL_DL_DEV
        best_dist = float("inf")
        for i in range(len(ev) - 1):
            dl = ls[i + 1] - ls[i]
            de = ev[i + 1] - ev[i]
            if de == 0:
                continue
            slope = dl / de
            lo, hi = min(ls[i], ls[i + 1]), max(ls[i], ls[i + 1])
            if lo <= l_value <= hi:
                return max(slope, 1.0)
            dist = min(abs(l_value - lo), abs(l_value - hi))
            if dist < best_dist:
                best_dist, best_slope = dist, slope
        return max(best_slope, 1.0)

    def solve_dev(self, current_l: float, target_l: float) -> float:
        """Delta Exposure2012 pour amener `current_l` vers `target_l` (linéarisation locale).

        Pente prise au milieu de l'intervalle [current_l, target_l] : robuste à la
        non-linéarité de la courbe (un pas ; la boucle fermée affine si besoin).
        """
        slope = self.slope_at(0.5 * (current_l + target_l))
        return (target_l - current_l) / slope


@dataclass
class WBResponse:
    """Jacobien 2×2 local ∂(a*, b*)/∂(Temp, Tint), mesuré par sondage.

    da_dtemp, db_dtemp : variation a*/b* par +100 K. da_dtint, db_dtint : par +1 Tint.
    Tout à 0 → non calibré (le raffinement WB neutre ne s'applique pas, on garde le seed).
    Pas de prior nominal : la magnitude dépend trop du profil pour être devinée honnêtement.
    """

    da_dtemp: float = 0.0
    db_dtemp: float = 0.0
    da_dtint: float = 0.0
    db_dtint: float = 0.0

    def is_calibrated(self) -> bool:
        return any(abs(v) > 1e-9 for v in (self.da_dtemp, self.db_dtemp, self.da_dtint, self.db_dtint))

    def solve(self, a_bias: float, b_bias: float) -> tuple[float, float]:
        """(ΔTemp, ΔTint) pour annuler un biais (a_bias, b_bias) mesuré sur les neutres.

        Résout le système 2×2 J·[dTemp(/100), dTint] = -[a_bias, b_bias]. Retourne (0, 0)
        si non calibré ou singulier. ΔTemp en Kelvin, ΔTint en unités Lr.
        """
        if not self.is_calibrated():
            return 0.0, 0.0
        det = self.da_dtemp * self.db_dtint - self.da_dtint * self.db_dtemp
        if abs(det) < 1e-12:
            return 0.0, 0.0
        # On veut Δ(a,b) = -biais.
        ta, tb = -a_bias, -b_bias
        dtemp100 = (ta * self.db_dtint - self.da_dtint * tb) / det
        dtint = (self.da_dtemp * tb - ta * self.db_dtemp) / det
        return dtemp100 * 100.0, dtint


def fit_linear_response(deltas: Sequence[float], measured: Sequence[float]) -> float:
    """Pente ∂mesuré/∂delta_curseur — régression linéaire (moindres carrés, ordonnée
    libre) sur des échantillons de sondage (delta de curseur connu, mesure rendue).

    Ordonnée libre (pas de passage forcé par l'origine) : absorbe un décalage de
    mesure constant entre échantillons (bruit de rendu), seule la PENTE nous
    intéresse (`BandResponse.dchroma_dsat` etc. — cf. `core.hsl`).
    Retourne 0.0 si <2 échantillons ou deltas non dispersés (pente non identifiable,
    ex. tous les deltas sondés identiques) — le caller retombe sur le prior nominal.
    """
    n = len(deltas)
    if n != len(measured) or n < 2:
        return 0.0
    mean_x = sum(deltas) / n
    mean_y = sum(measured) / n
    var_x = sum((x - mean_x) ** 2 for x in deltas)
    if var_x < 1e-9:
        return 0.0
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(deltas, measured))
    return cov_xy / var_x


@dataclass
class BandResponse:
    """Réponse locale d'une bande HSL (≈ diagonale : Sat→chroma, Lum→L*, Hue→teinte).

    dchroma_dsat : ΔC* (CIELAB) par +1 de SaturationAdjustment<bande>.
    dl_dlum      : ΔL* par +1 de LuminanceAdjustment<bande>.
    dhue_dhue    : Δteinte (deg) par +1 de HueAdjustment<bande>.
    0 → non calibré pour cet axe (on n'émet pas le curseur correspondant).
    """

    dchroma_dsat: float = 0.0
    dl_dlum: float = 0.0
    dhue_dhue: float = 0.0


@dataclass
class ResponseModel:
    """Modèle de réponse complet d'un (caméra, profil)."""

    camera: str
    profile: str
    exposure: ExposureResponse = field(default_factory=ExposureResponse)
    wb: WBResponse = field(default_factory=WBResponse)
    bands: dict[str, BandResponse] = field(default_factory=dict)

    def band(self, name: str) -> BandResponse:
        return self.bands.get(name, BandResponse())


# --------------------------------------------------------------------------- #
# Cache disque (JSON) — clé "caméra|profil"
# --------------------------------------------------------------------------- #
def _key(camera: str | None, profile: str | None) -> str:
    cam = (camera or "unknown").replace("|", "_")
    prof = (profile or "unknown").replace("|", "_")
    return f"{cam}|{prof}"


def _cache_file(camera: str | None, profile: str | None) -> Path:
    safe = _key(camera, profile).replace("|", "__").replace("/", "_").replace(" ", "_")
    return _CACHE_DIR / f"{safe}.json"


def load(camera: str | None, profile: str | None) -> ResponseModel:
    """Charge le modèle caché pour (caméra, profil), ou un modèle vide (priors nominaux).

    Un JSON de cache corrompu/tronqué retombe sur le modèle vide (revue Fable 5
    A-05) : une donnée jetable ne doit jamais faire échouer toute l'analyse.
    """
    f = _cache_file(camera, profile)
    if not f.is_file():
        return ResponseModel(camera=camera or "unknown", profile=profile or "unknown")
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return ResponseModel(
            camera=data.get("camera", camera or "unknown"),
            profile=data.get("profile", profile or "unknown"),
            exposure=ExposureResponse(**data.get("exposure", {})),
            wb=WBResponse(**data.get("wb", {})),
            bands={k: BandResponse(**v) for k, v in data.get("bands", {}).items()},
        )
    except Exception:
        logging.getLogger("abelr.response").exception(
            "modèle de réponse illisible (%s) — repli sur les priors", f
        )
        return ResponseModel(camera=camera or "unknown", profile=profile or "unknown")


def save(model: ResponseModel) -> Path:
    """Persiste un modèle de réponse sur disque (JSON)."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = _cache_file(model.camera, model.profile)
    payload = {
        "camera": model.camera,
        "profile": model.profile,
        "exposure": asdict(model.exposure),
        "wb": asdict(model.wb),
        "bands": {k: asdict(v) for k, v in model.bands.items()},
    }
    f.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return f
