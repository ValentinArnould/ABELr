"""Fenêtre principale PySide6 — base minimale fonctionnelle.

Bouton « Analyser la sélection » -> crée un job get_selected_photos que le plugin
récupère via polling, et affiche les photos retournées.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType
from .job_worker import JobWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lr Automation")
        self.resize(640, 480)

        self._worker: JobWorker | None = None

        self.status_label = QLabel("Prêt. Sélectionnez des photos dans Lightroom.")
        self.analyze_btn = QPushButton("Analyser la sélection")
        self.photo_list = QListWidget()

        self.analyze_btn.clicked.connect(self._on_analyze)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.analyze_btn)
        layout.addWidget(self.photo_list)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    # ------------------------------------------------------------------ #
    def _on_analyze(self) -> None:
        self.analyze_btn.setEnabled(False)
        self.status_label.setText("Requête envoyée — attente du plugin Lr…")
        self.photo_list.clear()

        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_result(self, result: JobResult) -> None:
        self.analyze_btn.setEnabled(True)
        if not result.photos:
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        self.status_label.setText(f"{len(result.photos)} photo(s) reçue(s).")
        for photo in result.photos:
            self.photo_list.addItem(photo.path)

    def _on_failed(self, message: str) -> None:
        self.analyze_btn.setEnabled(True)
        self.status_label.setText(f"Erreur : {message}")
