"""Audit/validation HSL en espace rendu sur un catalogue fini.

Mesure les statistiques par bande HSL (`render_metrics.band_stats`) sur l'**aperçu
rendu** (Previews.lrdata, hors-ligne). Agrège par bande sur toute la série : chroma
médiane, fraction de pixels quasi-saturés, dispersion de teinte. Sert à repérer les
bandes globalement **sursaturées** (cible n°1 de l'étalonnage HSL) et incohérentes
en teinte, et à fixer des cibles de référence par bande pour `core.hsl`.

⚠️ Valide la **mesure** par bande sur de vrais rendus. La **réponse** des curseurs
HSL (`core.response.BandResponse`) se cale par sondage dans Lr (job `render_probe`).

Usage : python -m app.tools.validate_hsl "essais/essai CGC" [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import catalog, render_metrics  # noqa: E402
from app.core.previews import PreviewIndex  # noqa: E402


def _circ_std_deg(degrees: list[float]) -> float:
    """Écart-type circulaire (degrés) — correct au passage 0/360 (bande Red)."""
    ang = np.radians(np.asarray(degrees, float))
    r = np.hypot(np.mean(np.cos(ang)), np.mean(np.sin(ang)))
    if r <= 1e-9:
        return 180.0
    return float(np.degrees(np.sqrt(max(0.0, -2.0 * np.log(r)))))


def list_ids(lrcat: Path) -> list[str]:
    con = catalog.open_readonly(str(lrcat))
    try:
        return [r[0] for r in con.execute("SELECT id_global FROM Adobe_images")]
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    base = Path(a.folder).resolve()
    lrcat = next(base.glob("**/*.lrcat"))
    ids = list_ids(lrcat)
    if a.limit:
        ids = ids[: a.limit]

    # Agrégats par bande sur les photos où la bande est peuplée.
    chroma = defaultdict(list)
    satclip = defaultdict(list)
    hue = defaultdict(list)
    lvals = defaultdict(list)
    n_used = 0

    with PreviewIndex(str(lrcat)) as idx:
        for gid in ids:
            rgb = idx.load_rendered(gid)
            if rgb is None:
                continue
            n_used += 1
            for b in render_metrics.band_stats(rgb):
                if not render_metrics.band_is_reliable(b):
                    continue
                chroma[b.name].append(b.median_chroma)
                satclip[b.name].append(b.sat_clip_frac)
                hue[b.name].append(b.median_hue)
                lvals[b.name].append(b.median_l)

    print(f"{base.name} : {len(ids)} photos, {n_used} avec aperçu rendu.\n")
    if not n_used:
        print("Aucun aperçu rendu trouvé.")
        return

    print(f"{'Bande':<9} {'n':>4} {'C* méd':>7} {'C* p90':>7} {'satClip':>8} "
          f"{'L* méd':>7} {'hue σ':>6}")
    for name in render_metrics.BAND_NAMES:
        c = chroma.get(name)
        if not c:
            continue
        c = np.array(c)
        sc = np.array(satclip[name])
        print(f"{name:<9} {len(c):>4} {np.median(c):>7.1f} {np.percentile(c,90):>7.1f} "
              f"{sc.mean():>8.3f} {np.median(lvals[name]):>7.1f} {_circ_std_deg(hue[name]):>6.1f}")

    print("\nBandes candidates à réduction de saturation (C* p90 élevée ou satClip notable) :")
    flagged = []
    for name in render_metrics.BAND_NAMES:
        c = chroma.get(name)
        if not c:
            continue
        if np.percentile(c, 90) > 70 or np.mean(satclip[name]) > 0.03:
            flagged.append(name)
    print("  " + (", ".join(flagged) if flagged else "aucune"))


if __name__ == "__main__":
    main()
