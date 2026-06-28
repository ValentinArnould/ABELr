"""Fenêtre principale PySide6 — base minimale fonctionnelle.

Bouton « Analyser la sélection » -> crée un job get_selected_photos que le plugin
récupère via polling, et affiche les photos retournées.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
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
from .analysis_worker import AnalysisWorker, PhotoAnalysis
from .job_worker import JobWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lr Automation")
        self.resize(640, 480)

        self._worker: JobWorker | None = None
        self._check_worker: JobWorker | None = None
        self._analysis_worker: AnalysisWorker | None = None

        self.bridge_label = QLabel()
        self.status_label = QLabel("Prêt. Sélectionnez des photos dans Lightroom.")
        self.check_btn = QPushButton("Check plugin")
        self.analyze_btn = QPushButton("Analyser la sélection")
        self.photo_list = QListWidget()

        self.check_btn.clicked.connect(self._on_check)
        self.analyze_btn.clicked.connect(self._on_analyze)

        layout = QVBoxLayout()
        layout.addWidget(self.bridge_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.check_btn)
        layout.addWidget(self.analyze_btn)
        layout.addWidget(self.photo_list)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        # Indicateur live du pont : rafraîchi toutes les secondes. Lit l'état
        # directement (GUI et serveur FastAPI partagent le même process).
        self._bridge_timer = QTimer(self)
        self._bridge_timer.timeout.connect(self._refresh_bridge)
        self._bridge_timer.start(1000)
        self._refresh_bridge()

    # ------------------------------------------------------------------ #
    def _refresh_bridge(self) -> None:
        if job_queue.bridge_connected():
            since = job_queue.seconds_since_poll() or 0.0
            self.bridge_label.setText(
                f"Pont plugin : ● actif (dernier poll il y a {since:.1f}s)"
            )
        else:
            self.bridge_label.setText(
                "Pont plugin : ○ inactif — dans Lightroom : "
                "Modules externes > Démarrer / connecter l'application"
            )

    # ------------------------------------------------------------------ #
    def _on_analyze(self) -> None:
        self.analyze_btn.setEnabled(False)
        self.status_label.setText("Requête envoyée — attente du plugin Lr…")
        self.photo_list.clear()

        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_check(self) -> None:
        # Court-circuit : si le pont n'a pas pollé récemment, inutile d'envoyer
        # un job — il ne serait jamais récupéré (timeout garanti).
        if not job_queue.bridge_connected():
            self.status_label.setText(
                "Pont inactif — le plugin Lr n'écoute pas. Dans Lightroom : "
                "Modules externes > Démarrer / connecter l'application."
            )
            return

        self.check_btn.setEnabled(False)
        self.status_label.setText("Check plugin — attente du plugin Lr…")

        self._check_worker = JobWorker(JobType.TEST, timeout=10.0)
        self._check_worker.finished_result.connect(self._on_check_result)
        self._check_worker.failed.connect(self._on_check_failed)
        self._check_worker.start()

    def _on_check_result(self, result: JobResult) -> None:
        self.check_btn.setEnabled(True)
        if result.status == "ok":
            self.status_label.setText("Plugin OK — popup affichée dans Lightroom.")
        else:
            self.status_label.setText(f"Plugin a répondu une erreur : {result.error}")

    def _on_check_failed(self, message: str) -> None:
        self.check_btn.setEnabled(True)
        self.status_label.setText(f"Check plugin échoué : {message}")

    def _on_result(self, result: JobResult) -> None:
        if not result.photos:
            self.analyze_btn.setEnabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return

        # Photos reçues du plugin → on enchaîne sur l'analyse pixel (Smart Preview
        # si dispo, sinon RAW) dans un worker dédié pour ne pas geler le GUI.
        catalog_path = result.photos[0].catalog_path
        self.status_label.setText(
            f"{len(result.photos)} photo(s) reçue(s) — analyse en cours…"
        )
        self._analysis_worker = AnalysisWorker(result.photos, catalog_path)
        self._analysis_worker.photo_done.connect(self._on_photo_analyzed)
        self._analysis_worker.progress.connect(self._on_analysis_progress)
        self._analysis_worker.finished_all.connect(self._on_analysis_done)
        self._analysis_worker.failed.connect(self._on_failed)
        self._analysis_worker.start()

    def _on_failed(self, message: str) -> None:
        self.analyze_btn.setEnabled(True)
        self.status_label.setText(f"Erreur : {message}")

    # ------------------------------------------------------------------ #
    # Analyse pixel (Smart Preview / RAW)
    # ------------------------------------------------------------------ #
    def _on_analysis_progress(self, index: int, total: int) -> None:
        self.status_label.setText(f"Analyse {index}/{total}…")

    def _on_photo_analyzed(self, pa: PhotoAnalysis) -> None:
        import os

        name = os.path.basename(pa.path) or pa.photo_id
        if pa.error:
            self.photo_list.addItem(f"⚠ {name} — erreur : {pa.error}")
            return
        tag = "SP" if pa.source == "smart_preview" else "RAW"
        self.photo_list.addItem(
            f"[{tag}] {name} — luma {pa.mean_luma:.0f} "
            f"(hl {pa.clipped_highlights*100:.1f}% / sh {pa.clipped_shadows*100:.1f}%) "
            f"WB r/g {pa.wb_gain_rg:.2f} b/g {pa.wb_gain_bg:.2f}"
        )

    def _on_analysis_done(self) -> None:
        self.analyze_btn.setEnabled(True)
        n = self.photo_list.count()
        self.status_label.setText(f"Analyse terminée — {n} photo(s).")
