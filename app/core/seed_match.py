"""Matching k-NN sur seeds — remplace `regime.py` côté app live (`wb_model.py`
reste live : `refine_temp_tint` raffine Temp/Tint après le k-NN, cf. autocorrect).

Au lieu d'une régression physique (pente boîtier r/g → Temperature) ou d'un
recalage purement render-space, on cherche pour chaque photo cible les **seeds**
(marqués explicitement par l'utilisateur, `cache.is_seed`) dont l'analyse RAW
(zone nette, `core.sharpness`) est la plus proche, et on utilise **leur** aperçu
rendu (`PreviewJPEG`, déjà retouché par l'utilisateur — la référence de style
voulue) comme cible pour les axes Exposition/WB/HSL.

`exposure.py`/`hsl.py`/`autocorrect.py` consomment `target_from_seeds(...)` pour
obtenir une cible (ToneStats + bandes + Temperature/Tint) à comparer à l'état
**courant**, mesuré frais (hash vérifié) par le caller — c'est ce hash-check
côté caller qui garantit qu'on ne recompound jamais un delta sur une mesure
périmée (cf. CLAUDE.md / plan de refonte).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import cache as cachemod
from .render_metrics import BandStats, ToneStats, band_is_reliable

K_MAX = 3


# Étalonnage caméra (panneau "Calibration caméra") : 7 réglages plats, transplantés
# tels quels depuis les seeds (comme Temperature/Tint) — pas de mesure/inversion
# possible, ce sont des réglages créatifs sans cible objective côté rendu.
# Note : "RedHue"/"GreenHue"/"BlueHue" sont des curseurs linéaires -100..100 (pas
# un angle de teinte) → moyenne pondérée classique, pas de moyenne circulaire.
CALIB_FIELDS = (
    "shadow_tint",
    "red_hue", "red_saturation",
    "green_hue", "green_saturation",
    "blue_hue", "blue_saturation",
)


@dataclass
class SeedVector:
    photo_id: str
    asshot_rg: float | None
    asshot_bg: float | None
    raw_median_l: float | None              # ToneStats.median_l du RAW (zone nette)
    temperature: float | None               # Temperature retouchée par l'utilisateur
    tint: float | None
    preview_tone: ToneStats | None          # PreviewJPEG du seed (cible expo)
    preview_bands: list[BandStats] | None   # PreviewJPEG du seed (cible HSL)
    profile_capture: str | None = None      # profil créatif boîtier (filtre de groupe)
    ev100: float | None = None              # contexte scène (non utilisé dans la distance)
    shadow_tint: float | None = None        # Étalonnage — cf. CALIB_FIELDS
    red_hue: float | None = None
    red_saturation: float | None = None
    green_hue: float | None = None
    green_saturation: float | None = None
    blue_hue: float | None = None
    blue_saturation: float | None = None


@dataclass
class SeedTarget:
    """Cible agrégée depuis les k seeds les plus proches (ou un seed unique si
    correspondance quasi exacte)."""

    temperature: float | None
    tint: float | None
    tone: ToneStats | None
    bands: list[BandStats] | None
    shadow_tint: float | None
    red_hue: float | None
    red_saturation: float | None
    green_hue: float | None
    green_saturation: float | None
    blue_hue: float | None
    blue_saturation: float | None
    n_matched: int
    seed_ids: list[str]

    def has_calibration(self) -> bool:
        return any(getattr(self, f) is not None for f in CALIB_FIELDS)


def _f(dev: dict, key: str, default: float | None = None) -> float | None:
    v = (dev or {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_seed_vector(conn, uuid: str) -> SeedVector | None:
    """Construit le vecteur d'un seed depuis le cache (sans vérif de fraîcheur —
    cf. `cache.get_source_raw_latest`). `None` si l'analyse RAW manque (le seed
    n'est pas encore passé par "Analyser sélection")."""
    sr = cachemod.get_source_raw_latest(conn, uuid)
    if sr is None or sr["asshot_rg"] is None:
        return None
    pic = cachemod.get_picture(conn, uuid)
    dev = pic["current_develop"] if pic else {}
    preview = cachemod.get_preview_jpeg_latest(conn, uuid)
    profile = sr.get("profile_capture") or (pic.get("profile_capture") if pic else None)
    return SeedVector(
        photo_id=uuid,
        asshot_rg=sr["asshot_rg"],
        asshot_bg=sr["asshot_bg"],
        raw_median_l=sr["tone"].median_l if sr["tone"] else None,
        temperature=_f(dev, "Temperature"),
        tint=_f(dev, "Tint"),
        preview_tone=preview.tone if preview else None,
        preview_bands=preview.bands if preview else None,
        profile_capture=profile,
        ev100=sr.get("ev100"),
        shadow_tint=_f(dev, "ShadowTint"),
        red_hue=_f(dev, "RedHue"),
        red_saturation=_f(dev, "RedSaturation"),
        green_hue=_f(dev, "GreenHue"),
        green_saturation=_f(dev, "GreenSaturation"),
        blue_hue=_f(dev, "BlueHue"),
        blue_saturation=_f(dev, "BlueSaturation"),
    )


