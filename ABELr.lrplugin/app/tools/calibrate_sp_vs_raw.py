"""Smart Preview ↔ RAW calibration — inspection tool (verdict already reached).

VERDICT (real catalog): the Smart Preview is camera-native raw (LinearRaw,
before WB and the color matrix) → inconsistent exposure offset (σ ≈ 1.3 stop)
and unmanageable WB ratios vs. developed RAW. Conclusion: **analyze RAW only**
(see `image_source` / `previews`). This tool remains useful for re-inspecting
a catalog or re-testing a possible future SP de-raw-matrixing.


For each photo that has both a Smart Preview and a RAW, decode both in
**scene-linear** space and compare the global statistics that drive the
analysis: mean luminance (exposure) and per-channel ratios (gray-world / WB).

What the output settles:
- Small and **constant** Δ luma → the Smart Preview is enough; an offset can
  be applied to the RAW fallback to make the two sources consistent.
- Small Δ channel ratios → SP≈RAW primaries for WB; no need for a primaries
  conversion matrix in v1.
- Scattered Δ (large std dev) → sources are not interchangeable; dig further.

Usage:
    python -m app.tools.calibrate_sp_vs_raw \
        "C:/photos sony/Catalogues/Last soirée Abreu/Last soirée Abreu.lrcat" [N]

N = max number of photos (default: all photos with both SP + RAW).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

from ..core import catalog, raw
from ..core.previews import PreviewIndex, decode_smart_preview

# Rec.709 luminance weights (applied in linear space).
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _stats(rgb: np.ndarray) -> dict:
    """Per-channel means + luma + gray-world ratios, on linear float RGB."""
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
    """(stem, id_global, raw_path) for photos that have both SP + RAW present."""
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
        print("No photo with both Smart Preview + RAW present.")
        return 1
    print(f"{len(photos)} photo(s) — SP vs RAW (scene-linear)\n")
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
        print(f"  {name:<10} mean {a.mean():+.3f}  std dev {a.std():.3f}  "
              f"[min {a.min():+.3f} / max {a.max():+.3f}]")

    print("\nAggregate:")
    line("Δexpo", d_ev)   # stops; constant ⇒ offset can be applied to the RAW fallback
    line("Δ(g/r)%", d_gr)  # ~0 ⇒ primaries compatible for WB
    line("Δ(g/b)%", d_bg)
    print("\nReading: low std dev ⇒ sources consistent (constant offset "
          "correctable). High std dev ⇒ SP and RAW are not interchangeable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
