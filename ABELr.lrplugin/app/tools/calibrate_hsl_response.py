"""H2 du PLAN — sondage `render_probe` pour calibrer `core.response.BandResponse`.

`core.hsl.plan_band` retombe sur des gains nominaux heuristiques
(`_NOM_DCHROMA_DSAT` etc.) tant qu'aucune réponse mesurée n'existe
(`response.save` jamais appelé — cf. PLAN.md étape H2). Ce script sonde la
vraie pente ∂rendu/∂curseur : pour une photo de référence **sélectionnée dans
Lightroom**, il applique des deltas connus de `SaturationAdjustment<Bande>` /
`LuminanceAdjustment<Bande>` / `HueAdjustment<Bande>` (un axe à la fois, autour
de la valeur courante du curseur) via des jobs `render_probe`, mesure le rendu
(`render_metrics_gpu.analyze_rendered_gpu`), fit la pente locale
(`core.response.fit_linear_response`) et sauvegarde le `ResponseModel`
(`response.save`) pour (caméra, profil).

⚠️ **Lr requis** : Lightroom doit être ouvert, le plugin `ABELr` actif
(polling `/jobs/pending`), une photo de référence sélectionnée dans le
catalogue. GPU-strict (`gpu.require_cuda`) : pas de repli CPU.

Ce script démarre lui-même le serveur FastAPI (comme `app.main`, sans le GUI
Qt) pour que le plugin puisse s'y connecter — ne pas lancer en même temps que
`python -m app.main` (conflit de port 5000).

Usage :
    python -m app.tools.calibrate_hsl_response
    python -m app.tools.calibrate_hsl_response --bands Red,Orange --deltas -15,-8,0,8,15
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import exif_profile, gpu, gpu_jpeg, render_metrics, render_metrics_gpu, response  # noqa: E402
from app.core.response import BandResponse, fit_linear_response  # noqa: E402
from app.server.job_queue import job_queue  # noqa: E402
from app.server.models import JobType  # noqa: E402

_AXES = {
    "Saturation": "median_chroma",
    "Luminance": "median_l",
    "Hue": "median_hue",
}
_DEFAULT_DELTAS = (-15.0, -8.0, 0.0, 8.0, 15.0)
_BRIDGE_TIMEOUT_S = 60.0
_JOB_TIMEOUT_S = 30.0


def _start_server() -> None:
    from app import main as app_main

    threading.Thread(target=app_main._run_server, daemon=True, name="fastapi").start()


def _wait_for_bridge(timeout_s: float) -> None:
    print("En attente du pont plugin (Lightroom ouvert, plugin actif) …")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if job_queue.bridge_connected():
            print("Pont connecté.")
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"Pont non connecté après {timeout_s:.0f}s — Lightroom + plugin actif requis."
    )


def _pick_reference_photo(photo_id: str | None) -> dict:
    """Récupère la photo de référence (sélection Lr courante) via `get_selected_photos`."""
    job_id = job_queue.submit(JobType.GET_SELECTED_PHOTOS)
    result = job_queue.wait_result(job_id, _JOB_TIMEOUT_S)
    if result is None or not result.photos:
        raise RuntimeError("Aucune photo sélectionnée dans Lightroom.")
    photos = result.photos
    if photo_id:
        match = next((p for p in photos if p.photo_id == photo_id), None)
        if match is None:
            raise RuntimeError(f"photo_id {photo_id!r} absent de la sélection courante.")
        return match.model_dump()
    if len(photos) > 1:
        print(f"({len(photos)} photos sélectionnées — utilise la première : {photos[0].path})")
    return photos[0].model_dump()


def _probe_once(photo_id: str, develop: dict, settle: float) -> render_metrics.BandStats | None:
    """Sonde un seul réglage temporaire, retourne les stats de bande mesurées (toutes bandes)."""
    job_id = job_queue.submit(
        JobType.RENDER_PROBE, {"adjustments": [{"photo_id": photo_id, "develop": develop}], "settle": settle}
    )
    result = job_queue.wait_result(job_id, _JOB_TIMEOUT_S)
    if result is None or not result.thumbnails:
        print(f"  [!] pas de réponse render_probe pour {develop}")
        return None
    thumb = result.thumbnails[0]
    if thumb.restore_error:
        print(f"  [!] ATTENTION restore échoué : {thumb.restore_error} — photo restée en état sondé.")
    if not thumb.thumbnail_path:
        print(f"  [!] miniature absente (error={thumb.error})")
        return None
    chw = gpu_jpeg.decode_file(thumb.thumbnail_path)
    if chw is None:
        print("  [!] miniature illisible")
        return None
    analysis = render_metrics_gpu.analyze_rendered_gpu(chw)
    return analysis.bands


def _hue_unwrap(hue: float, ref: float) -> float:
    """Teinte ramenée dans (ref−180, ref+180] — mêmes conventions que `hsl._hue_diff`."""
    return ref + ((hue - ref + 180.0) % 360.0 - 180.0)


def calibrate_band_axis(
    photo_id: str, band_name: str, axis: str, current_val: float, deltas: list[float], settle: float
) -> float:
    """Sonde un axe (Saturation/Luminance/Hue) d'une bande, retourne la pente fittée."""
    key = f"{axis}Adjustment{band_name}"
    field = _AXES[axis]
    xs: list[float] = []
    ys: list[float] = []
    ref_hue: float | None = None
    for d in deltas:
        bands = _probe_once(photo_id, {key: current_val + d}, settle)
        if bands is None:
            continue
        band = next((b for b in bands if b.name == band_name), None)
        if band is None or not render_metrics.band_is_reliable(band):
            print(f"  [!] bande {band_name} non fiable/absente pour delta {d:+g} — ignoré")
            continue
        value = getattr(band, field)
        if axis == "Hue":
            if ref_hue is None:
                ref_hue = value
            value = _hue_unwrap(value, ref_hue)
        xs.append(d)
        ys.append(value)
    slope = fit_linear_response(xs, ys)
    print(f"  {key:<28} n={len(xs)}  pente={slope:+.4f}")
    return slope


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--photo-id", default=None, help="photo_id précis (défaut : 1ère photo sélectionnée)")
    ap.add_argument("--bands", default=",".join(render_metrics.BAND_NAMES),
                     help="bandes à sonder, séparées par virgule")
    ap.add_argument("--deltas", default=",".join(str(d) for d in _DEFAULT_DELTAS),
                     help="deltas de curseur à sonder, séparés par virgule")
    ap.add_argument("--settle", type=float, default=0.6, help="délai de rendu Lr (s) entre apply et mesure")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    gpu.require_cuda()  # GPU-strict — pas de repli CPU (CLAUDE.md).

    bands = [b.strip() for b in a.bands.split(",") if b.strip()]
    deltas = [float(d) for d in a.deltas.split(",")]

    _start_server()
    _wait_for_bridge(_BRIDGE_TIMEOUT_S)

    photo = _pick_reference_photo(a.photo_id)
    photo_id = photo["photo_id"]
    current_develop = photo.get("current_develop") or {}
    camera = (photo.get("exif") or {}).get("camera")
    profile = exif_profile.read_capture_profiles([photo["path"]]).get(photo["path"])
    print(f"Photo de référence : {photo['path']}  (caméra={camera!r}, profil={profile!r})\n")

    model = response.load(camera, profile)
    for band_name in bands:
        print(f"Bande {band_name} :")
        for axis in ("Saturation", "Luminance", "Hue"):
            key = f"{axis}Adjustment{band_name}"
            current_val = float(current_develop.get(key) or 0.0)
            slope = calibrate_band_axis(photo_id, band_name, axis, current_val, deltas, a.settle)
            prev = model.bands.get(band_name, BandResponse())
            model.bands[band_name] = BandResponse(
                dchroma_dsat=slope if axis == "Saturation" else prev.dchroma_dsat,
                dl_dlum=slope if axis == "Luminance" else prev.dl_dlum,
                dhue_dhue=slope if axis == "Hue" else prev.dhue_dhue,
            )

    path = response.save(model)
    print(f"\nModèle de réponse sauvegardé : {path}")


if __name__ == "__main__":
    main()
