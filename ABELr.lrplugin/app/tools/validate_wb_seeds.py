"""Validation of the WB-seeds model (core.wb_model + core.regime) on a catalog.

Simulates the real workflow: draws k seeds at random, calibrates, predicts the
Temperature of the remaining photos, compares against the Temperature actually
chosen. Uses the `_features.csv` cache (as-shot) + the catalog (chosen Temp/Tint/Exp).

Usage: python -m app.tools.validate_wb_seeds "essais/essai CGC" [--k 6] [--trials 300]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import catalog, wb_model, regime  # noqa: E402
from app.core.wb_model import Seed  # noqa: E402
from app.tools.analyze_ground_truth import parse_develop  # noqa: E402


def load_rows(base: Path):
    lc = next(base.glob("**/*.lrcat"))
    con = catalog.open_readonly(str(lc))
    dev = {}
    try:
        for bn, text in con.execute(
            """SELECT f.baseName,d.text FROM AgLibraryFile f
               JOIN Adobe_images i ON i.rootFile=f.id_local
               JOIN Adobe_imageDevelopSettings d ON d.image=i.id_local"""):
            p = parse_develop(text or "")
            dev[bn] = (p.get("Temperature"), p.get("Tint"), p.get("Exposure2012"))
    finally:
        con.close()
    rows = []
    with (base / "_features.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = dev.get(r["photo"])
            if not t or t[0] is None:
                continue
            try:
                rows.append({
                    "id": r["photo"],
                    "rg": float(r["asshot_rg"]), "bg": float(r["asshot_bg"]),
                    "temp": float(t[0]), "tint": float(t[1] or 0),
                    "exp": float(t[2] or 0),
                })
            except (ValueError, KeyError):
                pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--camera", default="ILCE-7M4")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    base = Path(a.folder).resolve()
    rows = load_rows(base)
    n = len(rows)
    slope = wb_model.slope_for_camera(a.camera)
    print(f"{base.name}: {n} photos, slope {a.camera}={slope:.0f}K/[r/g], k={a.k} seeds, {a.trials} draws\n")

    temp_all = np.array([r["temp"] for r in rows])
    tint_all = np.array([r["tint"] for r in rows])
    base_rmse = float(np.sqrt(((temp_all - np.median(temp_all)) ** 2).mean()))

    rng = np.random.default_rng(0)
    errs, tint_errs, residuals = [], [], []
    regimes = {"physics": 0, "uncertain": 0, "artistic": 0}
    for _ in range(a.trials):
        idx = rng.choice(n, a.k, replace=False)
        seed_set = set(idx.tolist())
        seeds = [Seed(rows[i]["id"], rows[i]["rg"], rows[i]["bg"],
                      rows[i]["temp"], rows[i]["tint"], rows[i]["exp"]) for i in idx]
        cal = wb_model.calibrate(seeds, slope)
        rep = regime.detect(cal)
        regimes[rep.regime.value] += 1
        residuals.append(cal.residual_k)
        # predict the others
        pred, true_t, true_tint = [], [], []
        for i, r in enumerate(rows):
            if i in seed_set:
                continue
            pred.append(cal.predict_temperature(r["rg"]))
            true_t.append(r["temp"])
            true_tint.append(r["tint"])
        pred = np.array(pred); true_t = np.array(true_t)
        errs.append(np.sqrt(((pred - true_t) ** 2).mean()))
        tint_errs.append(np.sqrt(((cal.tint - np.array(true_tint)) ** 2).mean()))

    errs = np.array(errs); tint_errs = np.array(tint_errs); residuals = np.array(residuals)
    print(f"=== Temperature (target σ {temp_all.std():.0f}K) ===")
    print(f"  baseline (median)      : {base_rmse:.0f}K")
    print(f"  seed model (fixed slope): {errs.mean():.0f}K  (±{errs.std():.0f}, "
          f"p90 {np.percentile(errs,90):.0f})")
    print(f"  gain: {(1 - errs.mean()/base_rmse)*100:.0f}% vs knowing nothing")
    print(f"\n=== Tint (target σ {tint_all.std():.1f}) ===")
    print(f"  seed median RMSE: {tint_errs.mean():.1f}")
    print(f"\n=== Detected regime (median seed residual {np.median(residuals):.0f}K) ===")
    for k, v in regimes.items():
        print(f"  {k:<10} : {v*100//a.trials}%")


if __name__ == "__main__":
    main()
