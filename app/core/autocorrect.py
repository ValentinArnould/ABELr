"""Orchestration de la correction automatique par photo (exposition + WB + HSL).

Pur et testable (comme `exposure`/`hsl`/`seed_match`) : reçoit les mesures déjà
collectées par le worker GUI (+ le pool de seeds déjà construit depuis le cache)
et renvoie un `PhotoAdjustment` par photo + un diagnostic. Le worker Qt
(`gui.autocorrect_worker`) se charge des I/O (jobs, décodage, cache, parallélisme).

Modes de référence (décision utilisateur) :
- **seeds** : un pool de seeds exploitables existe (marqués explicitement, cf.
  `cache.is_seed`) → pour chaque photo cible, on cherche les seeds dont l'analyse
  RAW (zone nette) est la plus proche (`core.seed_match`), et on utilise leur
  aperçu rendu déjà retouché comme référence de style. Mesure de l'état courant =
  rendu frais (`m.analysis`). Les seeds eux-mêmes ne sont JAMAIS réécrits.
- **embedded** (forcé OU aucun seed exploitable) : **ancré sur le rendu neutre**.
  Cible T = JPEG boîtier (immuable, mesure **brute**) ; ancre N = NeutralPreview
  (rendu Lr du même RAW : style courant, WB As Shot, Expo 0, HSL 0 — cf.
  `gui.neutral_preview_worker`). La correction vise directement T (L* tone, cast
  a*/b*, bandes HSL) : le delta T−N, converti en réglages Lr, rapproche le rendu du
  RAW du look du JPEG boîtier — **sans soustraction de biais de profil** (décision
  utilisateur : on transplante le look boîtier tel quel, biais revu plus tard).
  L'ancre N reste indispensable (Lr applique des deltas relatifs au rendu du RAW ;
  on ne peut pas écrire le L* absolu du JPEG). Les valeurs émises sont **absolues**
  (ancre à zéro) → idempotentes, aucune dépendance au rendu courant. Sous les zones
  mortes, **aucune clé n'est écrite** (préserve les réglages/presets).

Mesures embedded : **global** par défaut (T et N = deux rendus de la même scène,
pas de désalignement de masque) ; bascule **zone nette** si crop fort (l'aperçu Lr
est croppé, pas le JPEG boîtier — le masque net s'ancre sur le sujet commun).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import hsl as _hsl
from . import exposure as _exp
from . import seed_match
from . import wb_model as _wb
from .hsl import BandTarget
from .pipeline import RenderAnalysis
from .render_metrics import BandStats, ToneStats, band_is_reliable
from .response import ResponseModel
from .seed_match import SeedTarget, SeedVector
from ..server.models import PhotoAdjustment

DEFAULT_AXES = frozenset({"expo", "wb", "hsl"})

# --------------------------------------------------------------------------- #
# Seuils du mode embedded (ancré neutre, déviation par photo)
# --------------------------------------------------------------------------- #
# Zone morte exposition : sous ce |ΔEV| on n'écrit PAS Exposure2012 (photo
# conforme au profil — ne pas écraser un réglage manuel avec ~0).
_EXPO_DEADBAND_EV = 0.10
# Zone morte WB : distance de cast a*b* (ΔE approx) sous laquelle on ne touche pas.
_WB_CAST_DEADBAND = 3.0
# Fraction minimale de pixels quasi-neutres pour qu'un cast soit fiable.
_MIN_NEUTRAL_FRAC = 0.02
# Confiance pleine du ProfileBias partagé (biais nul, cf. _plan_embedded : la
# décision « biais ignoré » a supprimé le pool de calibration — revue Fable 5 DB-06).
_BIAS_FULL_N = 8
# Aire de crop (fraction du cadre) sous laquelle on mesure en zone nette plutôt
# qu'en global (cadres trop différents entre JPEG boîtier et rendu croppé).
_CROP_AREA_MIN = 0.8
# Divergence ΔL* global ↔ zone nette au-delà de laquelle on note « sujet/fond
# divergents » (contre-jour, sujet éclairé autrement que le fond).
_DIVERGENCE_L = 4.0


@dataclass
class PhotoMeasure:
    """Mesures d'une photo, collectées par le worker.

    Mode seeds : `analysis` (rendu courant frais, zone nette) requis.
    Mode embedded : `embedded_*` (T = JPEG boîtier) et `neutral_*` (N = rendu
    neutre) requis, chacun en global + zone nette ; `analysis` inutile.
    """

    photo_id: str
    path: str
    current_develop: dict
    exif_camera: str | None
    analysis: RenderAnalysis | None = None   # rendu courant (zone nette) — mode seeds
    is_seed: bool = False                    # marquage explicite (cache.is_seed)
    raw_tone: ToneStats | None = None        # RAW source, zone nette — clé du matching k-NN
    embedded_sharp: RenderAnalysis | None = None   # T : JPEG boîtier (zone nette)
    embedded_global: RenderAnalysis | None = None  # T : JPEG boîtier (global)
    neutral_sharp: RenderAnalysis | None = None    # N : rendu neutre (zone nette)
    neutral_global: RenderAnalysis | None = None   # N : rendu neutre (global)
    neutral_asshot_temp: float | None = None       # Temperature numérique de l'As Shot
    neutral_asshot_tint: float | None = None
    hash_style: str | None = None            # clé de groupe du biais (avec profile_capture)
    asshot_rg: float | None = None
    asshot_bg: float | None = None
    profile_capture: str | None = None       # profil créatif boîtier (filtre k-NN + biais)
    ev100: float | None = None               # contexte scène (diagnostic)


@dataclass
class ProfileBias:
    """Biais systématique T−N d'un couple (profil créatif boîtier, style Lr).

    Médianes robustes des deltas par photo sur le pool de calibration :
    `l` (ΔL* médian), `cast_a`/`cast_b` (Δcast a*/b* sur neutres),
    `bands[name] = (dchroma, dl, dhue)`. `n` = taille du pool (photos avec tone).
    """

    n: int
    l: float = 0.0
    cast_a: float = 0.0
    cast_b: float = 0.0
    bands: dict[str, tuple[float, float, float]] = field(default_factory=dict)


@dataclass
class PlanDiagnostics:
    mode: str                       # "seeds" | "embedded"
    n_seeds: int
    n_targets: int
    notes: list[str] = field(default_factory=list)


def _f(dev: dict, key: str, default: float = 0.0) -> float:
    v = (dev or {}).get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _hue_diff(a: float, b: float) -> float:
    """Différence circulaire signée a−b dans [−180, 180) (l'antipode donne −180)."""
    return (a - b + 180.0) % 360.0 - 180.0


# --------------------------------------------------------------------------- #
# Mode embedded — sélection de variante, biais de profil, cibles
# --------------------------------------------------------------------------- #
def _crop_area(dev: dict) -> float:
    """Aire du crop Lr (fraction du cadre). 1.0 si pas de clés crop."""
    left = _f(dev, "CropLeft", 0.0)
    right = _f(dev, "CropRight", 1.0)
    top = _f(dev, "CropTop", 0.0)
    bottom = _f(dev, "CropBottom", 1.0)
    return max(0.0, right - left) * max(0.0, bottom - top)


def _variant_for(m: PhotoMeasure) -> str:
    """Variante de mesure embedded : global par défaut, zone nette si crop fort."""
    return "sharp" if _crop_area(m.current_develop) < _CROP_AREA_MIN else "global"


def _pair_for(
    m: PhotoMeasure, variant: str
) -> tuple[RenderAnalysis | None, RenderAnalysis | None, str]:
    """(T, N, variante effective) de la photo — repli sur l'autre variante si la
    demandée est incomplète (la variante retournée pilote aussi le choix du biais)."""
    other_variant = "global" if variant == "sharp" else "sharp"
    if variant == "sharp":
        first = (m.embedded_sharp, m.neutral_sharp)
        other = (m.embedded_global, m.neutral_global)
    else:
        first = (m.embedded_global, m.neutral_global)
        other = (m.embedded_sharp, m.neutral_sharp)
    if first[0] is not None and first[1] is not None:
        return first[0], first[1], variant
    if other[0] is not None and other[1] is not None:
        return other[0], other[1], other_variant
    return None, None, variant


def _embedded_band_targets(
    t: RenderAnalysis, bias: ProfileBias, *, ignore_bias: bool = False
) -> dict[str, BandTarget]:
    """Cibles HSL embedded = bandes du JPEG boîtier.

    `ignore_bias=True` (chemin live) : cible = bande **brute** du JPEG boîtier
    (`target = T.band`), toute bande fiable comptée — on transplante le look boîtier
    tel quel. `ignore_bias=False` (historique) : `target = T.band − B.band`, on ne
    vise que la déviation par rapport à la norme du couple profil × style, et les
    bandes sans norme de biais sont sautées.
    """
    out: dict[str, BandTarget] = {}
    for b in t.bands or []:
        if not band_is_reliable(b):
            continue
        if ignore_bias:
            out[b.name] = BandTarget(
                name=b.name,
                chroma=b.median_chroma,
                lstar=b.median_l,
                hue=b.median_hue,
            )
            continue
        b_bias = bias.bands.get(b.name)
        if b_bias is None:
            continue  # pas de norme pour cette bande → pas de cible (prudence)
        dchroma, dl, dhue = b_bias
        out[b.name] = BandTarget(
            name=b.name,
            chroma=b.median_chroma - dchroma,
            lstar=b.median_l - dl,
            hue=b.median_hue - dhue,
        )
    return out


def _band_targets_from_seed_match(t: SeedTarget | None) -> dict[str, BandTarget]:
    """Cibles HSL = bandes agrégées des seeds les plus proches (déjà pondérées)."""
    out: dict[str, BandTarget] = {}
    if t is None or not t.bands:
        return out
    for b in t.bands:
        out[b.name] = BandTarget(
            name=b.name, chroma=b.median_chroma, lstar=b.median_l, hue=b.median_hue
        )
    return out


def plan(
    measures: list[PhotoMeasure],
    *,
    axes: frozenset[str] = DEFAULT_AXES,
    forced_embedded: bool = False,
    model: ResponseModel | None = None,
    camera: str | None = None,
    seed_pool: list[SeedVector] | None = None,
) -> tuple[list[PhotoAdjustment], PlanDiagnostics]:
    """Planifie la correction par photo. Voir le docstring du module pour les modes."""
    seed_pool = seed_pool or []
    targets = [m for m in measures if not m.is_seed]
    mode_embedded = forced_embedded or not seed_pool

    dev_by_id: dict[str, dict] = {m.photo_id: {} for m in targets}
    diag = PlanDiagnostics(
        mode="embedded" if mode_embedded else "seeds",
        n_seeds=len(seed_pool),
        n_targets=len(targets),
    )
    if mode_embedded:
        reason = "case cochée" if forced_embedded else "aucun seed exploitable"
        diag.notes.insert(0, f"mode JPEG embarqué ancré neutre ({reason})")
        return _plan_embedded(targets, axes, model, dev_by_id, diag)

    diag.notes.insert(0, f"mode seeds — pool de {len(seed_pool)} seed(s)")
    return _plan_seeds(targets, axes, model, seed_pool, dev_by_id, diag)


# --------------------------------------------------------------------------- #
# Mode embedded — ancré neutre, déviation par photo, valeurs absolues
# --------------------------------------------------------------------------- #
def _plan_embedded(
    targets: list[PhotoMeasure],
    axes: frozenset[str],
    model: ResponseModel | None,
    dev_by_id: dict[str, dict],
    diag: PlanDiagnostics,
) -> tuple[list[PhotoAdjustment], PlanDiagnostics]:
    # Cible = mesure JPEG boîtier brute → biais de profil nul (partagé, lecture
    # seule) : `bias.l`/`.cast_*`/`.bands` valent 0 dans les boucles ci-dessous.
    bias = ProfileBias(n=_BIAS_FULL_N)

    # Résolution par photo : variante + paire (T, N).
    resolved: list[tuple[PhotoMeasure, str, RenderAnalysis, RenderAnalysis, ProfileBias]] = []
    n_no_anchor = 0
    n_divergent = 0
    for m in targets:
        t, n, variant = _pair_for(m, _variant_for(m))
        if t is None or n is None or t.tone is None or n.tone is None:
            n_no_anchor += 1
            continue
        # Divergence global ↔ zone nette (diagnostic sujet/fond).
        if (
            m.embedded_global is not None and m.neutral_global is not None
            and m.embedded_sharp is not None and m.neutral_sharp is not None
            and m.embedded_global.tone and m.neutral_global.tone
            and m.embedded_sharp.tone and m.neutral_sharp.tone
        ):
            d_glob = m.embedded_global.tone.median_l - m.neutral_global.tone.median_l
            d_sharp = m.embedded_sharp.tone.median_l - m.neutral_sharp.tone.median_l
            if abs(d_glob - d_sharp) > _DIVERGENCE_L:
                n_divergent += 1
        if variant == "sharp":
            diag.notes.append(
                f"{m.photo_id[:8]}: crop fort (aire {_crop_area(m.current_develop):.2f}) → mesure zone nette"
            )
        resolved.append((m, variant, t, n, bias))

    diag.notes.append("biais profil ignoré — cible = mesures JPEG boîtier brutes")
    if n_no_anchor:
        diag.notes.append(
            f"{n_no_anchor} photo(s) sans ancre neutre ou cible boîtier → ignorée(s)"
        )
    if n_divergent:
        diag.notes.append(
            f"{n_divergent} photo(s) : ΔL* global ↔ zone nette divergents (> {_DIVERGENCE_L:g} L*) "
            f"— sujet/fond éclairés différemment, correction à vérifier"
        )

    # ---- Exposition : valeur absolue ancrée à Exposure2012 = 0 -------------
    if "expo" in axes:
        samples = []
        for m, _variant, t, n, bias in resolved:
            desired_l = t.tone.median_l - bias.l
            samples.append(
                _exp.ExposureSample(
                    m.photo_id,
                    current_l=n.tone.median_l,
                    current_exposure=0.0,        # ancre : le delta EST la valeur absolue
                    desired_l=desired_l,
                    clipped_hi=n.tone.clipped_hi,
                    clipped_lo=n.tone.clipped_lo,
                )
            )
        n_written = 0
        n_conform = 0
        for adj in _exp.plan_from_render(samples, model.exposure if model else None):
            new_ev = adj.develop.get("Exposure2012", 0.0)
            if abs(new_ev) < _EXPO_DEADBAND_EV:
                n_conform += 1        # conforme au profil → aucune écriture
                continue
            dev_by_id[adj.photo_id]["Exposure2012"] = new_ev
            n_written += 1
        diag.notes.append(
            f"expo: {n_written} déviante(s) corrigée(s), {n_conform} conforme(s) au profil "
            f"(aucune écriture), sur {len(resolved)} résolue(s)"
        )

    # ---- Balance des blancs : déviation de cast, base As Shot numérique ----
    if "wb" in axes:
        wbresp = model.wb if model else None
        n_written = 0
        n_conform = 0
        n_uncalibrated = 0
        for m, _variant, t, n, bias in resolved:
            tn, nn = t.neutral, n.neutral
            if (
                tn is None or nn is None
                or tn.neutral_frac < _MIN_NEUTRAL_FRAC
                or nn.neutral_frac < _MIN_NEUTRAL_FRAC
            ):
                n_conform += 1  # cast non mesurable → ne rien toucher
                continue
            # Excès de cast du rendu Lr (As Shot) vs boîtier, corrigé du biais :
            # e = (N − T) + B ; sous la zone morte → photo conforme, rien à écrire.
            e_a = (nn.a_bias - tn.a_bias) + bias.cast_a
            e_b = (nn.b_bias - tn.b_bias) + bias.cast_b
            if (e_a * e_a + e_b * e_b) ** 0.5 < _WB_CAST_DEADBAND:
                n_conform += 1
                continue
            if (
                wbresp is None or not wbresp.is_calibrated()
                or m.neutral_asshot_temp is None
            ):
                n_uncalibrated += 1
                continue
            dtemp, dtint = wbresp.solve(e_a, e_b)
            temp = max(2000.0, min(12000.0, m.neutral_asshot_temp + max(-600.0, min(600.0, dtemp))))
            # Tint borné aux limites Lr ±150 (revue Fable 5 A-06), comme Temperature.
            tint = max(-150.0, min(150.0, (m.neutral_asshot_tint or 0.0) + max(-10.0, min(10.0, dtint))))
            dev_by_id[m.photo_id].update(
                WhiteBalance="Custom", Temperature=round(temp), Tint=round(tint)
            )
            n_written += 1
        note = f"wb: {n_written} corrigée(s), {n_conform} conforme(s) (aucune écriture)"
        if n_uncalibrated:
            note += (
                f", {n_uncalibrated} déviante(s) NON corrigée(s) — réponse WB non "
                f"calibrée (sondage render_probe à faire)"
            )
        diag.notes.append(note)

    # ---- HSL : cibles = T − biais, valeurs absolues (ancre HSL = 0) --------
    if "hsl" in axes:
        n_written = 0
        for m, _variant, t, n, bias in resolved:
            tgs = _embedded_band_targets(t, bias, ignore_bias=True)
            deltas, _corrs = _hsl.plan_hsl(n.bands or [], tgs, model)
            wrote = False
            for key, d in deltas.items():
                # Ancre HSL = 0 ⇒ la valeur absolue est le delta lui-même. Les
                # zones mortes de plan_band ont déjà omis les bandes conformes.
                if d == 0:
                    continue
                dev_by_id[m.photo_id][key] = int(max(-100, min(100, round(d))))
                wrote = True
            if wrote:
                n_written += 1
        diag.notes.append(
            f"hsl: {n_written}/{len(resolved)} photo(s) déviante(s) ajustée(s) "
            f"(clés conformes omises)"
        )

    adjustments = [
        PhotoAdjustment(photo_id=pid, develop=dev) for pid, dev in dev_by_id.items() if dev
    ]
    return adjustments, diag


# --------------------------------------------------------------------------- #
# Mode seeds — chemin k-NN historique (inchangé)
# --------------------------------------------------------------------------- #
def _plan_seeds(
    targets: list[PhotoMeasure],
    axes: frozenset[str],
    model: ResponseModel | None,
    seed_pool: list[SeedVector],
    dev_by_id: dict[str, dict],
    diag: PlanDiagnostics,
) -> tuple[list[PhotoAdjustment], PlanDiagnostics]:
    # Le mode seeds mesure l'état courant sur le rendu frais : exiger `analysis`.
    usable = [m for m in targets if m.analysis is not None and m.analysis.tone is not None]
    if len(usable) < len(targets):
        diag.notes.append(f"{len(targets) - len(usable)} photo(s) sans rendu courant → ignorée(s)")

    # Cible k-NN par photo, calculée une fois et réutilisée par les 3 axes.
    match_cache: dict[str, SeedTarget | None] = {}

    def _match(m: PhotoMeasure) -> SeedTarget | None:
        if m.photo_id not in match_cache:
            query = SeedVector(
                photo_id=m.photo_id,
                asshot_rg=m.asshot_rg,
                asshot_bg=m.asshot_bg,
                raw_median_l=m.raw_tone.median_l if m.raw_tone else None,
                temperature=None, tint=None, preview_tone=None, preview_bands=None,
                profile_capture=m.profile_capture, ev100=m.ev100,
            )
            match_cache[m.photo_id] = seed_match.match_target(query, seed_pool)
        return match_cache[m.photo_id]

    # ---- Exposition --------------------------------------------------------
    if "expo" in axes:
        samples = []
        n_resolved = 0
        for m in usable:
            t = _match(m)
            desired = t.tone.median_l if (t and t.tone) else None
            if desired is not None:
                n_resolved += 1
            samples.append(
                _exp.ExposureSample(
                    m.photo_id, m.analysis.tone.median_l, _f(m.current_develop, "Exposure2012"),
                    desired_l=desired,
                    clipped_hi=m.analysis.tone.clipped_hi, clipped_lo=m.analysis.tone.clipped_lo,
                )
            )
        for adj in _exp.plan_from_render(samples, model.exposure if model else None):
            dev_by_id[adj.photo_id].update(adj.develop)
        diag.notes.append(f"expo: {n_resolved}/{len(usable)} cible(s) résolue(s)")

    # ---- Balance des blancs -----------------------------------------------
    if "wb" in axes:
        n_wb = 0
        wbresp = model.wb if model else None
        for m in usable:
            t = _match(m)
            if t is None or t.temperature is None:
                continue
            temp = t.temperature
            tint = t.tint if t.tint is not None else 0.0
            # Garde `neutral is not None` (revue Fable 5 A-04) : une RenderAnalysis
            # servie du cache peut ne pas porter de NeutralStats — sans la garde,
            # une seule photo faisait échouer tout le run (AttributeError).
            if wbresp is not None and m.analysis.neutral is not None:
                temp, tint, _ = _wb.refine_temp_tint(temp, tint, m.analysis.neutral, wbresp)
            tint = max(-150.0, min(150.0, tint))  # borne Lr ±150 (A-06)
            dev_by_id[m.photo_id].update(
                WhiteBalance="Custom", Temperature=round(temp), Tint=round(tint)
            )
            n_wb += 1
        diag.notes.append(f"wb: {n_wb}/{len(usable)} photo(s) matchée(s) (k-NN seeds)")

    # ---- HSL ---------------------------------------------------------------
    if "hsl" in axes:
        n_hsl = 0
        for m in usable:
            tgs = _band_targets_from_seed_match(_match(m))
            deltas, _corrs = _hsl.plan_hsl(m.analysis.bands, tgs, model)
            for key, d in deltas.items():
                cur = _f(m.current_develop, key, 0.0)
                dev_by_id[m.photo_id][key] = int(max(-100, min(100, round(cur + d))))
            if deltas:
                n_hsl += 1
        diag.notes.append(f"hsl: {n_hsl}/{len(usable)} photo(s) ajustée(s)")

    adjustments = [
        PhotoAdjustment(photo_id=pid, develop=dev) for pid, dev in dev_by_id.items() if dev
    ]
    return adjustments, diag
