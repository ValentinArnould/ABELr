"""Fenêtre principale PySide6.

Boutons câblés (tout travail bloquant — attente plugin, décodage RAW — tourne dans
un QThread pour ne pas geler le GUI) :
- « Test » : job `test` → popup Hello World côté Lightroom ;
- « Analyse Sélection » : job `get_selected_photos` puis analyse pixel
  (`AnalysisWorker`, décodage RAW ProPhoto linéaire) ;
- « Analyse Catalogue » : job `get_catalog_photos` → index métadonnées (EXIF +
  develop) de toutes les photos, sans décodage pixel ;
- « Calibrate WB / Expo » : récupère la sélection, apprend le réglage choisi par
  le photographe sur les seeds (WhiteBalance Custom) et l'affiche — n'applique rien ;
- « Apply WB / Expo » : autonome — récupère la sélection, calibre sur les seeds,
  calcule les corrections et les applique via job `apply_adjustments`.

WB et exposition sont indépendantes (boutons séparés) : Apply WB ne touche pas
l'exposition et inversement. La checkbox « Écraser seeds » étend la cible Apply à
toute la sélection (seeds compris) au lieu des seules non-seeds.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import exposure, seeds
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
        # Capturé au clic Apply : écrase aussi les seeds (cible = toute la sélection).
        self._wb_force = False
        self._exp_force = False

        self.bridge_label = QLabel()
        self.status_label = QLabel("Prêt. Sélectionnez des photos dans Lightroom.")

        # Ligne outils : ping plugin + analyses.
        self.test_btn = QPushButton("Test")
        self.analyze_catalog_btn = QPushButton("Analyse Catalogue")
        self.analyze_btn = QPushButton("Analyse Sélection")

        # Calibrage : Expo + WB. La sélection courante = seeds.
        self.calibrate_exp_btn = QPushButton("Calibrate Expo")
        self.calibrate_wb_btn = QPushButton("Calibrate WB")

        # Application : Expo + WB, sur la sélection cible.
        self.apply_exp_btn = QPushButton("Apply Expo")
        self.apply_wb_btn = QPushButton("Apply WB")
        # force Apply = écrase aussi les photos qui sont des seeds.
        self.apply_force_cb = QCheckBox("Écraser seeds")
        self.apply_force_cb.setToolTip(
            "Décoché : ne modifie pas les photos qui sont des seeds.\n"
            "Coché : applique sur toute la sélection, écrase les seeds."
        )

        self.photo_list = QListWidget()

        self.test_btn.clicked.connect(self._on_check)
        self.analyze_catalog_btn.clicked.connect(self._on_analyze_catalog)
        self.analyze_btn.clicked.connect(self._on_analyze)
        self.calibrate_exp_btn.clicked.connect(self._on_calibrate_exposure)
        self.calibrate_wb_btn.clicked.connect(self._on_calibrate)
        self.apply_exp_btn.clicked.connect(self._on_apply_exposure)
        self.apply_wb_btn.clicked.connect(self._on_apply)

        layout = QVBoxLayout()
        layout.addWidget(self.bridge_label)
        layout.addWidget(self.status_label)

        # Ligne 1 : outils / analyses.
        tools_row = QHBoxLayout()
        tools_row.addWidget(self.test_btn)
        tools_row.addWidget(self.analyze_catalog_btn)
        tools_row.addWidget(self.analyze_btn)
        tools_row.addStretch()
        layout.addLayout(tools_row)

        # Ligne 2 : Calibrate (2 boutons, informatif).
        calib_row = QHBoxLayout()
        calib_row.addWidget(self.calibrate_exp_btn)
        calib_row.addWidget(self.calibrate_wb_btn)
        calib_row.addStretch()
        layout.addLayout(calib_row)

        # Ligne 3 : Apply (2 boutons) + checkbox force apply à côté.
        apply_row = QHBoxLayout()
        apply_row.addWidget(self.apply_exp_btn)
        apply_row.addWidget(self.apply_wb_btn)
        apply_row.addWidget(self.apply_force_cb)
        apply_row.addStretch()
        layout.addLayout(apply_row)

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

        self.test_btn.setEnabled(False)
        self.status_label.setText("Check plugin — attente du plugin Lr…")

        self._check_worker = JobWorker(JobType.TEST, timeout=10.0)
        self._check_worker.finished_result.connect(self._on_check_result)
        self._check_worker.failed.connect(self._on_check_failed)
        self._check_worker.start()

    def _on_check_result(self, result: JobResult) -> None:
        self.test_btn.setEnabled(True)
        if result.status == "ok":
            self.status_label.setText("Plugin OK — popup affichée dans Lightroom.")
        else:
            self.status_label.setText(f"Plugin a répondu une erreur : {result.error}")

    def _on_check_failed(self, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.status_label.setText(f"Check plugin échoué : {message}")

    # ------------------------------------------------------------------ #
    # Analyse Catalogue — index métadonnées de toutes les photos (pas de pixels)
    # ------------------------------------------------------------------ #
    def _on_analyze_catalog(self) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText(
                "Pont inactif — démarrez l'application depuis Lightroom."
            )
            return
        self.analyze_catalog_btn.setEnabled(False)
        self.photo_list.clear()
        self.status_label.setText("Récupération du catalogue (toutes les photos)…")
        # Métadonnées seules (EXIF + develop), pas de décodage RAW → peut être long
        # à transiter sur un gros catalogue mais reste léger côté plugin.
        self._worker = JobWorker(JobType.GET_CATALOG_PHOTOS, timeout=120.0)
        self._worker.finished_result.connect(self._on_catalog_result)
        self._worker.failed.connect(self._on_catalog_failed)
        self._worker.start()

    def _on_catalog_result(self, result: JobResult) -> None:
        self.analyze_catalog_btn.setEnabled(True)
        photos = result.photos
        if not photos:
            self.status_label.setText("Catalogue vide ou aucune photo retournée.")
            return
        seed_photos = [p for p in photos if seeds.is_seed(p.current_develop or {})]
        cameras: dict[str, int] = {}
        for p in photos:
            cam = p.exif.camera or "?"
            cameras[cam] = cameras.get(cam, 0) + 1
        self.photo_list.clear()
        self.photo_list.addItem(
            f"Catalogue : {len(photos)} photo(s) — {len(seed_photos)} seed(s) (WB Custom)."
        )
        for cam, n in sorted(cameras.items(), key=lambda kv: -kv[1]):
            self.photo_list.addItem(f"  {cam} : {n} photo(s)")
        self.status_label.setText(
            f"Catalogue indexé — {len(photos)} photo(s), {len(seed_photos)} seed(s)."
        )

    def _on_catalog_failed(self, message: str) -> None:
        self.analyze_catalog_btn.setEnabled(True)
        self.status_label.setText(f"Analyse Catalogue échouée : {message}")

    # ------------------------------------------------------------------ #
    # Calibrate Expo — apprend l'exposition choisie par le photographe (seeds)
    # ------------------------------------------------------------------ #
    def _on_calibrate_exposure(self) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText(
                "Pont inactif — démarrez l'application depuis Lightroom."
            )
            return
        self.calibrate_exp_btn.setEnabled(False)
        self.photo_list.clear()
        self.status_label.setText("Récupération de la sélection (calibrage expo)…")
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_calib_exp_photos)
        self._worker.failed.connect(self._on_calib_exp_failed)
        self._worker.start()

    def _on_calib_exp_photos(self, result: JobResult) -> None:
        self.calibrate_exp_btn.setEnabled(True)
        if not result.photos:
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        # Calcul instantané (médiane des Exposure2012 des seeds, pas de décodage RAW).
        exposures, others = exposure.collect_exposures(result.photos)
        try:
            cal = exposure.calibrate(exposures)
        except ValueError as exc:
            self.status_label.setText(f"Calibrage expo : {exc}")
            return
        self.photo_list.clear()
        self.photo_list.addItem(
            f"Expo calibrée : {cal.exposure:+.2f} EV "
            f"(médiane de {cal.n_seeds} seed(s), σ {cal.spread_ev:.2f} EV)."
        )
        self.photo_list.addItem(f"{len(others)} photo(s) sans réglage manuel.")
        self.status_label.setText(
            f"Calibrage expo OK — {cal.exposure:+.2f} EV sur {cal.n_seeds} seed(s). "
            f"« Apply Expo » pour l'appliquer."
        )

    def _on_calib_exp_failed(self, message: str) -> None:
        self.calibrate_exp_btn.setEnabled(True)
        self.status_label.setText(f"Calibrate Expo échoué : {message}")

    # ------------------------------------------------------------------ #
    # Apply Expo — calibre puis applique l'exposition sur la sélection cible
    # ------------------------------------------------------------------ #
    def _on_apply_exposure(self) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText(
                "Pont inactif — démarrez l'application depuis Lightroom."
            )
            return
        self.apply_exp_btn.setEnabled(False)
        self.photo_list.clear()
        self._exp_force = self.apply_force_cb.isChecked()
        self.status_label.setText("Récupération de la sélection (apply expo)…")
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_apply_exp_photos)
        self._worker.failed.connect(self._on_apply_exp_failed)
        self._worker.start()

    def _on_apply_exp_photos(self, result: JobResult) -> None:
        if not result.photos:
            self.apply_exp_btn.setEnabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        exposures, others = exposure.collect_exposures(result.photos)
        try:
            cal = exposure.calibrate(exposures)
        except ValueError as exc:
            self.apply_exp_btn.setEnabled(True)
            self.status_label.setText(f"Calibrage expo : {exc}")
            return
        # force : réécrire toute la sélection (seeds inclus) ; sinon hors seeds.
        targets = result.photos if self._exp_force else others
        adjustments = exposure.plan_adjustments(targets, cal)
        if not adjustments:
            self.apply_exp_btn.setEnabled(True)
            self.status_label.setText(
                "Aucune photo à corriger (toute la sélection est seed ?)."
            )
            return
        mode = "toute la sélection" if self._exp_force else "hors seeds"
        self.status_label.setText(
            f"Application expo {cal.exposure:+.2f}EV sur {len(adjustments)} "
            f"photo(s) ({mode})…"
        )
        payload = {"adjustments": [a.model_dump() for a in adjustments]}
        self._apply_worker = JobWorker(JobType.APPLY_ADJUSTMENTS, payload, timeout=120.0)
        self._apply_worker.finished_result.connect(self._on_apply_exp_done)
        self._apply_worker.failed.connect(self._on_apply_exp_failed)
        self._apply_worker.start()

    def _on_apply_exp_done(self, result: JobResult) -> None:
        self.apply_exp_btn.setEnabled(True)
        applied = result.applied if result.applied is not None else "?"
        total = result.total if result.total is not None else "?"
        if result.status == "ok":
            self.status_label.setText(
                f"Expo : {applied}/{total} appliquée(s) dans Lightroom."
            )
        else:
            self.status_label.setText(f"Expo : {applied}/{total} — {result.error}")

    def _on_apply_exp_failed(self, message: str) -> None:
        self.apply_exp_btn.setEnabled(True)
        self.status_label.setText(f"Apply Expo échoué : {message}")

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
        self.calibrate_wb_btn.setEnabled(False)
        self.photo_list.clear()
        self.status_label.setText("Récupération de la sélection (seeds)…")
        # Récupère la sélection ; les seeds = photos à WB Custom dans le lot.
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_calib_photos)
        self._worker.failed.connect(self._on_calib_failed)
        self._worker.start()

    def _on_calib_photos(self, result: JobResult) -> None:
        if not result.photos:
            self.calibrate_wb_btn.setEnabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        self.status_label.setText(
            f"{len(result.photos)} photo(s) — calibrage WB (décodage as-shot)…"
        )
        # Informatif : calibre + détecte le régime, ne planifie pas (plan=False).
        self._calib_worker = CalibrateWorker(result.photos, plan=False)
        self._calib_worker.finished_result.connect(self._on_calib_done)
        self._calib_worker.failed.connect(self._on_calib_failed)
        self._calib_worker.start()

    def _on_calib_done(self, res: CalibrationResult) -> None:
        self.calibrate_wb_btn.setEnabled(True)
        cal = res.calibration
        self.photo_list.clear()
        self.photo_list.addItem(
            f"Calibrage : {res.n_seeds} seed(s) | pente {cal.slope_rg:.0f}K/[r/g] "
            f"| intercept {cal.intercept:+.0f}K | Tint {cal.tint:+.0f}"
        )
        self.photo_list.addItem(
            f"Résidu {cal.residual_k:.0f}K | étalement {cal.temp_spread_k:.0f}K"
        )
        self.photo_list.addItem(
            f"Régime : {res.regime.regime.value} — {res.regime.message}"
        )
        self.status_label.setText(
            f"Calibrage WB OK — {res.n_seeds} seed(s), régime {res.regime.regime.value}. "
            f"« Apply WB » pour appliquer à la sélection."
        )

    def _on_calib_failed(self, message: str) -> None:
        self.calibrate_wb_btn.setEnabled(True)
        self.status_label.setText(f"Calibrage échoué : {message}")

    def _on_apply(self) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText(
                "Pont inactif — démarrez l'application depuis Lightroom."
            )
            return
        self.apply_wb_btn.setEnabled(False)
        self.photo_list.clear()
        self._wb_force = self.apply_force_cb.isChecked()
        self.status_label.setText("Récupération de la sélection (apply WB)…")
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_apply_wb_photos)
        self._worker.failed.connect(self._on_apply_failed)
        self._worker.start()

    def _on_apply_wb_photos(self, result: JobResult) -> None:
        if not result.photos:
            self.apply_wb_btn.setEnabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        self.status_label.setText(
            f"{len(result.photos)} photo(s) — calibrage + planification WB "
            f"(décodage as-shot)…"
        )
        # Autonome : calibre sur les seeds + planifie les corrections en un coup.
        self._calib_worker = CalibrateWorker(
            result.photos, plan=True, force_seeds=self._wb_force
        )
        self._calib_worker.finished_result.connect(self._on_apply_wb_planned)
        self._calib_worker.failed.connect(self._on_apply_failed)
        self._calib_worker.start()

    def _on_apply_wb_planned(self, res: CalibrationResult) -> None:
        if not res.adjustments:
            self.apply_wb_btn.setEnabled(True)
            self.status_label.setText("Calibrage OK — aucune photo à corriger.")
            return
        mode = "toute la sélection" if self._wb_force else "hors seeds"
        self.status_label.setText(
            f"Application WB sur {res.n_planned} photo(s) ({mode}) — "
            f"régime {res.regime.regime.value}…"
        )
        payload = {"adjustments": [a.model_dump() for a in res.adjustments]}
        self._apply_worker = JobWorker(JobType.APPLY_ADJUSTMENTS, payload, timeout=120.0)
        self._apply_worker.finished_result.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_apply_failed)
        self._apply_worker.start()

    def _on_apply_done(self, result: JobResult) -> None:
        self.apply_wb_btn.setEnabled(True)
        applied = result.applied if result.applied is not None else "?"
        total = result.total if result.total is not None else "?"
        if result.status == "ok":
            self.status_label.setText(
                f"WB : {applied}/{total} correction(s) appliquée(s) dans Lightroom — "
                f"vérifiez, retouchez les exceptions."
            )
        else:
            self.status_label.setText(
                f"WB : {applied}/{total} appliqués — {result.error}"
            )

    def _on_apply_failed(self, message: str) -> None:
        self.apply_wb_btn.setEnabled(True)
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
