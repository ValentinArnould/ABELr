"""HSL audit/validation in rendered space on a finished catalog.

Measures per-band HSL statistics (`render_metrics.band_stats`) on the **rendered
preview** (Previews.lrdata, offline). Aggregates by band across the whole series:
median chroma, fraction of near-saturated pixels, hue dispersion. Used to spot
bands that are globally **oversaturated** (target #1 of the HSL calibration) and
hue-inconsistent, and to set per-band reference targets for `core.hsl`.

⚠️ Validates the per-band **measurement** on real renders. The **response** of
the HSL sliders (`core.response.BandResponse`) is calibrated by probing in Lr
(`render_probe` job).

Usage: python -m app.tools.validate_hsl "essais/essai CGC" [--limit N]
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
    """Circular standard deviation (degrees) — correct across the 0/360 wrap (Red band)."""
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

    # Per-band aggregates, over photos where the band is populated.
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

    print(f"{base.name}: {len(ids)} photos, {n_used} with rendered preview.\n")
    if not n_used:
        print("No rendered preview found.")
        return

    print(f"{'Band':<9} {'n':>4} {'C* med':>7} {'C* p90':>7} {'satClip':>8} "
          f"{'L* med':>7} {'hue σ':>6}")
    for name in render_metrics.BAND_NAMES:
        c = chroma.get(name)
        if not c:
            continue
        c = np.array(c)
        sc = np.array(satclip[name])
        print(f"{name:<9} {len(c):>4} {np.median(c):>7.1f} {np.percentile(c,90):>7.1f} "
              f"{sc.mean():>8.3f} {np.median(lvals[name]):>7.1f} {_circ_std_deg(hue[name]):>6.1f}")

    print("\nBands that are candidates for saturation reduction (high C* p90 or notable satClip):")
    flagged = []
    for name in render_metrics.BAND_NAMES:
        c = chroma.get(name)
        if not c:
            continue
        if np.percentile(c, 90) > 70 or np.mean(satclip[name]) > 0.03:
            flagged.append(name)
    print("  " + (", ".join(flagged) if flagged else "none"))


if __name__ == "__main__":
    main()
