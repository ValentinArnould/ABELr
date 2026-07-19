"""Chaleur/coherence WB des JPEG finals (rendu voulu par le photographe).

Mesure gray-world g/r, g/b sur un echantillon de finals. Une WB "coherente neutre"
a g/r et g/b proches de 1 (gris moyen neutre). Trop chaud = exces de rouge
(g/r < 1) et/ou manque de bleu (g/b > 1). On regarde la mediane et la dispersion.

Usage : python -m app.tools.finals_warmth "essais/essai CGC" [--sample 250]
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np, cv2

_REC709 = np.array([0.2126, 0.7152, 0.0722], np.float32)


def warmth(path: str):
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    if w > 1024:
        img = cv2.resize(img, (1024, round(1024 * h / w)), interpolation=cv2.INTER_AREA)
    x = img[:, :, ::-1].astype(np.float32) / 255.0
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    f = lin.reshape(-1, 3) + 1e-9
    m = f.mean(0)
    return {"gr": float(m[1] / m[0]), "gb": float(m[1] / m[2])}


def _w(p):
    return warmth(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("--sample", type=int, default=250)
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    base = Path(a.folder).resolve()
    jpg = sorted((base / "RTH").rglob("*.JPG")) + sorted((base / "RTH").rglob("*.jpg"))
    if a.sample and len(jpg) > a.sample:
        rng = np.random.default_rng(1)
        jpg = [jpg[i] for i in sorted(rng.choice(len(jpg), a.sample, replace=False))]
    print(f"{len(jpg)} finals...")
    with ProcessPoolExecutor(max_workers=10) as ex:
        res = [r for r in ex.map(_w, [str(p) for p in jpg]) if r]
    gr = np.array([r["gr"] for r in res]); gb = np.array([r["gb"] for r in res])
    print(f"\nGray-world final (n={len(res)}) :")
    print(f"  g/r : med {np.median(gr):.3f}  sigma {gr.std():.3f}  [{np.percentile(gr,10):.2f}, {np.percentile(gr,90):.2f}]")
    print(f"  g/b : med {np.median(gb):.3f}  sigma {gb.std():.3f}  [{np.percentile(gb,10):.2f}, {np.percentile(gb,90):.2f}]")
    # interpretation
    warm = np.mean(gr < 0.9) * 100  # exces rouge
    print(f"\n  Photos g/r<0.9 (chaudes/rouges) : {warm:.0f}%")
    if abs(np.median(gr) - 1) < 0.12 and abs(np.median(gb) - 1) < 0.15:
        print("  -> WB finale globalement COHERENTE (proche neutre)")
    else:
        bias = "chaude (rouge)" if np.median(gr) < 1 else "froide"
        print(f"  -> biais median {bias} ; dispersion {'serree' if gr.std()<0.12 else 'large'}")


if __name__ == "__main__":
    main()
