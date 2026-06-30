"""Re-validation : pipeline RAW **GPU** vs **LibRaw** (CPU) — parité des scalaires.

Le passage du décodage RAW sur GPU (`core.gpu_raw`) remplace LibRaw (`core.raw.load_linear`
+ `core.analysis`). Comme le demosaic et la conversion couleur diffèrent, ce script mesure
l'écart sur les scalaires qui pilotent les corrections : exposition (Y moyen/médian),
gray-world (g/r, g/b), WB as-shot. Si l'écart dépasse la tolérance, ajuster la matrice
couleur / l'adaptation dans `gpu_raw._cam_to_prophoto` avant de faire confiance au GPU.

Usage :
    python -m app.tools.validate_gpu_vs_libraw <dossier_ou_fichiers...> [--n 8]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from app.core import analysis, gpu_raw, raw


def _libraw_scalars(path: str) -> dict[str, float]:
    rgb = raw.load_linear(path, half_size=True)
    expo = analysis.exposure_stats(rgb)
    gw_rg, gw_bg = analysis.gray_world_wb(rgb)
    as_rg, as_bg = raw.read_asshot_wb(path)
    return {
        "mean_luma": expo.mean_luma, "median_luma": expo.median_luma,
        "gw_rg": gw_rg, "gw_bg": gw_bg, "asshot_rg": as_rg, "asshot_bg": as_bg,
    }


def _gpu_scalars(path: str) -> dict[str, float]:
    res = gpu_raw.analyze_raw_gpu(path)
    if res is None:
        raise RuntimeError(f"GPU decode échoué : {path}")
    return {
        "mean_luma": res.exposure.mean_luma, "median_luma": res.exposure.median_luma,
        "gw_rg": res.grayworld_rg, "gw_bg": res.grayworld_bg,
        "asshot_rg": res.asshot_rg, "asshot_bg": res.asshot_bg,
    }


def _collect_paths(args: list[str], n: int) -> list[str]:
    paths: list[str] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths += [str(x) for x in sorted(p.rglob("*.ARW"))]
        elif p.is_file():
            paths.append(str(p))
    return paths[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="dossier(s) ou fichier(s) ARW")
    ap.add_argument("--n", type=int, default=8, help="nb de RAW à échantillonner")
    args = ap.parse_args()

    paths = _collect_paths(args.paths, args.n)
    if not paths:
        print("Aucun ARW trouvé.")
        return 1

    keys = ["mean_luma", "median_luma", "gw_rg", "gw_bg", "asshot_rg", "asshot_bg"]
    diffs: dict[str, list[float]] = {k: [] for k in keys}
    lib_vals: dict[str, list[float]] = {k: [] for k in keys}
    gpu_vals: dict[str, list[float]] = {k: [] for k in keys}

    for path in paths:
        try:
            lib = _libraw_scalars(path)
            gp = _gpu_scalars(path)
        except Exception as exc:
            print(f"  SKIP {Path(path).name} : {exc}")
            continue
        print(f"\n{Path(path).name}")
        for k in keys:
            d = gp[k] - lib[k]
            diffs[k].append(abs(d))
            lib_vals[k].append(lib[k])
            gpu_vals[k].append(gp[k])
            print(f"  {k:12s} lib={lib[k]:8.4f}  gpu={gp[k]:8.4f}  d={d:+.4f}")

    print("\n=== Résumé (écart absolu moyen + corrélation) ===")
    for k in keys:
        if not diffs[k]:
            continue
        mad = float(np.mean(diffs[k]))
        if len(lib_vals[k]) >= 2 and np.std(lib_vals[k]) > 1e-9:
            corr = float(np.corrcoef(lib_vals[k], gpu_vals[k])[0, 1])
        else:
            corr = float("nan")
        print(f"  {k:12s} MAD={mad:.4f}  corr={corr:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
