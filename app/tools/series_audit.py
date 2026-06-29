"""
Audit d'une serie evenementielle : determiner si expo/WB sont PREDICTIBLES
par photo, et dans quel REGIME de WB se situe la serie.

Question centrale : la serie SUIT-elle l'AWB boitier (regime ou Temp ~ as-shot
fonctionne, ex. St Valentin R2=0.92) ou IMPOSE-t-elle une teinte/expo arbitraire
(regime Yggdrasil ou tout echoue) ? Le 2e est l'exception (look artistique 2 mois) ;
le 1er devrait etre la norme des events retouches.

Pour chaque RAW (parallele) :
  - decode ProPhoto lineaire -> ymean global, gray-world g/r g/b
  - as-shot WB (rawpy camera_whitebalance) -> r/g, b/g
Catalogue : Exposure2012, Temperature, Tint par photo (baseName).

Sorties :
  - regressions expo (~log2 ymean) et WB (Temp/Tint ~ as-shot), R2 + LOO-RMSE
  - coherence WB : dispersion Temp/Tint choisis ; correlation as-shot<->choisi
  - chaleur : Temp median + part de photos "chaudes" (Temp > seuil)

Usage :
    python -m app.tools.series_audit "essais/essai CGC" [--workers 10] [--cache]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import rawpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import analysis, catalog, image_source  # noqa: E402
from app.tools.analyze_ground_truth import parse_develop  # noqa: E402


def raw_feats(arw: str) -> dict | None:
    try:
        loaded = image_source.load_for_analysis(arw)
        es = analysis.exposure_stats(loaded.rgb)
        gr, gb = analysis.gray_world_wb(loaded.rgb)
        with rawpy.imread(arw) as r:
            wb = list(r.camera_whitebalance)
        g = wb[1] or 1.0
        return {
            "photo": Path(arw).stem,
            "ymean": es.mean_luma,
            "gr": gr, "gb": gb,
            "asshot_rg": wb[0] / g, "asshot_bg": wb[2] / g,
        }
    except Exception:
        return None


def _worker(p):
    return raw_feats(p)


def read_catalog(lrcat: Path) -> dict[str, dict]:
    con = catalog.open_readonly(str(lrcat))
    out = {}
    try:
        rows = con.execute(
            """SELECT f.baseName, d.text
               FROM AgLibraryFile f
               JOIN Adobe_images i ON i.rootFile = f.id_local
               JOIN Adobe_imageDevelopSettings d ON d.image = i.id_local"""
        ).fetchall()
    finally:
        con.close()
    for base, text in rows:
        p = parse_develop(text or "")
        out[base] = {
            "exp":  p.get("Exposure2012"),
            "temp": p.get("Temperature"),
            "tint": p.get("Tint"),
            "wbmode": p.get("WhiteBalance"),
        }
    return out


def regress(x, y):
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 20:
        return None
    A = np.vstack([x, np.ones(n)]).T
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    H = (A @ np.linalg.pinv(A.T @ A) @ A.T).diagonal()
    loo = float(np.sqrt((((y - pred) / np.clip(1 - H, 1e-6, None)) ** 2).mean()))
    base = float(np.sqrt(((y - y.mean()) ** 2).mean()))
    return r2, loo, base, n, coef


def line(name, res, unit):
    if res is None:
        print(f"  {name:<26} (trop peu)")
        return
    r2, loo, base, n, _ = res
    flag = "  <== AIDE" if loo < base * 0.9 else ""
    print(f"  {name:<26} R2={r2:5.3f}  LOO={loo:8.3f}{unit}  base={base:7.3f}  n={n}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    base = Path(args.folder).resolve()
    lrcat = next(base.glob("**/*.lrcat"))
    print(f"Catalogue : {lrcat.name}")
    dev = read_catalog(lrcat)
    print(f"  {len(dev)} photos dans le catalogue")

    cache = base / "_features.csv"
    feats = {}
    if cache.is_file():
        print(f"Cache RAW : {cache}")
        with cache.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                feats[row["photo"]] = {k: float(v) for k, v in row.items()
                                       if k != "photo" and v not in ("", "None")}
    else:
        raws = sorted((base / "RAW").rglob("*.ARW"))
        if args.limit:
            raws = raws[: args.limit]
        print(f"Decodage {len(raws)} RAW ({args.workers} workers)...")
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, m in enumerate(ex.map(_worker, [str(r) for r in raws])):
                if m:
                    feats[m["photo"]] = m
                if (i + 1) % 100 == 0:
                    print(f"  {i+1}/{len(raws)}")
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["photo", "ymean", "gr", "gb", "asshot_rg", "asshot_bg"])
            w.writeheader()
            for v in feats.values():
                w.writerow(v)
        print(f"Ecrit {cache}")

    # merge
    rows = []
    for stem, d in dev.items():
        f = feats.get(stem)
        if not f or d["exp"] is None or d["temp"] is None:
            continue
        rows.append({**f, **d})
    n = len(rows)
    print(f"\nMerge : {n} photos\n")
    if n < 50:
        print("Trop peu.")
        return

    def col(k):
        return np.array([r.get(k) if r.get(k) is not None else np.nan for r in rows], np.float64)

    exp, temp, tint = col("exp"), col("temp"), col("tint")
    ymean = col("ymean")
    a_rg, a_bg = col("asshot_rg"), col("asshot_bg")

    print("=== CIBLES (ce que le photographe a choisi) ===")
    print(f"  Exposure2012 : med {np.nanmedian(exp):+.2f}  sigma {np.nanstd(exp):.3f}EV  "
          f"[{np.nanmin(exp):+.2f}, {np.nanmax(exp):+.2f}]")
    print(f"  Temperature  : med {np.nanmedian(temp):.0f}K  sigma {np.nanstd(temp):.0f}K  "
          f"[{np.nanmin(temp):.0f}, {np.nanmax(temp):.0f}]")
    print(f"  Tint         : med {np.nanmedian(tint):.0f}  sigma {np.nanstd(tint):.1f}  "
          f"[{np.nanmin(tint):.0f}, {np.nanmax(tint):.0f}]")
    # WB mode distribution
    modes = {}
    for r in rows:
        modes[r.get("wbmode")] = modes.get(r.get("wbmode"), 0) + 1
    print(f"  WB mode      : {modes}")

    print("\n=== EXPOSITION : Exposure2012 ~ log2(ymean global) ===")
    line("log2(ymean)", regress(np.log2(np.clip(ymean, 1e-6, None)), exp), "EV")

    print("\n=== WB TEMPERATURE : regime AWB-suivable ? ===")
    line("as-shot r/g",          regress(a_rg, temp), "K")
    line("as-shot b/g",          regress(a_bg, temp), "K")
    # modele 2D poole : Temp ~ a*r/g + b*b/g + c
    m = np.isfinite(a_rg) & np.isfinite(a_bg) & np.isfinite(temp)
    if m.sum() > 30:
        A = np.vstack([a_rg[m], a_bg[m], np.ones(m.sum())]).T
        coef, *_ = np.linalg.lstsq(A, temp[m], rcond=None)
        pred = A @ coef
        r2 = 1 - ((temp[m]-pred)**2).sum() / ((temp[m]-temp[m].mean())**2).sum()
        H = (A @ np.linalg.pinv(A.T @ A) @ A.T).diagonal()
        loo = float(np.sqrt((((temp[m]-pred)/np.clip(1-H,1e-6,None))**2).mean()))
        base = float(np.sqrt(((temp[m]-temp[m].mean())**2).mean()))
        flag = "  <== AIDE" if loo < base*0.9 else ""
        print(f"  {'2D as-shot (r/g,b/g)':<26} R2={r2:5.3f}  LOO={loo:8.1f}K  base={base:7.1f}  n={m.sum()}{flag}")

    print("\n=== WB TINT ~ as-shot ===")
    line("as-shot r/g", regress(a_rg, tint), "")
    line("as-shot b/g", regress(a_bg, tint), "")

    print("\n=== COHERENCE / CHALEUR WB ===")
    # gray-world final non dispo ici (pas de finals decodes) ; on juge sur Temp choisi
    warm = np.nanmean(temp > 5500) * 100
    cool = np.nanmean(temp < 4500) * 100
    print(f"  Temp > 5500K (chaud) : {warm:.0f}%   Temp < 4500K (froid) : {cool:.0f}%")
    # coherence : si Temp tres concentre -> serie homogene ; si suit as-shot -> per-photo physique
    res = regress(a_rg, temp)
    if res:
        r2 = res[0]
        if r2 > 0.4:
            print(f"  -> REGIME AWB-SUIVABLE (Temp ~ as-shot R2={r2:.2f}) : WB predictible physiquement")
        elif np.nanstd(temp) < 250:
            print(f"  -> REGIME SERIE-CONSTANTE (Temp sigma {np.nanstd(temp):.0f}K, as-shot ignore)")
        else:
            print(f"  -> REGIME MIXTE/ARTISTIQUE (Temp varie {np.nanstd(temp):.0f}K mais R2 as-shot {r2:.2f} faible)")


if __name__ == "__main__":
    main()
