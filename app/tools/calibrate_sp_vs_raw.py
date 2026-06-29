"""Calibration Smart Preview ↔ RAW — outil d'inspection (verdict déjà rendu).

VERDICT (catalogue réel) : la Smart Preview est du raw caméra-natif (LinearRaw,
avant WB et matrice couleur) → écart d'exposition incohérent (σ ≈ 1.3 stop) et
ratios WB ingérables vs RAW développé. Conclusion : **analyse sur RAW seul**
(cf. `image_source` / `previews`). Cet outil reste utile pour ré-inspecter un
catalogue ou re-tester une éventuelle dérawmatisation SP future.


Pour chaque photo possédant à la fois une Smart Preview et un RAW, décode les
deux en **scène-linéaire** et compare les statistiques globales qui pilotent
l'analyse : luminance moyenne (exposition) et ratios par canal (gray-world / WB).

Ce que la sortie tranche :
- Δ luma faible et **constant** → la Smart Preview suffit ; offset applicable au
  repli RAW pour rendre les deux sources cohérentes.
- Δ ratios de canaux faible → primaires SP≈RAW pour la WB ; pas besoin de matrice
  de conversion de primaires en v1.
- Δ dispersé (grand écart-type) → sources non interchangeables ; creuser.

Usage :
    python -m app.tools.calibrate_sp_vs_raw \
        "C:/photos sony/Catalogues/Last soirée Abreu/Last soirée Abreu.lrcat" [N]

N = nombre max de photos (défaut : toutes celles ayant SP + RAW).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

from ..core import catalog, raw
from ..core.previews import PreviewIndex, decode_smart_preview

# Poids de luminance Rec.709 (appliqués en linéaire).
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _stats(rgb: np.ndarray) -> dict:
    """Moyennes par canal + luma + ratios gray-world, sur du RGB float linéaire."""
    flat = rgb.reshape(-1, 3).astype(np.float32)
    mean = flat.mean(0) + 1e-9  # (R, G, B)
    luma = float((flat * _LUMA).sum(1).mean())
    return {
        "mean_r": float(mean[0]),
        "mean_g": float(mean[1]),
        "mean_b": float(mean[2]),
        "luma": luma,
        "gr": float(mean[1] / mean[0]),  # g/r
        "bg": float(mean[1] / mean[2]),  # g/b
    }


def _collect(lrcat: str, limit: int | None) -> list[tuple[str, str, str]]:
    """(stem, id_global, raw_path) pour les photos ayant SP + RAW présent."""
    con = catalog.open_readonly(catalog.resolve_catalog(lrcat).lrcat)
    idx = PreviewIndex(lrcat)
    out: list[tuple[str, str, str]] = []
    try:
        for (id_global,) in con.execute("SELECT id_global FROM Adobe_images"):
            rawp = catalog.resolve_raw_path(con, id_global)
            if not rawp or not Path(rawp).is_file():
                continue
            if idx.smart_path(id_global) is None:
                continue
            out.append((Path(rawp).stem, id_global, rawp))
            if limit and len(out) >= limit:
                break
    finally:
        idx.close()
        con.close()
    return sorted(out)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    lrcat = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

    photos = _collect(lrcat, limit)
    if not photos:
        print("Aucune photo avec Smart Preview + RAW présent.")
        return 1
    print(f"{len(photos)} photo(s) — SP vs RAW (scène-linéaire)\n")
    print(f"{'photo':<10} {'Δexpo(stops)':>12} {'Δ(g/r)%':>9} {'Δ(g/b)%':>9} "
          f"{'sp_luma':>8} {'raw_luma':>8}")

    d_ev, d_gr, d_bg = [], [], []
    idx = PreviewIndex(lrcat)
    try:
        for stem, id_global, rawp in photos:
            sp = decode_smart_preview(idx.smart_path(id_global), normalize=True)
            rw = raw.load_linear(rawp, half_size=True)
            s, r = _stats(sp), _stats(rw)
            ev = math.log2(s["luma"] / r["luma"]) if r["luma"] > 0 else float("nan")
            gr = (s["gr"] / r["gr"] - 1.0) * 100.0
            bg = (s["bg"] / r["bg"] - 1.0) * 100.0
            d_ev.append(ev); d_gr.append(gr); d_bg.append(bg)
            print(f"{stem:<10} {ev:>12.3f} {gr:>9.2f} {bg:>9.2f} "
                  f"{s['luma']:>8.4f} {r['luma']:>8.4f}")
    finally:
        idx.close()

    def line(name, xs):
        a = np.array(xs)
        print(f"  {name:<10} moy {a.mean():+.3f}  écart-type {a.std():.3f}  "
              f"[min {a.min():+.3f} / max {a.max():+.3f}]")

    print("\nAgrégat :")
    line("Δexpo", d_ev)   # stops ; constant ⇒ offset applicable au repli RAW
    line("Δ(g/r)%", d_gr)  # ~0 ⇒ primaires compatibles pour la WB
    line("Δ(g/b)%", d_bg)
    print("\nLecture : écart-type faible ⇒ sources cohérentes (offset constant "
          "corrigeable). Écart-type élevé ⇒ SP et RAW non interchangeables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
