"""Contexte GPU/CPU — sélection de device, budget VRAM, pool de streams.

Politique projet (mise à jour — décision utilisateur) : **GPU prioritaire, fallback
CPU si aucun GPU CUDA utilisable**. Historiquement ce module imposait un GPU-strict
(`require_cuda()` levait plutôt que de retomber sur le CPU) ; l'objectif de faire
tourner le plugin sur des machines sans NVIDIA a fait lever cette contrainte. Ce
module centralise la décision :

- `device()` renvoie `cuda` si disponible, sinon `cpu` — **jamais d'exception** ;
  tout le reste du pipeline (`gpu_raw`, `gpu_jpeg`, `render_metrics_gpu`,
  `gpu_schedule`) route son device par cet appel, donc bascule automatiquement.
- `require_cuda()` reste disponible pour les usages qui veulent explicitement
  exiger CUDA (outils de calibration/parité GPU) — ce n'est plus le chemin par défaut.
- `vram_budget_bytes()` expose la VRAM disponible pour le scheduler en vagues
  (`gpu_schedule`) si GPU, ou un plafond RAM fixe conservateur si CPU.
- `streams()` fournit un pool de `torch.cuda.Stream` (GPU uniquement) pour recouvrir
  upload H2D et calcul ("multithread GPU").

Le seul travail CPU **irréductible** même en présence d'un GPU (cf. `gpu_raw`) est
l'unpack/décompression du conteneur ARW par LibRaw : aucun codec GPU n'existe pour
l'ARW Sony.
"""

from __future__ import annotations

import threading

import torch


class GpuUnavailable(RuntimeError):
    """Levée par `require_cuda()` quand un GPU CUDA est explicitement exigé mais absent."""


# Seul le SUCCÈS est mémoïsé (revue Fable 5 C-02) : un échec d'init transitoire
# (OOM au lancement, driver occupé) mémorisé par lru_cache condamnait le process
# jusqu'au redémarrage alors que le GPU était revenu.
_cuda_ok = False

# Budget RAM hôte conservateur utilisé par le scheduler quand on tourne en CPU
# (pas de VRAM à interroger). Volontairement prudent : le CPU est déjà le chemin
# lent, pas la peine de risquer un swap en visant trop grand.
_CPU_BUDGET_BYTES = 2_000_000_000


def _diagnose() -> str | None:
    """Retourne un message d'erreur si CUDA est inutilisable, sinon None."""
    global _cuda_ok
    if _cuda_ok:
        return None
    if not torch.cuda.is_available():
        return (
            "torch.cuda.is_available() == False — driver NVIDIA absent, ou build torch "
            "CPU-only. Installez torch CUDA (cu124) pour activer le GPU : "
            "pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124"
        )
    try:
        torch.zeros(1, device="cuda")  # force l'init du contexte CUDA
    except Exception as exc:  # contexte cassé, OOM au démarrage, etc.
        return f"initialisation du contexte CUDA échouée : {exc}"
    _cuda_ok = True
    return None


def is_available() -> bool:
    """True si un GPU CUDA utilisable est présent (sans lever)."""
    return _diagnose() is None


def require_cuda() -> None:
    """Lève `GpuUnavailable` si pas de GPU utilisable.

    Réservé aux usages qui veulent explicitement exiger CUDA (calibration, tests de
    parité) — le pipeline normal utilise `device()`, qui ne lève jamais.
    """
    err = _diagnose()
    if err:
        raise GpuUnavailable(f"GPU CUDA requis mais indisponible : {err}")


def device() -> torch.device:
    """Device de calcul : `cuda` si utilisable, sinon `cpu` (jamais d'exception)."""
    return torch.device("cuda") if is_available() else torch.device("cpu")


def device_name() -> str:
    """Nom du device courant — nom GPU si CUDA, sinon repère explicite CPU."""
    if is_available():
        return torch.cuda.get_device_name(0)
    return "CPU (fallback — pas de GPU CUDA détecté)"


# --------------------------------------------------------------------------- #
# Budget VRAM/RAM (pour le scheduler en vagues)
# --------------------------------------------------------------------------- #
def free_total_vram() -> tuple[int, int]:
    """(libre, total) en octets de la VRAM du device courant.

    Exige CUDA (pas de notion de VRAM en CPU) — le scheduler doit passer par
    `vram_budget_bytes()`, qui gère la branche CPU.
    """
    require_cuda()
    free, total = torch.cuda.mem_get_info()
    return int(free), int(total)


def vram_budget_bytes(margin: float = 0.75) -> int:
    """VRAM raisonnablement utilisable = libre × marge ; plafond RAM fixe si CPU.

    La marge (<1) réserve de la place au contexte CUDA, à la fragmentation et aux
    tampons intermédiaires du décodage/demosaic — sur 8 Go la marge évite l'OOM dur.
    En CPU, pas de VRAM à mesurer : `_CPU_BUDGET_BYTES` fixe borne les vagues du
    scheduler à une taille raisonnable.
    """
    if not is_available():
        return _CPU_BUDGET_BYTES
    free, _ = free_total_vram()
    return int(free * margin)


def empty_cache() -> None:
    """Rend la VRAM mise en cache par l'allocateur torch à la fin d'une vague."""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# --------------------------------------------------------------------------- #
# Pool de CUDA streams (overlap upload/compute)
# --------------------------------------------------------------------------- #
_streams_lock = threading.Lock()
_streams: list[torch.cuda.Stream] = []


def streams(n: int = 2) -> list[torch.cuda.Stream]:
    """Pool partagé de `n` CUDA streams (créés paresseusement, réutilisés)."""
    require_cuda()
    with _streams_lock:
        while len(_streams) < n:
            _streams.append(torch.cuda.Stream())
        return _streams[:n]


def synchronize() -> None:
    """Attend la fin de tous les kernels en vol sur le device."""
    if torch.cuda.is_available():
        torch.cuda.synchronize()
