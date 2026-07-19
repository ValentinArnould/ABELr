"""
Combien de SEEDS (photos retouchees a la main) faut-il par catalogue pour
calibrer le modele WB Temp ~ as-shot et l'expo ?

Pour k seeds tires au hasard : fit Temp ~ a*r/g + b*b/g + c sur k photos,
predit le reste -> RMSE. Tint et Expo : on prend la mediane des seeds.
Compare aussi 'pente physique fixe (a=2450) + intercept depuis seeds'.
Moyenne sur 200 tirages.

Usage : python -m app.tools.seed_curve
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
SLOPE_PRIOR = 2450.0  # K par unite r/g, stable ILCE-7M4 (mesure 2436/2459/2464)


def load_cgc():
    feat = ROOT / "essai CGC" / "_features.csv"
    lc = next((ROOT / "essai CGC").glob("**/*.lrcat"))
    con = catalog.open_readonly(str(lc))
    dev = {}
    try:
        for base, text in con.execute(
            """SELECT f.baseName,d.text FROM AgLibraryFile f
               JOIN Adobe_images i ON i.rootFile=f.id_local
               JOIN Adobe_imageDevelopSettings d ON d.image=i.id_local"""):
            p = parse_develop(text or "")
            dev[base] = (p.get("Temperature"), p.get("Tint"), p.get("Exposure2012"))
    finally:
        con.close()
    rows = []
    with feat.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = dev.get(r["photo"])
            if not t or t[0] is None:
                continue
            try:
                rows.append({"rg": float(r["asshot_rg"]), "bg": float(r["asshot_bg"]),
                             "temp": float(t[0]), "tint": float(t[1]),
                             "exp": float(t[2]) if t[2] is not None else 0.0})
            except (ValueError, KeyError):
                pass
    return rows


def load_yggdrasil():
    rows = []
    with (ROOT / "essai v3" / "Yggdrasil FFL 25" / "_features.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append({"rg": float(r["asshot_rg"]), "bg": float(r["asshot_bg"]),
                             "temp": float(r["temp"]), "tint": float(r["tint"]),
                             "exp": float(r["exp"])})
            except (ValueError, KeyError):
                pass
    return rows


def eval_seeds(data, k, trials=200, rng=None):
    rng = rng or np.random.default_rng(0)
    n = len(data)
    rg = np.array([d["rg"] for d in data]); bg = np.array([d["bg"] for d in data])
    temp = np.array([d["temp"] for d in data]); tint = np.array([d["tint"] for d in data])
    exp = np.array([d["exp"] for d in data])
    r_full, r_slope, r_tint, r_exp = [], [], [], []
    for _ in range(trials):
        idx = rng.choice(n, k, replace=False)
        mask = np.ones(n, bool); mask[idx] = False
        # 2D full fit (si k>=3)
        if k >= 3:
            X = np.vstack([rg[idx], bg[idx], np.ones(k)]).T
            coef, *_ = np.linalg.lstsq(X, temp[idx], rcond=None)
            Xp = np.vstack([rg[mask], bg[mask], np.ones(mask.sum())]).T
            r_full.append(np.sqrt(((Xp @ coef - temp[mask]) ** 2).mean()))
        # pente fixe + intercept median des seeds
        inter = np.median(temp[idx] - SLOPE_PRIOR * rg[idx])
        pred = SLOPE_PRIOR * rg[mask] + inter
        r_slope.append(np.sqrt(((pred - temp[mask]) ** 2).mean()))
        # tint / exp = mediane seeds
        r_tint.append(np.sqrt(((np.median(tint[idx]) - tint[mask]) ** 2).mean()))
        r_exp.append(np.sqrt(((np.median(exp[idx]) - exp[mask]) ** 2).mean()))
    return (np.mean(r_full) if r_full else float("nan"),
            np.mean(r_slope), np.mean(r_tint), np.mean(r_exp))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for name, data in [("CGC (froid, n=%d)", load_cgc()), ("YGGDRASIL (artistique, n=%d)", load_yggdrasil())]:
        print("\n" + "=" * 64)
        print(name % len(data))
        temp = np.array([d["temp"] for d in data])
        tint = np.array([d["tint"] for d in data])
        exp = np.array([d["exp"] for d in data])
        print(f"  Temp sigma {temp.std():.0f}K | Tint sigma {tint.std():.1f} | Exp sigma {exp.std():.3f}EV")
        print(f"  {'k seeds':<10}{'Temp 2Dfit':>12}{'Temp slope+seed':>17}{'Tint=med':>10}{'Exp=med':>10}")
        for k in (3, 5, 8, 12, 20, 40):
            if k >= len(data):
                break
            full, slope, t, e = eval_seeds(data, k)
            print(f"  {k:<10}{full:>11.0f}K{slope:>16.0f}K{t:>10.1f}{e:>9.3f}")


if __name__ == "__main__":
    main()
