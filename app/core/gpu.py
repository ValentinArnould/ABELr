"""Contexte CUDA — garde GPU-strict, budget VRAM, pool de streams.

Politique projet (décision utilisateur) : **tout le décodage pixel passe sur GPU,
aucun repli CPU de calcul**. Ce module centralise l'accès CUDA :

- `require_cuda()` lève une erreur **claire** si aucun GPU n'est utilisable (au lieu
  de retomber silencieusement sur le CPU) ;
- `vram_budget_bytes()` expose la VRAM disponible pour le scheduler en vagues
  (`gpu_schedule`) — dimensionne les lots pour ne jamais saturer les 8 Go ;
- `streams()` fournit un pool de `torch.cuda.Stream` pour recouvrir upload H2D et
  calcul ("multithread GPU").

Le seul travail CPU toléré ailleurs (cf. `gpu_raw`) est l'unpack/décompression du
conteneur ARW par LibRaw, **irréductible** : aucun codec GPU n'existe pour l'ARW Sony.
"""

from __future__ import annotations

import threading

import torch


class GpuUnavailable(RuntimeError):
    """Levée quand un GPU CUDA est requis (politique stricte) mais indisponible."""


# Seul le SUCCÈS est mémoïsé (revue Fable 5 C-02) : un échec d'init transitoire
# (OOM au lancement, driver occupé) mémorisé par lru_cache condamnait le process
# jusqu'au redémarrage alors que le GPU était revenu.
_cuda_ok = False


def _diagnose() -> str | None:
    """Retourne un message d'erreur si CUDA est inutilisable, sinon None."""
    global _cuda_ok
    if _cuda_ok:
        return None
    if not torch.cuda.is_available():
        return (
            "torch.cuda.is_available() == False — driver NVIDIA absent, ou build torch "
            "CPU-only. Installez torch CUDA (cu124) : "
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
    """Lève `GpuUnavailable` si pas de GPU utilisable. GPU-strict : aucun repli CPU."""
    err = _diagnose()
    if err:
        raise GpuUnavailable(f"GPU requis (politique GPU-strict) mais indisponible : {err}")


def device() -> torch.device:
    """Device CUDA (après vérification stricte)."""
    require_cuda()
    return torch.device("cuda")


def device_name() -> str:
    require_cuda()
    return torch.cuda.get_device_name(0)


# --------------------------------------------------------------------------- #
# Budget VRAM (pour le scheduler en vagues)
# --------------------------------------------------------------------------- #
def free_total_vram() -> tuple[int, int]:
    """(libre, total) en octets de la VRAM du device courant."""
    require_cuda()
    free, total = torch.cuda.mem_get_info()
    return int(free), int(total)


def vram_budget_bytes(margin: float = 0.75) -> int:
    """VRAM raisonnablement utilisable = libre × marge.

    La marge (<1) réserve de la place au contexte CUDA, à la fragmentation et aux
    tampons intermédiaires du décodage/demosaic — sur 8 Go la marge évite l'OOM dur.
    """
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
