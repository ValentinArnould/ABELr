"""Formatage des corrections develop finales (clés PascalCase SDK Lr).

Helper de construction du dict `develop` envoyé au plugin (job apply_adjustments).
Le *calcul* des corrections WB/expo vit dans `core.wb_model` / `core.seeds`
(modèle physique calibré sur seeds). L'ancienne heuristique « viser 18 % de gris »
a été retirée : prouvée fausse sur les events (la cible n'est pas constante — cf.
mémoire projet, échec à n=1142).
"""

from __future__ import annotations


def build_develop(exposure: float | None = None, **params) -> dict:
    """Construit un dict develop (PascalCase) en omettant les valeurs None."""
    develop: dict[str, float] = {}
    if exposure is not None:
        develop["Exposure"] = exposure
    for key, value in params.items():
        if value is not None:
            develop[key] = value
    return develop
