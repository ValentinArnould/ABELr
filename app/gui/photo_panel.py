"""Panneau sélection / aperçu photos — stub réservé.

Intégrera l'aperçu (miniatures via core.raw.load_thumbnail) et la liste détaillée.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class PhotoPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
