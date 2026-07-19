"""
Test: k-means clustering on sharp zone of JPEG finals.
Per photo: detect sharp zone (Laplacian top%), cluster pixels (k-means),
select the most neutral cluster (min chroma), measure luminance + WB.
Compare sigma across series vs global stats (prev: global=0.99, zone-mean=0.73 stops).

Usage:
    python -m app.tools.cluster_sharp_zone <RTH_folder> [--k 3] [--sample N] [--workers 8]
"""

import sys
import argparse
import math
import numpy as np
import cv2
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

SHARP_FRAC = 0.15   # top fraction of smoothed |Laplacian| = sharp zone
SIGMA_BLUR  = 8     # Gaussian blur sigma for Laplacian smoothing
MIN_PIXELS  = 200   # min sharp-zone pixels to proceed


def _srgb_to_linear(bgr_u8: np.ndarray) -> np.ndarray:
    rgb = bgr_u8[:, :, ::-1].astype(np.float32) / 255.0
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _rec709_luma(lin_rgb: np.ndarray) -> np.ndarray:
    return lin_rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def analyze_jpeg(path: Path, k: int) -> dict | None:
    img = cv2.imread(str(path))
    if img is None:
        return None

    # Half-size: speed + less noise for Laplacian
    h, w = img.shape[:2]
    img = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)

    # --- Sharp zone mask ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    lap_s = cv2.GaussianBlur(lap, (0, 0), SIGMA_BLUR)
    thresh = np.percentile(lap_s, (1.0 - SHARP_FRAC) * 100)
    mask = lap_s >= thresh

    # --- Global stats (whole image) ---
    lin_full = _srgb_to_linear(img)
    luma_full = _rec709_luma(lin_full)
    r_g, g_g, b_g = lin_full[..., 0].mean(), lin_full[..., 1].mean(), lin_full[..., 2].mean()

    if mask.sum() < MIN_PIXELS:
        return None

    # --- Sharp zone pixels ---
    lin = lin_full
    zone_pix = lin[mask]  # (N, 3) float32

    # k-means on sharp zone
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1)
    _, labels, centers = cv2.kmeans(
        zone_pix.copy(), k, None, criteria, 5, cv2.KMEANS_PP_CENTERS
    )

    # Select cluster with minimum chroma (most neutral = closest to gray axis)
    chromas = [float(centers[i].max() - centers[i].min()) for i in range(k)]
    neutral_idx = int(np.argmin(chromas))

    # Also select cluster with max luminance (possible highlights/skin)
    lumas_c = [float(_rec709_luma(centers[i:i+1])[0]) for i in range(k)]
    bright_idx = int(np.argmax(lumas_c))

    def cluster_stats(idx):
        c = centers[idx]
        R, G, B = float(c[0]), float(c[1]), float(c[2])
        if R < 1e-7 or B < 1e-7:
            return None
        luma = 0.2126 * R + 0.7152 * G + 0.0722 * B
        # pixel count in this cluster
        cnt = int((labels.flatten() == idx).sum())
        return {"luma": luma, "gr": G / R, "gb": G / B, "chroma": chromas[idx], "n": cnt}

    neutral = cluster_stats(neutral_idx)
    bright  = cluster_stats(bright_idx)
    if neutral is None:
        return None

    # Zone mean (all sharp pixels, no clustering) for comparison
    zm_r, zm_g, zm_b = zone_pix[:, 0].mean(), zone_pix[:, 1].mean(), zone_pix[:, 2].mean()
    zm_luma = float(_rec709_luma(zone_pix).mean())

    return {
        # Global
        "g_luma":  float(luma_full.mean()),
        "g_gr":    g_g / r_g if r_g > 1e-7 else None,
        "g_gb":    g_g / b_g if b_g > 1e-7 else None,
        # Zone mean (all sharp pixels)
        "zm_luma": zm_luma,
        "zm_gr":   zm_g / zm_r if zm_r > 1e-7 else None,
        "zm_gb":   zm_g / zm_b if zm_b > 1e-7 else None,
        # Neutral cluster
        "n_luma":  neutral["luma"],
        "n_gr":    neutral["gr"],
        "n_gb":    neutral["gb"],
        "n_chroma": neutral["chroma"],
        "n_pct":   neutral["n"] / max(mask.sum(), 1),
        # Bright cluster (for reference)
        "b_luma":  bright["luma"] if bright else None,
    }


