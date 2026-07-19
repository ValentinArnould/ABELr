"""Mock of the Lr plugin — simulates polling to test the App without Lightroom.

Reproduces the Lua plugin's behavior: loops on GET /jobs/pending then
POST /jobs/{id}/result with fake data.

Usage (App already running on :5000):
    python -m app.tools.mock_plugin
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:5000"
POLL_INTERVAL = 0.3

# Fake thumbnails (gray JPEG) written here for get_thumbnails / render_probe.
THUMBS_DIR = Path(tempfile.gettempdir()) / "abelr_mock_thumbs"

FAKE_PHOTOS = [
    {
        "photo_id": "uuid-aaa",
        "path": "C:/temp/DSC00123.ARW",
        "exif": {
            "iso": 800,
            "aperture": 2.8,
            "shutter_speed": "1/200",
            "focal_length": 85,
            "camera": "ILCE-7M4",
        },
        "current_develop": {"Exposure2012": 0.0, "Temperature": 5500, "Tint": 0},
    },
    {
        "photo_id": "uuid-bbb",
        "path": "C:/temp/DSC00124.ARW",
        "exif": {
            "iso": 1600,
            "aperture": 4.0,
            "shutter_speed": "1/125",
            "focal_length": 85,
            "camera": "ILCE-7M4",
        },
        "current_develop": {"Exposure2012": -0.3, "Temperature": 5200, "Tint": 5},
    },
]

# Fake As Shot WB returned by render_probe (like the plugin after apply).
FAKE_ASSHOT = {"temp": 5300.0, "tint": 4.0}

# Fake collection tree (Phase 2 list_collections jobs).
FAKE_COLLECTIONS = {
    "collections": [
        {"name": "Best of 2025", "id": "col-1", "kind": "collection", "photo_count": 12,
         "children": []},
        {"name": "Voyages", "id": "set-1", "kind": "set", "children": [
            {"name": "Japon", "id": "col-2", "kind": "collection", "photo_count": 40,
             "children": []},
        ]},
    ]
}

# Fake develop presets (Phase 2 list_develop_presets jobs).
FAKE_PRESETS = {
    "presets": [
        {"name": "Sony Portrait", "uuid": "preset-aaa", "folder": "User Presets"},
        {"name": "B&W Contrast", "uuid": "preset-bbb", "folder": "User Presets"},
    ]
}


def _batch_ok(job_id: str, applied: int, total: int) -> dict:
    """Standard result for a Phase 2 batch job (set_rating/keywords/preset…)."""
    return {"job_id": job_id, "status": "ok", "photos": [],
            "applied": applied, "total": total}


def _write_gray_jpeg(photo_id: str, level: int) -> str:
    """Writes a solid gray JPEG (fake thumbnail) and returns its absolute path."""
    import cv2
    import numpy as np

    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    path = THUMBS_DIR / f"{photo_id}.jpg"
    img = np.full((256, 384, 3), level, np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def _thumbnails_for(photo_ids: list[str], level: int, asshot: bool = False) -> list[dict]:
    out = []
    for pid in photo_ids:
        entry: dict = {"photo_id": pid, "thumbnail_path": _write_gray_jpeg(pid, level)}
        if asshot:
            entry["asshot_temp"] = FAKE_ASSHOT["temp"]
            entry["asshot_tint"] = FAKE_ASSHOT["tint"]
        out.append(entry)
    return out


def handle(job: dict) -> dict:
    job_id = job["job_id"]
    job_type = job["type"]
    payload = job.get("payload") or {}
    if job_type == "test":
        print("  [mock] test: Hello World popup (simulated)")
        return {"job_id": job_id, "status": "ok", "photos": []}
    if job_type == "get_selected_photos":
        return {"job_id": job_id, "status": "ok", "photos": FAKE_PHOTOS}
    if job_type == "get_thumbnails":
        ids = payload.get("photo_ids") or [p["photo_id"] for p in FAKE_PHOTOS]
        print(f"  [mock] get_thumbnails: {len(ids)} gray thumbnail(s)")
        return {
            "job_id": job_id, "status": "ok", "photos": [],
            "thumbnails": _thumbnails_for(ids, level=120),
        }
    if job_type == "render_probe":
        adjustments = payload.get("adjustments") or []
        ids = [a["photo_id"] for a in adjustments]
        print(
            f"  [mock] render_probe: {len(ids)} simulated neutral render(s) "
            f"(settle={payload.get('settle')})"
        )
        # Gray level slightly different from get_thumbnails: anchor != current render
        # (otherwise the anti-stale-probe guard would rightly trigger).
        return {
            "job_id": job_id, "status": "ok", "photos": [],
            "thumbnails": _thumbnails_for(ids, level=140, asshot=True),
        }
    if job_type == "apply_adjustments":
        print(f"  [mock] apply_adjustments: {payload}")
        n = len(payload.get("adjustments") or [])
        return {
            "job_id": job_id, "status": "ok", "photos": [],
            "applied": n, "matched": n, "total": n,
        }
    # --- Phase 2 ---
    if job_type in ("set_rating", "set_flag_color", "set_keywords",
                    "add_to_collection", "apply_develop_preset"):
        ids = payload.get("photo_ids") or []
        print(f"  [mock] {job_type}: {len(ids)} photo(s) | {payload}")
        return _batch_ok(job_id, applied=len(ids), total=len(ids))
    if job_type == "list_collections":
        print("  [mock] list_collections")
        return {"job_id": job_id, "status": "ok", "photos": [], "data": FAKE_COLLECTIONS}
    if job_type == "create_collection":
        name = payload.get("name")
        print(f"  [mock] create_collection: {name} (parent={payload.get('parent')})")
        return {"job_id": job_id, "status": "ok", "photos": [],
                "data": {"name": name, "id": "col-new", "created": True}}
    if job_type == "list_develop_presets":
        print("  [mock] list_develop_presets")
        return {"job_id": job_id, "status": "ok", "photos": [], "data": FAKE_PRESETS}
    return {"job_id": job_id, "status": "error", "error": f"unknown type: {job_type}"}


def main() -> None:
    print(f"Mock plugin -> {BASE} (Ctrl+C to stop)")
    while True:
        try:
            resp = requests.get(f"{BASE}/jobs/pending", timeout=5)
        except requests.RequestException:
            time.sleep(1.0)
            continue
        if resp.status_code == 204 or not resp.content:
            time.sleep(POLL_INTERVAL)
            continue
        job = resp.json()
        print(f"Job received: {job['type']} ({job['job_id']})")
        result = handle(job)
        requests.post(f"{BASE}/jobs/{job['job_id']}/result", json=result, timeout=5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping mock plugin.")
