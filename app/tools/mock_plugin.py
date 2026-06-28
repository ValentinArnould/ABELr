"""Mock du plugin Lr — simule le polling pour tester l'App sans Lightroom.

Reproduit le comportement du plugin Lua : boucle GET /jobs/pending puis
POST /jobs/{id}/result avec des données factices.

Usage (App déjà lancée sur :5000) :
    python -m app.tools.mock_plugin
"""

from __future__ import annotations

import time

import requests

BASE = "http://127.0.0.1:5000"
POLL_INTERVAL = 0.3

FAKE_PHOTOS = [
    {
        "photo_id": "uuid-aaa",
        "path": "C:/photos sony/DSC00123.ARW",
        "exif": {
            "iso": 800,
            "aperture": 2.8,
            "shutter_speed": "1/200",
            "focal_length": 85,
            "camera": "ILCE-7M4",
        },
        "current_develop": {"Exposure": 0.0, "Temperature": 5500, "Tint": 0},
    },
    {
        "photo_id": "uuid-bbb",
        "path": "C:/photos sony/DSC00124.ARW",
        "exif": {
            "iso": 1600,
            "aperture": 4.0,
            "shutter_speed": "1/125",
            "focal_length": 85,
            "camera": "ILCE-7M4",
        },
        "current_develop": {"Exposure": -0.3, "Temperature": 5200, "Tint": 5},
    },
]


def handle(job: dict) -> dict:
    job_id = job["job_id"]
    job_type = job["type"]
    if job_type == "test":
        print("  [mock] test: popup Hello World (simulée)")
        return {"job_id": job_id, "status": "ok", "photos": []}
    if job_type == "get_selected_photos":
        return {"job_id": job_id, "status": "ok", "photos": FAKE_PHOTOS}
    if job_type == "apply_adjustments":
        print(f"  [mock] apply_adjustments: {job.get('payload')}")
        return {"job_id": job_id, "status": "ok", "photos": []}
    return {"job_id": job_id, "status": "error", "error": f"type inconnu: {job_type}"}


def main() -> None:
    print(f"Mock plugin -> {BASE} (Ctrl+C pour arrêter)")
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
        print(f"Job reçu: {job['type']} ({job['job_id']})")
        result = handle(job)
        requests.post(f"{BASE}/jobs/{job['job_id']}/result", json=result, timeout=5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêt mock plugin.")
