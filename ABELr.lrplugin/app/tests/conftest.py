"""Config pytest — filet de tests des fonctions **pures** du cœur (sans GPU ni RAW).

Ces tests verrouillent les invariants numériques dont dépend toute la justesse du
pipeline (colorimétrie, stabilité des clés de cache, agrégation k-NN, réponse
calibrée). Ils tournent en quelques secondes, sans CUDA ni fichier `.ARW` :
- lancer depuis la racine du projet : `pytest app/tests -q`
- les tests marqués `@pytest.mark.gpu` sont **skippés** si torch/CUDA est absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# La racine du projet (parent du paquet `app`) doit être sur sys.path pour
# `from app.core import ...` quel que soit le cwd de lancement.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "gpu: nécessite torch + CUDA (skippé si absent)"
    )


@pytest.fixture(scope="session")
def cuda_or_skip():
    """Skip le test si torch/CUDA n'est pas disponible (parité GPU/CPU)."""
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA indisponible")
    return torch
