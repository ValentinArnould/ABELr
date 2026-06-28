# Lr_automation — Vue d'ensemble du projet

## Description

Lr_automation est un système de retouche photo intelligente pour Adobe Lightroom Classic.
Il couple un plugin Lightroom (Lua) à une application Python externe dotée d'une interface graphique.

L'application analyse les fichiers RAW Sony (format ARW), détermine les ajustements optimaux,
et les applique automatiquement dans Lightroom — sans intervention manuelle photo par photo.

---

## Problème résolu

La retouche manuelle d'une série de 500 à 1000 photos est longue et incohérente.
L'exposition, la balance des blancs et l'étalonnage des couleurs varient d'une prise à l'autre
selon les conditions lumineuses, et les corriger à la main produit des résultats inégaux.

Lr_automation analyse l'ensemble de la série, construit une carte de prédiction des ajustements,
et applique des corrections cohérentes et précises sur toutes les photos en batch.

---

## Architecture globale

```
┌─────────────────────────────────┐      HTTP JSON       ┌──────────────────────────────────────┐
│       Plugin Lightroom          │ ◄──── polling ──────► │         Application Python           │
│         (Lua, SDK Lr)           │      localhost:5000   │   FastAPI + PySide6 (Qt6) GUI        │
│                                 │                       │                                      │
│  • Lit les données catalog Lr   │                       │  • Interface graphique utilisateur   │
│  • Applique les ajustements     │                       │  • Décodage RAW Sony (rawpy/LibRaw)  │
│  • Boucle polling 300ms         │                       │  • Analyse image (numpy, OpenCV)     │
└─────────────────────────────────┘                       │  • Calcul ajustements                │
              │                                           │  • Carte de prédiction (scikit-learn)│
              │ SDK Lr                                    └──────────────────────────────────────┘
              ▼
┌─────────────────────────────────┐
│      Lightroom Classic 12+      │
│                                 │
│  • Catalog photos               │
│  • Develop settings             │
│  • Métadonnées / EXIF           │
└─────────────────────────────────┘
```

### Principe de communication

Le plugin Lua est toujours **client HTTP**. L'App Python est toujours **serveur HTTP**.

Le plugin tourne une boucle de polling toutes les 300 ms (`LrTasks`).
Quand l'App a besoin de données Lightroom, elle crée un *job* dans sa file d'attente interne.
Le plugin récupère ce job, l'exécute via le SDK Lr, et retourne le résultat à l'App.
Les ajustements calculés par l'App sont eux aussi transmis au plugin via un job.

---

## Fonctionnalités

### Équilibrage batch de l'exposition
Analyse les histogrammes de luminance de chaque photo.
Calcule un delta d'exposition pour ramener chaque image à une luminosité cible cohérente.
Prend en compte les paramètres EXIF (ISO, ouverture, vitesse) pour pondérer la correction.

### Équilibrage batch de la balance des blancs
Analyse les zones neutres et la température de couleur effective de chaque RAW.
Calcule les valeurs Température et Teinte Lr pour uniformiser la série.

### Harmonisation de l'étalonnage des couleurs
Analyse les teintes dominantes, saturation et luminosité par canal (HSL).
Harmonise l'étalonnage (Color Grading) sur l'ensemble de la série.

### Carte de prédiction (séries 500-1000 photos)
Sur une grande série, construit un modèle de variation des conditions lumineuses.
Prédit les ajustements nécessaires pour les photos intermédiaires.
Permet une correction progressive et naturelle sur toute la durée d'une session de prise de vue.

---

## Stack technique

