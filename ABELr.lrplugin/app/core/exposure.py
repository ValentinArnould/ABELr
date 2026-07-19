"""Équilibrage d'exposition — espace rendu, cible résolue par le caller (embedded ou k-NN seeds).

L'exposition perçue vit dans le **rendu** (profil DCP + courbe + curseurs), pas en
scène-linéaire. On mesure la clarté CIE **L*** du rendu courant
(`render_metrics.tone_stats`, zone nette) et on la compare à une **clarté cible**
déjà résolue par le caller (`core.autocorrect`) :

- Mode **embedded** : cible = L* du JPEG boîtier de la photo elle-même (zone nette).
- Mode **seeds** : cible = L* de l'aperçu rendu (déjà retouché) du/des seed(s) les
  plus proches en analyse RAW (`core.seed_match.match_target`).

L'écart est traduit en ΔExposure2012 via la **réponse calibrée** `∂L*/∂EV`
(`core.response`), bornée par un pas max et un garde-fou **headroom** (clipping
RAW, ne pousse pas vers un écrêtage déjà présent). Le nouvel `Exposure2012`
cumule ce delta sur la valeur develop **courante** — fournie par le caller, qui
doit l'avoir mesurée fraîche (le rendu `current_l` doit refléter cette valeur
courante, sinon le delta recalculé n'a pas de sens).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .response import ExposureResponse
from ..server.models import PhotoAdjustment

# Seuils de clipping RAW (fractions) au-delà desquels on bride la correction.
_HI_LIMIT = 0.02
_LO_LIMIT = 0.02
# Pas d'exposition maximal par photo (sécurité contre une mesure aberrante).
_MAX_STEP_EV = 2.0


@dataclass
class ExposureSample:
    """Mesure d'une photo cible + sa clarté désirée (déjà résolue par le caller).

    `current_l`    : médiane L* du **rendu courant** (zone nette), doit refléter
                      `current_exposure` (mesure fraîche — responsabilité du caller).
    `desired_l`     : clarté L* visée (JPEG boîtier ou seeds matchés). `None` = pas
                      de cible exploitable → photo laissée inchangée.
    """

    photo_id: str
    current_l: float
    current_exposure: float
    desired_l: float | None
    clipped_hi: float = 0.0   # fraction hautes lumières écrêtées (RAW) — headroom
    clipped_lo: float = 0.0   # fraction ombres bouchées (RAW) — headroom


def _headroom_factor(clip: float, limit: float) -> float:
    """Facteur [0, 1] : 1 sous le seuil, décroît jusqu'à 0 à 2× le seuil."""
    if clip <= limit:
        return 1.0
    return max(0.0, 1.0 - (clip - limit) / limit)


def plan_from_render(
    samples: list[ExposureSample],
    resp: ExposureResponse | None = None,
    max_step_ev: float = _MAX_STEP_EV,
    hi_limit: float = _HI_LIMIT,
    lo_limit: float = _LO_LIMIT,
) -> list[PhotoAdjustment]:
    """Planifie Exposure2012 pour amener chaque photo à sa clarté `desired_l`.

    ΔEV = `resp.solve_dev(current_l → desired_l)`, borné à ±`max_step_ev`, puis
    atténué par le headroom RAW. Le nouvel Exposure2012 cumule le delta sur
    `current_exposure`. Photos sans `desired_l` : ignorées (rien à appliquer).
    """
    resp = resp or ExposureResponse()
    out: list[PhotoAdjustment] = []
    for s in samples:
        if s.desired_l is None:
            continue
        dev = resp.solve_dev(s.current_l, s.desired_l)
        dev = float(np.clip(dev, -max_step_ev, max_step_ev))
        if dev > 0:
            dev *= _headroom_factor(s.clipped_hi, hi_limit)
        elif dev < 0:
            dev *= _headroom_factor(s.clipped_lo, lo_limit)
        new_ev = round(s.current_exposure + dev, 2)
        out.append(PhotoAdjustment(photo_id=s.photo_id, develop={"Exposure2012": new_ev}))
    return out
