"""Profil créatif boîtier Sony (Creative Style / Creative Look).

Le profil créatif choisi au boîtier (Standard, IN, SH, FL, VV, VV2, Neutral…) change
fortement le rendu du JPEG embarqué **et** biaise les habitudes d'exposition (on
sous-expose souvent le RAW sous IN/SH pour protéger les hautes lumières du JPEG). Ce
profil n'est **pas** exposé par le SDK Lightroom ni par LibRaw/rawpy — il vit dans le
maker note Sony. On le lit via **exiftool** (binaire externe, déjà requis sur la
machine), hors Lr, directement sur le `.ARW` (comme `raw.read_asshot_wb`).

Tag retenu (prouvé sur ARW ILCE-7M4 réels, plusieurs jeux) : `Sony:CreativeStyle`
(ex. `Standard`, `SH`, `VV2`). Les tags `SR2DataIFD*:ColorMode` sont une table
d'énumération statique (toutes les valeurs possibles) — **pas** la valeur réelle, à
ne pas utiliser.

Extraction par lot (`-@ argfile` implicite via liste de chemins) pour amortir le coût
de lancement du process sur les séries 500-1000.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Tag maker note Sony portant le profil créatif effectif de la prise de vue.
_TAG = "-Sony:CreativeStyle"


def _exiftool_available() -> bool:
    try:
        subprocess.run(
            ["exiftool", "-ver"], capture_output=True, timeout=10, check=False
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def read_capture_profile(path: str | Path) -> str | None:
    """Profil créatif boîtier d'un ARW (ex. "Standard"/"SH"/"VV2"), ou None.

    None si exiftool est absent, le fichier illisible, ou le tag absent. Robuste :
    n'exceptionne jamais (le profil est un enrichissement optionnel du matching).
    """
    result = read_capture_profiles([str(path)])
    return result.get(str(path))


def read_capture_profiles(paths: list[str]) -> dict[str, str | None]:
    """Lit le profil créatif d'un **lot** de RAW en un seul appel exiftool.

    Un seul lancement de process pour tout le lot (le coût de spawn exiftool domine
    sur les grandes séries). Retourne `{chemin: profil|None}` — tout chemin sans tag
    lisible est mappé à None. Ne lève jamais.
    """
    out: dict[str, str | None] = {p: None for p in paths}
    if not paths:
        return out

    # -s3 : valeur brute (sans nom de tag). -T : sortie tabulée. -j : JSON avec
    # SourceFile → mapping fiable même si exiftool réordonne le lot.
    try:
        proc = subprocess.run(
            ["exiftool", "-j", "-s3", _TAG, *paths],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return out

    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        return out

    import json

    try:
        entries = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return out

    for entry in entries:
        src = entry.get("SourceFile")
        style = entry.get("CreativeStyle")
        if src is None:
            continue
        # exiftool renvoie SourceFile en chemin normalisé (slashes) : re-mapper sur
        # la clé d'entrée correspondante (comparaison insensible aux séparateurs).
        key = _match_path(src, paths)
        if key is not None and style:
            out[key] = str(style).strip()
    return out


def _match_path(src: str, paths: list[str]) -> str | None:
    """Retrouve le chemin d'origine correspondant à un `SourceFile` exiftool."""
    norm_src = src.replace("\\", "/").casefold()
    for p in paths:
        if p.replace("\\", "/").casefold() == norm_src:
            return p
    # Repli : comparer sur le nom de fichier seul.
    src_name = Path(src).name.casefold()
    for p in paths:
        if Path(p).name.casefold() == src_name:
            return p
    return None