def _worker(args):
    path, k = args
    try:
        return analyze_jpeg(path, k)
    except Exception:
        return None


def _sigma_stops(lumas: np.ndarray) -> float:
    med = np.median(lumas)
    if med <= 0:
        return float("nan")
    return float(np.std(np.log2(lumas / med)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rth_folder")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    folder = Path(args.rth_folder)
    jpegs = sorted(folder.rglob("*.JPG")) + sorted(folder.rglob("*.jpg"))
    if not jpegs:
        print("No JPEGs found.")
        return

    if args.sample > 0:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(jpegs), min(args.sample, len(jpegs)), replace=False)
        jpegs = [jpegs[i] for i in sorted(idx)]

    print(f"Processing {len(jpegs)} JPEGs (k={args.k}, {args.workers} workers)...")

    task_args = [(p, args.k) for p in jpegs]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(_worker, task_args))

    valid = [r for r in results if r is not None]
    n = len(valid)
    print(f"Valid: {n}/{len(jpegs)}")
    if n < 10:
        print("Too few results.")
        return

    # Collect arrays
    def arr(key):
        vals = [r[key] for r in valid if r.get(key) is not None]
        return np.array(vals, np.float64)

    g_luma  = arr("g_luma")
    zm_luma = arr("zm_luma")
    n_luma  = arr("n_luma")
    g_gr    = arr("g_gr");   g_gb   = arr("g_gb")
    zm_gr   = arr("zm_gr");  zm_gb  = arr("zm_gb")
    n_gr    = arr("n_gr");   n_gb   = arr("n_gb")
    n_pct   = arr("n_pct")

    print()
    print(f"n={n}  (k={args.k})")
    print()
    print("LUMINANCE sigma (stops) :")
    print(f"  Global     : {_sigma_stops(g_luma):.3f}")
    print(f"  Sharp zone (mean) : {_sigma_stops(zm_luma):.3f}")
    print(f"  Neutral cluster    : {_sigma_stops(n_luma):.3f}  <-- target")
    print(f"  (neutral cluster = {n_pct.mean()*100:.1f}% of sharp-zone pixels on average)")
    print()
    print("WB g/r sigma :")
    print(f"  Global     : {g_gr.std():.4f}  med={np.median(g_gr):.3f}")
    print(f"  Sharp zone : {zm_gr.std():.4f}  med={np.median(zm_gr):.3f}")
    print(f"  Neutral cluster : {n_gr.std():.4f}  med={np.median(n_gr):.3f}")
    print()
    print("WB g/b sigma :")
    print(f"  Global     : {g_gb.std():.4f}  med={np.median(g_gb):.3f}")
    print(f"  Sharp zone : {zm_gb.std():.4f}  med={np.median(zm_gb):.3f}")
    print(f"  Neutral cluster : {n_gb.std():.4f}  med={np.median(n_gb):.3f}")
    print()
    print("Neutral cluster chroma (min=grayest) :")
    n_chroma = arr("n_chroma")
    print(f"  med={np.median(n_chroma):.4f}  sigma={n_chroma.std():.4f}")
    print()
    # Summary
    print("=== SUMMARY ===")
    sig_g  = _sigma_stops(g_luma)
    sig_zm = _sigma_stops(zm_luma)
    sig_n  = _sigma_stops(n_luma)
    print(f"Ratio sharp-zone/global  : {sig_zm/sig_g:.2f}x")
    print(f"Ratio neutral/global      : {sig_n/sig_g:.2f}x")
    if sig_n < sig_zm:
        print(f"Clustering HELPS (+{(sig_zm-sig_n)/sig_zm*100:.0f}% vs zone-mean)")
    else:
        print("Clustering does NOT help vs zone-mean")
    gr_ratio = n_gr.std() / g_gr.std()
    gb_ratio = n_gb.std() / g_gb.std()
    print(f"WB g/r: neutral/global = {gr_ratio:.2f}x  g/b: {gb_ratio:.2f}x")


if __name__ == "__main__":
    main()
