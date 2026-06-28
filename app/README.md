# App externe — Lr_automation

Serveur HTTP (FastAPI, localhost:5000) + GUI (PySide6). Le plugin Lr est client :
il interroge l'App en polling.

## Install

```bash
cd app
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

## Lancer

```bash
python -m app.main            # depuis Lr_automation/
```

Démarre le serveur FastAPI (thread daemon) + la fenêtre GUI.

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
| `server/` | FastAPI (`api.py`), queue de jobs thread-safe (`job_queue.py`), modèles (`models.py`) |
| `gui/` | Fenêtre PySide6 + worker Qt non-bloquant |
| `core/` | Décodage RAW, analyse, ajustements, prédiction |
| `tools/` | Mock plugin pour dev sans Lr |

## Flux job

```
GUI submit() -> JobQueue.pending
plugin GET /jobs/pending      -> récupère le job
plugin POST /jobs/{id}/result -> JobQueue.submit_result() débloque le worker GUI
```