| Composant | Technologie | Rôle |
|---|---|---|
| Plugin Lr | Lua 5.1 + SDK Lr Classic 12+ | Bridge vers Lightroom |
| Serveur App | Python 3.11+ + FastAPI | API HTTP localhost |
| GUI | PySide6 (Qt6) | Interface utilisateur |
| Décodage RAW | rawpy (LibRaw) | Lecture fichiers ARW Sony |
| Analyse image | numpy + OpenCV | Histogrammes, analyse couleur |
| Calcul ajustements | scipy | Optimisation numérique |
| Carte prédiction | scikit-learn | Modèle sur séries photo |
| Accélération | Rust via PyO3 (optionnel) | Si bottleneck algo custom identifié |

---

## Structure des fichiers

```
Lr_automation/
├── CLAUDE.md                      # Référence technique pour le développement
├── documentation/
│   └── project_overview.md        # Ce fichier
│
├── plugin/                        # Plugin Lightroom (Lua)
│   ├── Info.lua                   # Manifeste plugin (obligatoire)
│   ├── Menu.lua                   # Entrées menu Lightroom
│   └── lib/
│       ├── PollingLoop.lua        # Boucle LrTasks 300ms
│       ├── HttpClient.lua         # Requêtes HTTP (LrHttp)
│       ├── Adjustments.lua        # Application ajustements SDK
│       ├── PhotoData.lua          # Lecture données photos
│       └── Utils.lua              # Helpers, JSON
│
└── app/                           # Application Python
    ├── main.py                    # Point d'entrée (GUI + serveur)
    ├── server/
    │   ├── api.py                 # Routes FastAPI
    │   └── job_queue.py           # File de jobs thread-safe
    ├── gui/
    │   ├── main_window.py         # Fenêtre principale
    │   ├── photo_panel.py         # Panneau photos
    │   └── analysis_panel.py      # Visualisation analyse
    ├── core/
    │   ├── raw.py                 # Décodage RAW Sony
    │   ├── analysis.py            # Analyse exposition / WB / couleurs
    │   ├── prediction.py          # Modèle carte de prédiction
    │   └── adjustments.py         # Calcul corrections finales
    ├── rust_ext/                  # (optionnel) Extensions Rust/PyO3
    └── requirements.txt
```

---

## Flux d'utilisation typique

```
1. Utilisateur ouvre Lightroom, sélectionne une série de photos
2. Utilisateur lance App Lr_automation (python app/main.py)
3. Plugin détecte l'App (polling /health)
4. Utilisateur clique "Analyser la sélection" dans l'App
5. App crée job "get_selected_photos"
6. Plugin récupère job → lit paths + EXIF + develop settings via SDK Lr
7. Plugin retourne les données à l'App
8. App décode chaque ARW (rawpy), analyse histogrammes et couleurs
9. App calcule ajustements et génère carte de prédiction
10. App affiche aperçu des corrections dans la GUI
11. Utilisateur valide
12. App crée job "apply_adjustments" avec toutes les corrections
13. Plugin récupère job → applique batch dans Lr (withWriteAccessDo)
14. Photos corrigées dans Lightroom
```

---

## Décisions d'architecture

| Décision | Choix retenu | Raison |
|---|---|---|
| Communication plugin ↔ App | HTTP JSON polling | Plugin ne peut pas exposer de serveur facilement ; LrHttp disponible en client |
| Langage App | Python (pas Rust natif) | Ecosystem image mature (rawpy, OpenCV, scikit-learn) ; Rust n'a pas d'équivalent |
| Rust | PyO3 optionnel, différé | rawpy/OpenCV/numpy sont déjà du C/C++ ; profiler avant d'optimiser |
| GUI | PySide6 (Qt6) | UI riche impossible avec les dialogs Lr natifs |
| Serveur App | FastAPI | Suffisant pour localhost ; async natif compatible PySide6 |
| Version Lr | 12+ (2023+) | LrHttp stable, SDK mature, pas de contrainte rétrocompatibilité |

---

## Prérequis

- Adobe Lightroom Classic 12+
- Python 3.11+
- Dépendances Python : voir `app/requirements.txt`
- Fichiers RAW au format Sony ARW (ILCE-7M4 et compatibles LibRaw)