def build_seed_pool(conn) -> list[SeedVector]:
    """Tous les seeds exploitables du catalogue (analyse RAW présente)."""
    out = []
    for uuid in cachemod.list_seed_uuids(conn):
        v = build_seed_vector(conn, uuid)
        if v is not None:
            out.append(v)
    return out


def _distance(target: SeedVector, seed: SeedVector, scale: dict[str, float]) -> float:
    """Distance euclidienne normalisée (z-score) sur (asshot_rg, asshot_bg, raw_median_l).
    Une feature manquante d'un côté ou de l'autre est ignorée (pas de pénalité)."""
    acc = 0.0
    for key in ("asshot_rg", "asshot_bg", "raw_median_l"):
        tv, sv = getattr(target, key), getattr(seed, key)
        if tv is None or sv is None:
            continue
        s = scale.get(key) or 1.0
        acc += ((tv - sv) / s) ** 2
    return math.sqrt(acc)


def _feature_scale(seeds: list[SeedVector]) -> dict[str, float]:
    """Écart-type (par feature) du pool de seeds — normalise la distance euclidienne
    pour que des features d'échelles très différentes (rg/bg ~0.1-3, L* ~0-100)
    pèsent comparablement."""
    scale: dict[str, float] = {}
    for key in ("asshot_rg", "asshot_bg", "raw_median_l"):
        vals = [getattr(s, key) for s in seeds if getattr(s, key) is not None]
        if len(vals) < 2:
            scale[key] = 1.0
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        scale[key] = math.sqrt(var) or 1.0
    return scale


