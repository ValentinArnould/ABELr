"""Analyse « vérité terrain » : RAW d'entrée → réglages develop → JPEG final voulu.

But du projet : sur un dossier où l'on possède le RAW, les réglages Lightroom
appliqués (catalogue) ET le JPEG final exporté, mesurer le triplet
    (métrique RAW)  →  (réglage develop choisi)  →  (métrique JPEG final)
afin d'apprendre à prédire la BONNE EXPOSITION et la BONNE WB par photo (le style
étant appliqué par ailleurs). C'est le travail manuel coûteux que l'on veut automatiser.

Pour chaque photo :
- EXIF (iso, ouverture, vitesse, focale, objectif, boîtier) — depuis le catalogue.
- Réglages develop parsés (Adobe_imageDevelopSettings.text) : exposition, WB, tons,
  calibration, indicateurs de style (HSL / color grade / tone curve / Look).
- Métriques RAW en ProPhoto linéaire (core.analysis) : luminance Y, clipping,
  gray-world ; + WB as-shot (multiplicateurs rawpy).
- Métriques du JPEG embarqué (rendu boîtier, référence neutre).
- Métriques du JPEG final (la cible du photographe) : luminance, gray-world, contraste.

Sortie : impression détaillée par photo + synthèse croisée + **export CSV** (un
dataset par dossier/catalogue, réutilisable comme aide à la prédiction).

Usage :
    python -m app.tools.analyze_ground_truth "essais/essai independant 2" [--csv chemin.csv]

Sans --csv, le CSV est écrit à côté : <dossier>/_ground_truth.csv. Les catalogues
(.lrcat) sont découverts automatiquement sous le dossier ; chaque photo est reliée
au catalogue qui la contient (gère plusieurs catalogues dans un même dossier).
"""

from __future__ import annotations

import csv
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import rawpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import analysis, catalog, image_source  # noqa: E402

# Réglages develop scalaires d'intérêt (PV 2012+).
DEVELOP_KEYS = [
    "ProcessVersion", "WhiteBalance", "Temperature", "Tint",
    "Exposure2012", "Contrast2012", "Highlights2012", "Shadows2012",
    "Whites2012", "Blacks2012", "Clarity2012", "Dehaze", "Texture",
    "Vibrance", "Saturation", "CameraProfile",
    "RedHue", "RedSaturation", "GreenHue", "GreenSaturation",
    "BlueHue", "BlueSaturation",
]
STYLE_PREFIXES = ("HueAdjustment", "SaturationAdjustment", "LuminanceAdjustment",
                  "ColorGrade", "ToneCurvePV2012", "Look")
_REC709 = np.array([0.2126, 0.7152, 0.0722], np.float32)


# --------------------------------------------------------------------------- #
# Develop text (table Lua `s = { ... }`)
# --------------------------------------------------------------------------- #
def parse_develop(text: str) -> dict:
    out: dict = {}
    for k, v in re.findall(r'(\w+)\s*=\s*("[^"]*"|true|false|-?[\d.]+)', text):
        if v.startswith('"'):
            out[k] = v.strip('"')
        elif v in ("true", "false"):
            out[k] = v == "true"
        else:
            out[k] = float(v) if "." in v else int(v)
    return out


def style_flags(text: str) -> dict:
    parsed = parse_develop(text)
    flags = {}
    for pref in STYLE_PREFIXES:
        vals = [v for k, v in parsed.items()
                if k.startswith(pref) and isinstance(v, (int, float))]
        flags[pref] = any(abs(v) > 1e-6 for v in vals)
    flags["Look"] = 'Name = "' in text
    return flags


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
def discover_catalogs(base: Path) -> list[Path]:
    return sorted(base.glob("**/*.lrcat"))


def catalog_basenames(lrcat: Path) -> set[str]:
    con = catalog.open_readonly(str(lrcat))
    try:
        return {r[0] for r in con.execute("SELECT baseName FROM AgLibraryFile")}
    finally:
        con.close()


