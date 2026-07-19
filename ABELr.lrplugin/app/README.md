# App externe — ABELr

Serveur HTTP (FastAPI, localhost:5000) + GUI (PySide6). Le plugin Lr est client :
il interroge l'App en polling.

Ce dossier (`app/`) vit **dans** `ABELr.lrplugin/` — le plugin est auto-suffisant, il
embarque tout le code Python. Toutes les commandes ci-dessous se lancent depuis
`ABELr.lrplugin/` (le dossier du plugin), pas depuis la racine du repo.

## Install

**Automatique (recommandé)** : le plugin construit son venv tout seul au 1er lancement
(`launch.ps1` → `bootstrap.ps1`, déclenché par le menu Lr *Démarrer/connecter l'application*).
Détecte un GPU NVIDIA (`nvidia-smi`) et installe torch CUDA (cu124) si présent, sinon torch CPU.
Nécessite Python 3.11+ sur le PATH + internet (1ère installation seulement).

**Manuelle** (dev) :
```bash
cd ABELr.lrplugin
python -m venv app\.venv
app\.venv\Scripts\activate        # Windows
pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124  # GPU
# ou : pip install torch==2.6.0 torchvision==0.21.0                                                # CPU
pip install -r app\requirements.txt
```

## Lancer

```bash
python -m app.main            # depuis ABELr.lrplugin/
```

Démarre le serveur FastAPI (thread daemon) + la fenêtre GUI. Le device (GPU si CUDA
utilisable, sinon CPU) est décidé automatiquement par `core/gpu.py` — aucun réglage requis.

## Tester sans Lightroom

Avec l'App lancée, simuler le plugin dans un autre terminal :

```bash
python -m app.tools.mock_plugin
```

Cliquer « Analyser la sélection » dans le GUI → le mock renvoie des photos factices.

## Vérifier le serveur seul

```bash
curl http://localhost:5000/health
curl http://localhost:5000/status
```

## Structure

| Dossier | Rôle |
|---|---|
| `server/` | FastAPI (`api.py`), queue de jobs thread-safe (`job_queue.py`), modèles Pydantic (`models.py`) |
| `gui/` | Fenêtre PySide6 (`main_window.py`), workers Qt non-bloquants (`job_worker.py` = attente plugin, `autocorrect_worker.py` = mesure+plan, `neutral_preview_worker.py` = ancres neutres) |
| `core/` | Pipeline image et analyse (voir ci-dessous) |
| `tools/` | Mock plugin pour dev sans Lr |

### `core/` — pipeline image

| Fichier | Rôle |
|---|---|
| `color.py` | Espaces couleur de l'analyse : ProPhoto linéaire, luminance Y (XYZ), conversion → sRGB pour l'affichage |
| `raw.py` | Décodage RAW Sony ARW via rawpy : `load_linear` (ProPhoto linéaire, analyse) / `load_rgb` (sRGB uint8, GUI) |
| `image_source.py` | Source pixel de l'analyse : **RAW → ProPhoto linéaire** (`LoadedImage`) |
| `analysis.py` | Métriques exposition (luminance Y) + balance des blancs (gray-world), en linéaire |
| `catalog.py` | Localise `.lrcat` + bundles `.lrdata` ; ouvre les SQLite en lecture seule (cohabite avec Lr ouvert) |
| `previews.py` | Résout `id_global` → fichiers de preview ; aperçu rendu (vérif résultat). **Smart Preview = inspection seulement** |
| `adjustments.py` / `prediction.py` | Calcul des corrections / lissage série — en cours |

> **Pourquoi le RAW et pas la Smart Preview ?** La calibration (`tools/calibrate_sp_vs_raw.py`)
> a montré que la Smart Preview est du **raw caméra-natif** (avant WB et matrice
> couleur), que LibRaw ne décode pas et qu'un dev fait main ne ramène pas
> fidèlement au RAW. Le RAW via rawpy est la seule source juste et cohérente.
> Format d'analyse : **float32 ProPhoto linéaire** (gamut large = WB non biaisée),
> luminance via Y de XYZ ; sRGB réservé à l'affichage.

## Flux job

```
GUI submit() -> JobQueue.pending
plugin GET /jobs/pending      -> récupère le job
plugin POST /jobs/{id}/result -> JobQueue.submit_result() débloque le worker GUI
```
