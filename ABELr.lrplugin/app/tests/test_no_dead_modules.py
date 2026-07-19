"""Étape 1 du PLAN — garde anti-code-mort.

Smoke-import de tous les modules `app/core/*` et `app/gui/*` présents sur disque
(les modules Qt s'importent sans display — seule l'instanciation exige un écran ;
tolérance documentée par le PLAN), et assert que les modules supprimés
(`gui/analysis_worker.py`, `core/seeds.py`, `core/adjustments.py`,
`core/prediction.py`) ne réapparaissent pas.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]

# Modules supprimés (revue Fable 5 / ARCHITECTURE §3) — chemin relatif à app/.
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
        f"{relpath} est censé être supprimé (PLAN étape 1) — ne pas le faire renaître"
    )


def test_analysis_worker_not_importable():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.gui.analysis_worker")
