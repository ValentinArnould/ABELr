"""PLAN step 1 — anti-dead-code guard.

Smoke-imports all `app/core/*` and `app/gui/*` modules present on disk (Qt modules
import without a display — only instantiation requires a screen; tolerance
documented by the PLAN), and asserts that removed modules (`gui/analysis_worker.py`,
`core/seeds.py`, `core/adjustments.py`, `core/prediction.py`) do not reappear.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]

# Removed modules (Fable 5 review / ARCHITECTURE §3) — path relative to app/.
DELETED = [
    "gui/analysis_worker.py",
    "core/seeds.py",
    "core/adjustments.py",
    "core/prediction.py",
]


def _modules(subpkg: str) -> list[str]:
    return sorted(
        f"app.{subpkg}.{p.stem}"
        for p in (APP_DIR / subpkg).glob("*.py")
        if p.stem != "__init__"
    )


@pytest.mark.parametrize("module", _modules("core") + _modules("gui"))
def test_smoke_import(module):
    importlib.import_module(module)


@pytest.mark.parametrize("relpath", DELETED)
def test_deleted_modules_stay_deleted(relpath):
    assert not (APP_DIR / relpath).exists(), (
        f"{relpath} is supposed to be removed (PLAN step 1) — do not bring it back"
    )


def test_analysis_worker_not_importable():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.gui.analysis_worker")
