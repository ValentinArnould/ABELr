"""
Test decisif : metrique ZONE NETTE sur le RAW d'entree -> prediction Exposure/Temp.

Hypothese (validee partiellement sur finals) : le photographe expose et balance
la WB sur le SUJET NET, pas sur le cadre global. Les stats globales RAW ont echoue
(R2=0.15 expo, R2=0.06 WB sur 1157 photos). On teste si la zone nette du RAW fait mieux.

Pour chaque RAW :
  - decode ProPhoto lineaire (half_size) via image_source
  - detecte zone nette : |Laplacian| lisse, top SHARP_FRAC
  - mesure luminance Y + gray-world (g/r, g/b) DANS la zone nette
Merge avec _features.csv (cibles develop Exposure2012/Temperature/Tint + globaux),
puis regression + LOO-RMSE comparant zone-nette vs global.

Usage :
    python -m app.tools.sharp_raw_predict "essais/essai v3/Yggdrasil FFL 25" [--workers 10] [--sharp-csv path]
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import image_source, color  # noqa: E402

SHARP_FRAC = 0.15
SIGMA_BLUR = 8
MIN_PIXELS = 200


def sharp_raw_metrics(arw: str) -> dict | None:
    try:
        loaded = image_source.load_for_analysis(arw)
    except Exception:
        return None
    rgb = loaded.rgb  # HxWx3 float32 ProPhoto lineaire
    luma = rgb @ color.PROPHOTO_TO_Y  # Y exact

    # zone nette : Laplacian sur luma (gamma-encode pour stabilite du detecteur)
    luma_g = np.clip(luma, 0, 1) ** (1 / 2.2)
    lap = np.abs(cv2.Laplacian(luma_g.astype(np.float32), cv2.CV_32F))
    lap_s = cv2.GaussianBlur(lap, (0, 0), SIGMA_BLUR)
    thresh = np.percentile(lap_s, (1.0 - SHARP_FRAC) * 100)
    mask = lap_s >= thresh
    if mask.sum() < MIN_PIXELS:
        return None

    zr = rgb[mask]  # (N,3) zone nette
    zl = luma[mask]
    r, g, b = float(zr[:, 0].mean()), float(zr[:, 1].mean()), float(zr[:, 2].mean())
    return {
        "sz_ymean":   float(zl.mean()),
        "sz_ymedian": float(np.median(zl)),
        "sz_gr":      g / r if r > 1e-7 else None,
        "sz_gb":      g / b if b > 1e-7 else None,
    }


def _worker(arw: str):
    return arw, sharp_raw_metrics(arw)


# --------------------------------------------------------------------------- #
def load_features(csv_path: Path) -> dict[str, dict]:
    out = {}
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["photo"]] = row
    return out


def regress(x: np.ndarray, y: np.ndarray):
    """OLS y = a*x + b. Retourne (a, b, r2, loo_rmse, baseline_rmse)."""
    n = len(x)
    A = np.vstack([x, np.ones(n)]).T
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    a, b = coef
    pred = A @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    # LOO via hat matrix : residu_loo = residu / (1 - h_ii)
    H_diag = (A @ np.linalg.pinv(A.T @ A) @ A.T).diagonal()
    loo_res = (y - pred) / np.clip(1 - H_diag, 1e-6, None)
    loo_rmse = float(np.sqrt((loo_res ** 2).mean()))
    baseline = float(np.sqrt(((y - y.mean()) ** 2).mean()))
    return a, b, r2, loo_rmse, baseline


def report(name: str, x: np.ndarray, y: np.ndarray, unit: str):
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 20:
        print(f"  {name}: trop peu de points ({len(x)})")
        return
    a, b, r2, loo, base = regress(x, y)
    flag = " <== AIDE" if loo < base * 0.95 else ""
    print(f"  {name:<28} R2={r2:5.3f}  LOO-RMSE={loo:7.3f}{unit}  (baseline {base:6.3f}){flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--sharp-csv", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    base = Path(args.folder).resolve()
    feats = load_features(base / "_features.csv")
    sharp_csv = Path(args.sharp_csv) if args.sharp_csv else base / "_sharp.csv"

    # --- cache : decode seulement si _sharp.csv absent ---
    if sharp_csv.is_file():
        print(f"Cache trouve : {sharp_csv}")
        sharp = load_features(sharp_csv)
    else:
        raws = sorted((base / "RAW").rglob("*.ARW"))
        if args.limit:
            raws = raws[: args.limit]
        print(f"Decodage {len(raws)} RAW + zone nette ({args.workers} workers)...")
        sharp = {}
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, (arw, m) in enumerate(ex.map(_worker, [str(r) for r in raws])):
                if m is not None:
                    sharp[Path(arw).stem] = {"photo": Path(arw).stem, **m}
                if (i + 1) % 100 == 0:
                    print(f"  {i+1}/{len(raws)}")
        with sharp_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["photo", "sz_ymean", "sz_ymedian", "sz_gr", "sz_gb"])
            w.writeheader()
            for v in sharp.values():
                w.writerow(v)
        print(f"Ecrit {sharp_csv} ({len(sharp)} photos)")

    # --- merge ---
    rows = []
    for stem, f in feats.items():
        s = sharp.get(stem)
        if s is None:
            continue
        try:
            rows.append({
                "exp":  float(f["exp"]),
                "temp": float(f["temp"]),
                "tint": float(f["tint"]),
                "g_ymean":  float(f["ymean"]),
                "asshot_rg": float(f["asshot_rg"]),
                "asshot_bg": float(f["asshot_bg"]),
                "sz_ymean": float(s["sz_ymean"]),
                "sz_gr":    float(s["sz_gr"]),
                "sz_gb":    float(s["sz_gb"]),
            })
        except (ValueError, KeyError):
            continue

    n = len(rows)
    print(f"\nMerge : {n} photos avec zone-nette + cibles develop\n")
    if n < 50:
        print("Trop peu pour conclure.")
        return

    def col(k):
        return np.array([r[k] for r in rows], np.float64)

    exp, temp, tint = col("exp"), col("temp"), col("tint")
    g_y = np.log2(np.clip(col("g_ymean"), 1e-6, None))
    sz_y = np.log2(np.clip(col("sz_ymean"), 1e-6, None))

    print(f"Cibles : Exposure2012 sigma={exp.std():.3f}EV  Temp sigma={temp.std():.0f}K  Tint sigma={tint.std():.1f}")
    print()
    print("=== EXPOSITION (cible Exposure2012, unite EV) ===")
    report("global  log2(ymean)",  g_y,  exp, "EV")
    report("ZONE NETTE log2(ymean)", sz_y, exp, "EV")
    print()
    print("=== WB TEMPERATURE (cible Temp, unite K) ===")
    report("as-shot r/g",       col("asshot_rg"), temp, "K")
    report("ZONE NETTE g/r",    col("sz_gr"), temp, "K")
    report("ZONE NETTE g/b",    col("sz_gb"), temp, "K")
    print()
    print("=== WB TINT (cible Tint) ===")
    report("ZONE NETTE g/r",    col("sz_gr"), tint, "")
    report("ZONE NETTE g/b",    col("sz_gb"), tint, "")
    print()
    # dispersion zone-nette vs global (uniformite finals deja vue ; ici sur entree RAW)
    print("=== DISPERSION luminance RAW d'entree (sigma stops) ===")
    def sig_stops(y):
        med = np.median(y)
        return float(np.std(np.log2(np.clip(y, 1e-6, None) / med)))
    print(f"  global ymean    : {sig_stops(col('g_ymean')):.3f}")
    print(f"  zone nette ymean : {sig_stops(col('sz_ymean')):.3f}")


if __name__ == "__main__":
    main()
