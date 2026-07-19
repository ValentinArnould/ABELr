"""
Cross-catalog generalization of the WB Temp ~ as-shot model.

Decides the design: UNIVERSAL coefficients (one baked model, zero seed) or
PER-CATALOG (common slope + warmth bias, 3-5 seeds per event)?

Fit Temp ~ a*r/g + b*b/g + c on each catalog, then predict the OTHERS.
If predicting-out-of-catalog is ~ as good as in-sample -> universal.
If slope is stable but intercept varies -> seeds calibrate the intercept (warmth).

Usage: python -m app.tools.cross_catalog_wb
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import catalog  # noqa: E402
from app.tools.analyze_ground_truth import parse_develop  # noqa: E402

ROOT = Path(__file__).resolve().parents[2] / "essais"


def load_ground_truth(csv_path: Path) -> list[dict]:
    out = []
    with csv_path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append({
                    "rg": float(r["asshot_rg"]), "bg": float(r["asshot_bg"]),
                    "temp": float(r["temperature"]), "tint": float(r["tint"]),
                })
            except (ValueError, KeyError):
                pass
    return out


def load_yggdrasil(csv_path: Path) -> list[dict]:
    out = []
    with csv_path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append({
                    "rg": float(r["asshot_rg"]), "bg": float(r["asshot_bg"]),
                    "temp": float(r["temp"]), "tint": float(r["tint"]),
                })
            except (ValueError, KeyError):
                pass
    return out


def load_cgc(features_csv: Path, lrcat: Path) -> list[dict]:
    con = catalog.open_readonly(str(lrcat))
    dev = {}
    try:
        for base, text in con.execute(
            """SELECT f.baseName, d.text FROM AgLibraryFile f
               JOIN Adobe_images i ON i.rootFile=f.id_local
               JOIN Adobe_imageDevelopSettings d ON d.image=i.id_local"""):
            p = parse_develop(text or "")
            dev[base] = (p.get("Temperature"), p.get("Tint"))
    finally:
        con.close()
    out = []
    with features_csv.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = dev.get(r["photo"])
            if not t or t[0] is None:
                continue
            try:
                out.append({"rg": float(r["asshot_rg"]), "bg": float(r["asshot_bg"]),
                            "temp": float(t[0]), "tint": float(t[1])})
            except (ValueError, KeyError):
                pass
    return out


def fit(data):
    X = np.array([[d["rg"], d["bg"], 1.0] for d in data])
    y = np.array([d["temp"] for d in data])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coef


def rmse(coef, data):
    X = np.array([[d["rg"], d["bg"], 1.0] for d in data])
    y = np.array([d["temp"] for d in data])
    return float(np.sqrt(((X @ coef - y) ** 2).mean()))


def fit_slope_only(data):
    """Common slope assumed; returns (a,b) averages via fit, intercept free."""
    return fit(data)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    sets = {}
    p = ROOT / "essai independant" / "_ground_truth.csv"
    if p.is_file():
        sets["indep1(CGC/JTH)"] = load_ground_truth(p)
    p = ROOT / "essai independant 2" / "_ground_truth.csv"
    if p.is_file():
        sets["indep2(StValentin)"] = load_ground_truth(p)
    p = ROOT / "essai CGC" / "_features.csv"
    lc = next((ROOT / "essai CGC").glob("**/*.lrcat"), None)
    if p.is_file() and lc:
        sets["CGC(1004)"] = load_cgc(p, lc)
    p = ROOT / "essai v3" / "Yggdrasil FFL 25" / "_features.csv"
    if p.is_file():
        sets["Yggdrasil(1142)"] = load_yggdrasil(p)

    names = list(sets)
    print("Datasets:", {k: len(v) for k, v in sets.items()})
    print()

    # coefficients per dataset
    print("=== Coefficients Temp = a*(r/g) + b*(b/g) + c ===")
    coefs = {}
    for n in names:
        if len(sets[n]) < 8:
            continue
        c = fit(sets[n])
        coefs[n] = c
        temps = np.array([d["temp"] for d in sets[n]])
        print(f"  {n:<20} a={c[0]:8.0f}  b={c[1]:8.0f}  c={c[2]:8.0f}   "
              f"in-sample RMSE={rmse(c, sets[n]):6.0f}K  (Temp sigma {temps.std():.0f}K)")

    # cross generalization: fit on X, predict Y
    print("\n=== CROSS GENERALIZATION (RMSE K): row=fit on, column=predict ===")
    big = [n for n in names if len(sets[n]) >= 50]  # reliable models
    hdr = "  fit\\pred         " + "".join(f"{n[:12]:>14}" for n in names)
    print(hdr)
    for fn in (big or names):
        if fn not in coefs:
            continue
        row = f"  {fn[:16]:<16}"
        for pn in names:
            if len(sets[pn]) < 4:
                row += f"{'-':>14}"
                continue
            row += f"{rmse(coefs[fn], sets[pn]):>14.0f}"
        print(row)

    # baseline: predict via the target dataset's median Temp (no model)
    print("\n=== Baseline (target Temp median, no model) ===")
    for pn in names:
        temps = np.array([d["temp"] for d in sets[pn]])
        base = float(np.sqrt(((temps - np.median(temps)) ** 2).mean()))
        print(f"  {pn:<20} baseline RMSE={base:6.0f}K")

    # intercept stability: same slope, free intercept?
    print("\n=== Stable slope? (a,b normalized) ===")
    for n in coefs:
        c = coefs[n]
        print(f"  {n:<20} a/b ratio={c[0]/c[1]:6.2f}")


if __name__ == "__main__":
    main()
