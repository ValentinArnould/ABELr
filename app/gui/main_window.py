"""Fenêtre principale PySide6.

Interactions :
- indicateur live du pont plugin (rafraîchi 1s, lit l'état du job_queue) ;
- « Check plugin » : job `test` → popup Hello World côté Lightroom ;
- « Analyser la sélection » : job `get_selected_photos`, puis analyse pixel des
  photos retournées via `AnalysisWorker` (décodage RAW ProPhoto linéaire) ;
- « Calibrer WB » : récupère la sélection, calibre le modèle WB sur les seeds
  (photos à WhiteBalance Custom) via `CalibrateWorker`, détecte le régime et
  planifie les corrections des autres photos ;
- « Appliquer WB au reste » : job `apply_adjustments` poussant les corrections
  planifiées dans Lightroom.

Tout travail bloquant (attente du plugin, décodage/analyse) tourne dans un
QThread dédié pour ne pas geler le GUI.
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
from .calibrate_worker import CalibrateWorker, CalibrationResult
from .job_worker import JobWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lr Automation")
        self.resize(640, 480)

        self._worker: JobWorker | None = None
        self._check_worker: JobWorker | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._calib_worker: CalibrateWorker | None = None
        self._apply_worker: JobWorker | None = None
        # Corrections planifiées par la dernière calibration, en attente d'application.
        self._pending_adjustments: list = []

        self.bridge_label = QLabel()
        self.status_label = QLabel("Prêt. Sélectionnez des photos dans Lightroom.")
        self.check_btn = QPushButton("Check plugin")
        self.analyze_btn = QPushButton("Analyser la sélection")
        self.calibrate_btn = QPushButton("Calibrer WB sur la sélection")
        self.apply_btn = QPushButton("Appliquer WB au reste")
        self.apply_btn.setEnabled(False)
        self.photo_list = QListWidget()

        self.check_btn.clicked.connect(self._on_check)
        self.analyze_btn.clicked.connect(self._on_analyze)
        self.calibrate_btn.clicked.connect(self._on_calibrate)
        self.apply_btn.clicked.connect(self._on_apply)

        layout = QVBoxLayout()
        layout.addWidget(self.bridge_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.check_btn)
        layout.addWidget(self.analyze_btn)
        layout.addWidget(self.calibrate_btn)
        layout.addWidget(self.apply_btn)
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

        # Photos reçues du plugin → analyse pixel (décodage RAW en ProPhoto
        # linéaire) dans un worker dédié pour ne pas geler le GUI.
        self.status_label.setText(
            f"{len(result.photos)} photo(s) reçue(s) — analyse en cours…"
        )
        self._analysis_worker = AnalysisWorker(result.photos)
        self._analysis_worker.photo_done.connect(self._on_photo_analyzed)
        self._analysis_worker.progress.connect(self._on_analysis_progress)
        self._analysis_worker.finished_all.connect(self._on_analysis_done)
        self._analysis_worker.failed.connect(self._on_failed)
        self._analysis_worker.start()

    def _on_failed(self, message: str) -> None:
        self.analyze_btn.setEnabled(True)
        self.status_label.setText(f"Erreur : {message}")

    # ------------------------------------------------------------------ #
    # Calibrage WB par seeds + application
    # ------------------------------------------------------------------ #
    def _on_calibrate(self) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText(
                "Pont inactif — démarrez l'application depuis Lightroom."
            )
            return
        self.calibrate_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self._pending_adjustments = []
        self.photo_list.clear()
        self.status_label.setText("Récupération de la sélection (seeds + à corriger)…")
        # Récupère la sélection ; les seeds = photos à WB Custom dans le lot.
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_calib_photos)
        self._worker.failed.connect(self._on_calib_failed)
        self._worker.start()

    def _on_calib_photos(self, result: JobResult) -> None:
        if not result.photos:
            self.calibrate_btn.setEnabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        self.status_label.setText(
            f"{len(result.photos)} photo(s) — calibrage WB (décodage as-shot)…"
        )
        self._calib_worker = CalibrateWorker(result.photos)
        self._calib_worker.finished_result.connect(self._on_calib_done)
        self._calib_worker.failed.connect(self._on_calib_failed)
        self._calib_worker.start()

    def _on_calib_done(self, res: CalibrationResult) -> None:
        self.calibrate_btn.setEnabled(True)
        cal = res.calibration
        self.photo_list.clear()
        self.photo_list.addItem(
            f"Calibrage : {res.n_seeds} seed(s) | pente {cal.slope_rg:.0f}K/[r/g] "
            f"| intercept {cal.intercept:+.0f}K | Tint {cal.tint:+.0f} "
            f"| Expo {cal.exposure:+.2f}EV"
        )
        self.photo_list.addItem(f"Régime : {res.regime.regime.value} — {res.regime.message}")
        self.photo_list.addItem(f"{res.n_planned} photo(s) à corriger.")
        self._pending_adjustments = res.adjustments
        if res.adjustments:
            self.apply_btn.setEnabled(True)
            self.status_label.setText(
                f"Calibrage OK — {res.n_planned} corrections prêtes. "
                f"« Appliquer WB au reste » pour les appliquer dans Lightroom."
            )
        else:
            self.status_label.setText("Calibrage OK — aucune photo à corriger.")

    def _on_calib_failed(self, message: str) -> None:
        self.calibrate_btn.setEnabled(True)
        self.status_label.setText(f"Calibrage échoué : {message}")

    def _on_apply(self) -> None:
        if not self._pending_adjustments:
            return
        self.apply_btn.setEnabled(False)
        n = len(self._pending_adjustments)
        self.status_label.setText(f"Application de {n} correction(s) dans Lightroom…")
        payload = {"adjustments": [a.model_dump() for a in self._pending_adjustments]}
        self._apply_worker = JobWorker(JobType.APPLY_ADJUSTMENTS, payload, timeout=120.0)
        self._apply_worker.finished_result.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_apply_failed)
        self._apply_worker.start()

    def _on_apply_done(self, result: JobResult) -> None:
        if result.status == "ok":
            self.status_label.setText(
                f"{len(self._pending_adjustments)} correction(s) appliquée(s) — "
                f"vérifiez dans Lightroom, retouchez les exceptions."
            )
            self._pending_adjustments = []
        else:
            self.apply_btn.setEnabled(True)
            self.status_label.setText(f"Application : erreur plugin — {result.error}")

    def _on_apply_failed(self, message: str) -> None:
        self.apply_btn.setEnabled(True)
        self.status_label.setText(f"Application échouée : {message}")

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
        # Métriques en linéaire : Ylin = luminance moyenne (0-1).
        self.photo_list.addItem(
            f"[RAW] {name} — Ylin {pa.mean_luma:.4f} "
            f"(hl {pa.clipped_highlights*100:.1f}% / sh {pa.clipped_shadows*100:.1f}%) "
            f"WB g/r {pa.wb_gain_rg:.2f} g/b {pa.wb_gain_bg:.2f}"
        )

    def _on_analysis_done(self) -> None:
        self.analyze_btn.setEnabled(True)
        n = self.photo_list.count()
        self.status_label.setText(f"Analyse terminée — {n} photo(s).")