def k_nearest(
    target: SeedVector, seeds: list[SeedVector], k: int | None = None
) -> list[tuple[SeedVector, float]]:
    """Les k seeds les plus proches de `target` (hors target lui-même).

    `k` par défaut = `min(K_MAX, max(1, n_seeds // 2))`. Si le plus proche est à
    une distance quasi nulle (correspondance exacte), ne renvoie que celui-là.

    Intention du `pool // 2` (revue Fable 5 A-07) : sur un petit pool (3-5 seeds),
    moyenner la moitié du pool diluerait la cible avec des seeds éloignés — k=3
    n'est donc atteint qu'à partir de 6 seeds, et c'est voulu.
    """
    pool = [s for s in seeds if s.photo_id != target.photo_id]
    if not pool:
        return []
    if k is None:
        k = min(K_MAX, max(1, len(pool) // 2))
    scale = _feature_scale(pool)
    ranked = sorted(((s, _distance(target, s, scale)) for s in pool), key=lambda t: t[1])
    if ranked[0][1] < 1e-6:
        return [ranked[0]]
    return ranked[:k]


def _circular_mean_deg(values: list[float]) -> float:
    if not values:
        return 0.0
    ang = [math.radians(v) for v in values]
    s = sum(math.sin(a) for a in ang)
    c = sum(math.cos(a) for a in ang)
    return math.degrees(math.atan2(s, c)) % 360.0


def _weighted(values: list[tuple[float, float]]) -> float | None:
    """Moyenne pondérée `[(valeur, poids), ...]`. None si rien d'exploitable."""
    total_w = sum(w for _, w in values)
    if total_w <= 0:
        return None
    return sum(v * w for v, w in values) / total_w


def _weighted_tone(matches: list[tuple[SeedVector, float]]) -> ToneStats | None:
    items = [(m.preview_tone, w) for m, _d in matches if m.preview_tone is not None
             for w in [1.0 / (_d + 1e-6)]]
    if not items:
        return None
    fields = ("median_l", "mean_l", "p05_l", "p95_l", "clipped_hi", "clipped_lo", "tonal_frac")
    kwargs = {f: _weighted([(getattr(t, f), w) for t, w in items]) for f in fields}
    return ToneStats(**kwargs)


def _weighted_bands(matches: list[tuple[SeedVector, float]]) -> list[BandStats] | None:
    by_name: dict[str, list[tuple[BandStats, float]]] = {}
    for m, d in matches:
        if not m.preview_bands:
            continue
        w = 1.0 / (d + 1e-6)
        for b in m.preview_bands:
            if not band_is_reliable(b):
                continue
            by_name.setdefault(b.name, []).append((b, w))
    if not by_name:
        return None
    out: list[BandStats] = []
    for name, items in by_name.items():
        out.append(
            BandStats(
                name=name,
                frac=_weighted([(b.frac, w) for b, w in items]) or 0.0,
                median_hue=_circular_mean_deg([b.median_hue for b, _ in items]),
                median_chroma=_weighted([(b.median_chroma, w) for b, w in items]) or 0.0,
                median_sat=_weighted([(b.median_sat, w) for b, w in items]) or 0.0,
                sat_clip_frac=_weighted([(b.sat_clip_frac, w) for b, w in items]) or 0.0,
                median_l=_weighted([(b.median_l, w) for b, w in items]) or 0.0,
            )
        )
    return out


def _weighted_field(matches: list[tuple[SeedVector, float]], field: str) -> float | None:
    items = [
        (getattr(m, field), 1.0 / (d + 1e-6)) for m, d in matches if getattr(m, field) is not None
    ]
    return _weighted(items)


def target_from_seeds(matches: list[tuple[SeedVector, float]]) -> SeedTarget | None:
    """Agrège les seeds matchés (pondération 1/distance) en une cible unique."""
    if not matches:
        return None
    temps = [(m.temperature, 1.0 / (d + 1e-6)) for m, d in matches if m.temperature is not None]
    tints = [(m.tint, 1.0 / (d + 1e-6)) for m, d in matches if m.tint is not None]
    return SeedTarget(
        temperature=_weighted(temps),
        tint=_weighted(tints),
        tone=_weighted_tone(matches),
        bands=_weighted_bands(matches),
        shadow_tint=_weighted_field(matches, "shadow_tint"),
        red_hue=_weighted_field(matches, "red_hue"),
        red_saturation=_weighted_field(matches, "red_saturation"),
        green_hue=_weighted_field(matches, "green_hue"),
        green_saturation=_weighted_field(matches, "green_saturation"),
        blue_hue=_weighted_field(matches, "blue_hue"),
        blue_saturation=_weighted_field(matches, "blue_saturation"),
        n_matched=len(matches),
        seed_ids=[m.photo_id for m, _ in matches],
    )


def _filter_by_profile(target: SeedVector, seeds: list[SeedVector]) -> list[SeedVector]:
    """Restreint le pool aux seeds du **même profil créatif** que la cible, si possible.

    Le profil créatif boîtier (Standard/IN/SH/VV2…) corrèle avec le style de retouche
    et le biais d'exposition (cf. sous-expo volontaire sous IN/SH). Matcher dans le même
    groupe évite de transférer une cible d'un autre régime. **Filtre doux** : si la
    cible n'a pas de profil, ou qu'aucun seed ne le partage, on garde le pool complet
    (jamais de pool vide → pas de régression sur les petits jeux de seeds)."""
    if target.profile_capture is None:
        return seeds
    same = [s for s in seeds if s.profile_capture == target.profile_capture]
    return same if same else seeds


def match_target(
    target: SeedVector,
    seeds: list[SeedVector],
    k: int | None = None,
    *,
    profile_aware: bool = True,
) -> SeedTarget | None:
    """Raccourci : (filtre profil doux) + k plus proches + agrégation en une cible."""
    pool = _filter_by_profile(target, seeds) if profile_aware else seeds
    return target_from_seeds(k_nearest(target, pool, k))
