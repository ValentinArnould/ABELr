"""Audit/validation of exposure in **rendered space** on a finished catalog.

Measures CIE L* lightness (`render_metrics.tone_stats`) on the **rendered preview**
(Previews.lrdata — already rendered by Lr with the photographer's settings, so offline,
without Lr open). On a finished, well-balanced series, the rendered lightness should be
**tight**: this validates that the median L* is a relevant event target (primary path
of `core.exposure`), and it lists the photos furthest off (the ones a rebalance would touch).

⚠️ This script validates the **measurement** (rendered L* target) on real renders. The
**inversion** ΔL*→ΔEV (`core.response`) is validated separately by probing in Lr (job
`render_probe`), because it requires re-rendering after applying — impossible offline.

Usage: python -m app.tools.validate_exposure_render "essais/essai CGC" [--limit N]
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
    """(id_global, baseName, Exposure2012) for each photo in the catalog."""
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
    ap.add_argument("--limit", type=int, default=0, help="0 = all photos")
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

    print(f"{base.name}: {len(rows)} photos, {len(lstars)} with rendered preview "
          f"({n_no_preview} without).\n")
    if not lstars:
        print("No rendered preview found (Previews.lrdata missing?).")
        return

    arr = np.array(lstars)
    target = float(np.median(arr))
    print("=== Rendered lightness (CIE L*) ===")
    print(f"  target (median) : {target:.1f}")
    print(f"  σ series          : {arr.std():.1f}   (tight = well balanced)")
    print(f"  range p05–p95  : {np.percentile(arr,5):.1f} … {np.percentile(arr,95):.1f}")
    if exps:
        print(f"  Exposure2012 chosen : median {np.median(exps):+.2f} EV, σ {np.std(exps):.2f}")

    # Photos furthest from the target = candidates for rebalancing.
    measured.sort(key=lambda m: abs(m["l"] - target), reverse=True)
    print("\n=== Furthest from target (a rebalance would touch these) ===")
    for m in measured[:10]:
        print(f"  {m['base']:<28} L*={m['l']:5.1f}  ΔL*={m['l']-target:+5.1f}  "
              f"clipHL={m['hi']:.2f} clipBL={m['lo']:.2f}")


if __name__ == "__main__":
    main()
