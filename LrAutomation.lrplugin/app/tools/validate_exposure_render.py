"""Audit/validation de l'exposition en **espace rendu** sur un catalogue fini.

Mesure la clarté CIE L* (`render_metrics.tone_stats`) sur l'**aperçu rendu**
(Previews.lrdata — déjà rendu par Lr avec les réglages du photographe, donc hors-ligne,
sans Lr ouvert). Sur une série finie et bien équilibrée, la clarté rendue doit être
**resserrée** : ça valide que la médiane L* est une cible d'event pertinente (chemin
primaire de `core.exposure`), et ça liste les photos les plus éloignées (celles qu'un
rééquilibrage toucherait).

⚠️ Ce script valide la **mesure** (cible L* rendue) sur de vrais rendus. L'**inversion**
ΔL*→ΔEV (`core.response`) se valide séparément par sondage dans Lr (job `render_probe`),
car elle exige de re-rendre après application — impossible hors-ligne.

Usage : python -m app.tools.validate_exposure_render "essais/essai CGC" [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import catalog, render_metrics  # noqa: E402
from app.core.previews import PreviewIndex  # noqa: E402
from app.tools.analyze_ground_truth import parse_develop  # noqa: E402


def load_photos(lrcat: Path) -> list[dict]:
    """(id_global, baseName, Exposure2012) de chaque photo du catalogue."""
    con = catalog.open_readonly(str(lrcat))
    rows: list[dict] = []
    try:
        for id_global, base, text in con.execute(
            """SELECT i.id_global, f.baseName, d.text
               FROM Adobe_images i
               JOIN AgLibraryFile f ON i.rootFile = f.id_local
               LEFT JOIN Adobe_imageDevelopSettings d ON d.image = i.id_local"""):
            p = parse_develop(text or "")
            rows.append({"id": id_global, "base": base, "exp": p.get("Exposure2012")})
    finally:
        con.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--limit", type=int, default=0, help="0 = toutes les photos")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    base = Path(a.folder).resolve()
    lrcat = next(base.glob("**/*.lrcat"))
    rows = load_photos(lrcat)
    if a.limit:
        rows = rows[: a.limit]

    lstars: list[float] = []
    exps: list[float] = []
    measured: list[dict] = []
    n_no_preview = 0

    with PreviewIndex(str(lrcat)) as idx:
        for r in rows:
            rgb = idx.load_rendered(r["id"])
            if rgb is None:
                n_no_preview += 1
                continue
            ts = render_metrics.tone_stats(rgb)
            lstars.append(ts.median_l)
            measured.append({"base": r["base"], "l": ts.median_l,
                             "hi": ts.clipped_hi, "lo": ts.clipped_lo})
            if r["exp"] is not None:
                exps.append(float(r["exp"]))

    print(f"{base.name} : {len(rows)} photos, {len(lstars)} avec aperçu rendu "
          f"({n_no_preview} sans).\n")
    if not lstars:
        print("Aucun aperçu rendu trouvé (Previews.lrdata absent ?).")
        return

    arr = np.array(lstars)
    target = float(np.median(arr))
    print("=== Clarté rendue (CIE L*) ===")
    print(f"  cible (médiane) : {target:.1f}")
    print(f"  σ série          : {arr.std():.1f}   (resserré = bien équilibré)")
    print(f"  étendue p05–p95  : {np.percentile(arr,5):.1f} … {np.percentile(arr,95):.1f}")
    if exps:
        print(f"  Exposure2012 choisi : médiane {np.median(exps):+.2f} EV, σ {np.std(exps):.2f}")

    # Photos les plus éloignées de la cible = candidates à rééquilibrage.
    measured.sort(key=lambda m: abs(m["l"] - target), reverse=True)
    print("\n=== Plus éloignées de la cible (un rééquilibrage les toucherait) ===")
    for m in measured[:10]:
        print(f"  {m['base']:<28} L*={m['l']:5.1f}  ΔL*={m['l']-target:+5.1f}  "
              f"clipHL={m['hi']:.2f} clipBL={m['lo']:.2f}")


if __name__ == "__main__":
    main()