def fetch_catalog_row(lrcat: Path, basename: str) -> dict:
    con = catalog.open_readonly(str(lrcat))
    try:
        row = con.execute(
            """
            SELECT x.isoSpeedRating, x.aperture, x.shutterSpeed, x.focalLength,
                   l.value, cm.value, d.text
            FROM AgLibraryFile f
            JOIN Adobe_images i ON i.rootFile = f.id_local
            LEFT JOIN AgHarvestedExifMetadata x ON x.image = i.id_local
            LEFT JOIN AgInternedExifLens l ON l.id_local = x.lensRef
            LEFT JOIN AgInternedExifCameraModel cm ON cm.id_local = x.cameraModelRef
            JOIN Adobe_imageDevelopSettings d ON d.image = i.id_local
            WHERE f.baseName = ?
            """,
            (basename,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return {}
    iso, ap, sh, fl, lens, cam, text = row
    return {
        "exif": {
            "iso": int(iso) if iso else None,
            "f": round(2 ** (ap / 2), 1) if ap is not None else None,
            "shutter_s": round(2 ** (-sh), 6) if sh is not None else None,
            "focal": fl, "lens": lens, "camera": cam,
        },
        "develop": {k: parse_develop(text).get(k) for k in DEVELOP_KEYS},
        "style": style_flags(text),
    }


# --------------------------------------------------------------------------- #
# Métriques pixels
# --------------------------------------------------------------------------- #
def srgb_to_linear(u8: np.ndarray) -> np.ndarray:
    x = u8.astype(np.float32) / 255.0
    a = 0.055
    return np.where(x <= 0.04045, x / 12.92, ((x + a) / (1 + a)) ** 2.4)


def jpeg_metrics(rgb_u8: np.ndarray) -> dict:
    lin = srgb_to_linear(rgb_u8)
    luma = lin @ _REC709
    f = lin.reshape(-1, 3) + 1e-9
    m = f.mean(0)
    return {
        "y_mean": float(luma.mean()), "y_median": float(np.median(luma)),
        "gr": float(m[1] / m[0]), "gb": float(m[1] / m[2]),
        "hl": float((luma > 0.95).mean() * 100), "sh": float((luma < 0.002).mean() * 100),
        "contrast": float(np.percentile(luma, 90) - np.percentile(luma, 10)),
    }


def raw_metrics(arw: Path) -> dict:
    loaded = image_source.load_for_analysis(str(arw))
    es = analysis.exposure_stats(loaded.rgb)
    gr, gb = analysis.gray_world_wb(loaded.rgb)
    luma = (loaded.rgb @ np.array([0.2880, 0.7119, 0.0001], np.float32))
    p25, p75 = float(np.percentile(luma, 25)), float(np.percentile(luma, 75))
    with rawpy.imread(str(arw)) as r:
        wb = list(r.camera_whitebalance)
    g = wb[1] or 1.0
    return {
        "y_mean": es.mean_luma, "y_median": es.median_luma,
        "y_p25": p25, "y_p75": p75,
        "hl": es.clipped_highlights * 100, "sh": es.clipped_shadows * 100,
        "gr": gr, "gb": gb,
        "asshot_rg": round(wb[0] / g, 4), "asshot_bg": round(wb[2] / g, 4),
    }


def embedded_jpeg_metrics(arw: Path) -> dict | None:
    try:
        with rawpy.imread(str(arw)) as r:
            t = r.extract_thumb()
    except Exception:
        return None
    if t.format != rawpy.ThumbFormat.JPEG:
        return None
    bgr = cv2.imdecode(np.frombuffer(t.data, np.uint8), cv2.IMREAD_COLOR)
    return jpeg_metrics(bgr[:, :, ::-1]) if bgr is not None else None


def final_jpeg_metrics(jpg: Path) -> dict | None:
    bgr = cv2.imread(str(jpg), cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    h, w = bgr.shape[:2]
    if w > 1024:
        bgr = cv2.resize(bgr, (1024, round(1024 * h / w)), interpolation=cv2.INTER_AREA)
    return jpeg_metrics(bgr[:, :, ::-1])


# --------------------------------------------------------------------------- #
def analyze_folder(base: Path) -> list[dict]:
    cats = discover_catalogs(base)
    cat_names = {c: catalog_basenames(c) for c in cats}
    records: list[dict] = []
    for arw in sorted(base.glob("*.ARW")):
        stem = arw.stem
        lrcat = next((c for c, names in cat_names.items() if stem in names), None)
        rec = {"photo": stem, "catalog": lrcat.stem if lrcat else "?"}
        if lrcat:
            rec.update(fetch_catalog_row(lrcat, stem))
        rec["raw"] = raw_metrics(arw)
        rec["embedded"] = embedded_jpeg_metrics(arw)
        jpg = arw.with_suffix(".jpg")
        if not jpg.is_file():
            jpg = arw.with_suffix(".JPG")
        rec["final"] = final_jpeg_metrics(jpg) if jpg.is_file() else None
        records.append(rec)
    return records


# --------------------------------------------------------------------------- #
# Sorties
# --------------------------------------------------------------------------- #
CSV_FIELDS = [
    "photo", "catalog", "iso", "f", "shutter_s", "focal",
    "exposure2012", "temperature", "tint",
    "contrast2012", "highlights", "shadows", "whites", "blacks",
    "clarity", "dehaze", "vibrance", "saturation",
    "raw_ymean", "raw_ymedian", "raw_yp25", "raw_yp75", "raw_hl", "raw_sh",
    "raw_gr", "raw_gb", "asshot_rg", "asshot_bg",
    "emb_ymean", "emb_gr", "emb_gb",
    "fin_ymean", "fin_ymedian", "fin_hl", "fin_sh", "fin_gr", "fin_gb", "fin_contrast",
]


def to_row(rec: dict) -> dict:
    d, e = rec.get("develop", {}), rec.get("exif", {})
    r, em, fn = rec["raw"], rec.get("embedded") or {}, rec.get("final") or {}
    return {
        "photo": rec["photo"], "catalog": rec["catalog"],
        "iso": e.get("iso"), "f": e.get("f"), "shutter_s": e.get("shutter_s"),
        "focal": e.get("focal"),
        "exposure2012": d.get("Exposure2012"), "temperature": d.get("Temperature"),
        "tint": d.get("Tint"), "contrast2012": d.get("Contrast2012"),
        "highlights": d.get("Highlights2012"), "shadows": d.get("Shadows2012"),
        "whites": d.get("Whites2012"), "blacks": d.get("Blacks2012"),
        "clarity": d.get("Clarity2012"), "dehaze": d.get("Dehaze"),
        "vibrance": d.get("Vibrance"), "saturation": d.get("Saturation"),
        "raw_ymean": round(r["y_mean"], 5), "raw_ymedian": round(r["y_median"], 5),
        "raw_yp25": round(r["y_p25"], 5), "raw_yp75": round(r["y_p75"], 5),
        "raw_hl": round(r["hl"], 2), "raw_sh": round(r["sh"], 2),
        "raw_gr": round(r["gr"], 4), "raw_gb": round(r["gb"], 4),
        "asshot_rg": r["asshot_rg"], "asshot_bg": r["asshot_bg"],
        "emb_ymean": round(em.get("y_mean", 0), 5), "emb_gr": round(em.get("gr", 0), 4),
        "emb_gb": round(em.get("gb", 0), 4),
        "fin_ymean": round(fn.get("y_mean", 0), 5), "fin_ymedian": round(fn.get("y_median", 0), 5),
        "fin_hl": round(fn.get("hl", 0), 2), "fin_sh": round(fn.get("sh", 0), 2),
        "fin_gr": round(fn.get("gr", 0), 4), "fin_gb": round(fn.get("gb", 0), 4),
        "fin_contrast": round(fn.get("contrast", 0), 4),
    }


def write_csv(records: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for rec in records:
            w.writerow(to_row(rec))


def print_report(records: list[dict]) -> None:
    for rec in records:
        d, e, s = rec.get("develop", {}), rec.get("exif", {}), rec.get("style", {})
        r = rec["raw"]
        print("\n" + "=" * 78)
        print(f"{rec['photo']}  [{rec['catalog']}]  iso{e.get('iso')} f/{e.get('f')} "
              f"{e.get('shutter_s')}s {e.get('focal')}mm")
        print(f"  develop  WB={d.get('WhiteBalance')} {d.get('Temperature')}K/{d.get('Tint')}  "
              f"Expo={d.get('Exposure2012')}  HL={d.get('Highlights2012')} Sh={d.get('Shadows2012')} "
              f"Wh={d.get('Whites2012')} Bl={d.get('Blacks2012')}  profile={d.get('CameraProfile')} "
              f"look={s.get('Look')}")
        print(f"  RAW      Ylin={r['y_mean']:.4f} (med {r['y_median']:.4f} p25 {r['y_p25']:.4f} "
              f"p75 {r['y_p75']:.4f})  hl{r['hl']:.1f}% sh{r['sh']:.1f}%  "
              f"gray g/r={r['gr']:.2f} g/b={r['gb']:.2f}  as-shot r/g={r['asshot_rg']} b/g={r['asshot_bg']}")
        if rec.get("final"):
            fn = rec["final"]
            print(f"  JPEG fin Ylin={fn['y_mean']:.4f} (med {fn['y_median']:.4f})  "
                  f"hl{fn['hl']:.1f}% sh{fn['sh']:.1f}%  g/r={fn['gr']:.2f} g/b={fn['gb']:.2f}  "
                  f"contraste={fn['contrast']:.3f}")


def print_summary(records: list[dict]) -> None:
    print("\n" + "#" * 78 + "\n# SYNTHÈSE")
    for cat in sorted({r["catalog"] for r in records}):
        rs = [r for r in records if r["catalog"] == cat]
        const, var = [], []
        for k in DEVELOP_KEYS:
            vals = {repr(r["develop"].get(k)) for r in rs}
            (const if len(vals) == 1 else var).append(k)
        print(f"\n## {cat} ({len(rs)} photos)")
        print(f"  CONSTANT: {', '.join(const)}")
        print(f"  VARIABLE: {', '.join(var)}")
        print(f"  {'photo':<10}{'rawYmed':>9}{'expo':>6}{'finYmed':>9}|"
              f"{'asR/G':>7}{'asB/G':>7}{'Temp':>6}{'Tint':>5}|{'finG/R':>8}{'finG/B':>8}")
        for r in rs:
            rw, d, fn = r["raw"], r["develop"], r.get("final") or {}
            print(f"  {r['photo']:<10}{rw['y_median']:>9.4f}{str(d.get('Exposure2012')):>6}"
                  f"{fn.get('y_median',0):>9.4f}|{rw['asshot_rg']:>7.2f}{rw['asshot_bg']:>7.2f}"
                  f"{str(d.get('Temperature')):>6}{str(d.get('Tint')):>5}|"
                  f"{fn.get('gr',0):>8.2f}{fn.get('gb',0):>8.2f}")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = [a for a in argv if not a.startswith("--")]
    base = Path(args[0]).resolve() if args else (
        Path(__file__).resolve().parents[2] / "essais" / "essai independant")
    csv_path = Path(argv[argv.index("--csv") + 1]) if "--csv" in argv else base / "_ground_truth.csv"

    records = analyze_folder(base)
    if not records:
        print(f"Aucun .ARW dans {base}")
        return 1
    print_report(records)
    print_summary(records)
    write_csv(records, csv_path)
    print(f"\nCSV écrit : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
