"""Fenêtre principale PySide6 — correction auto expo / WB / HSL par photo.

Flux unifié (espace rendu, cf. core.autocorrect) :
- **Analyse Catalogue** : index métadonnées (EXIF + develop) de toutes les photos.
- Cases **[Exposition] [WB] [HSL]** : axes à corriger. Case **[Réf JPEG embarqué]** :
  force le JPEG boîtier comme modèle (sinon : photos retouchées de la sélection si
  présentes, repli JPEG boîtier).
- **Aperçu** : mesure la sélection + calcule les corrections, **affiche sans appliquer**.
- **Appliquer** : mesure + calcule + applique via le job `apply_adjustments`.

La mesure tourne dans `AutoCorrectWorker` (QThread) : lecture RAW parallèle (JPEG boîtier
+ as-shot) + rendu courant (aperçu Previews.lrdata / miniature plugin) → `autocorrect.plan`.
Le pont plugin est requis pour connaître la sélection et pour appliquer.
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

from ..core import seeds
from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType
from .autocorrect_worker import AutoCorrectResult, AutoCorrectWorker
from .job_worker import JobWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lr Automation — correction auto")
        self.resize(680, 520)

        self._worker: JobWorker | None = None
        self._check_worker: JobWorker | None = None
        self._auto_worker: AutoCorrectWorker | None = None
        self._apply_worker: JobWorker | None = None
        # True si l'action en cours doit appliquer après planification (Appliquer vs Aperçu).
        self._apply_after_plan = False

        self.bridge_label = QLabel()
        self.status_label = QLabel("Prêt. Sélectionnez des photos dans Lightroom.")

        # Outils / diagnostic.
        self.test_btn = QPushButton("Test pont")
        self.analyze_catalog_btn = QPushButton("Analyse Catalogue")

        # Axes à corriger + référence.
        self.cb_expo = QCheckBox("Exposition")
        self.cb_wb = QCheckBox("WB")
        self.cb_hsl = QCheckBox("HSL")
        for cb in (self.cb_expo, self.cb_wb, self.cb_hsl):
            cb.setChecked(True)
        self.cb_embedded = QCheckBox("Réf = JPEG embarqué")
        self.cb_embedded.setToolTip(
            "Décoché : les photos déjà retouchées de la sélection servent de modèle ;\n"
            "si aucune, le JPEG boîtier de chaque photo sert de référence.\n"
            "Coché : force le JPEG boîtier comme modèle (recale sur l'appareil)."
        )

        # Actions.
        self.preview_btn = QPushButton("Aperçu (sans appliquer)")
        self.apply_btn = QPushButton("Appliquer à la sélection")

        self.photo_list = QListWidget()

        self.test_btn.clicked.connect(self._on_check)
        self.analyze_catalog_btn.clicked.connect(self._on_analyze_catalog)
        self.preview_btn.clicked.connect(lambda: self._start_autocorrect(apply_after=False))
        self.apply_btn.clicked.connect(lambda: self._start_autocorrect(apply_after=True))

        layout = QVBoxLayout()
        layout.addWidget(self.bridge_label)
        layout.addWidget(self.status_label)

        tools_row = QHBoxLayout()
        tools_row.addWidget(self.test_btn)
        tools_row.addWidget(self.analyze_catalog_btn)
        tools_row.addStretch()
        layout.addLayout(tools_row)

        axes_row = QHBoxLayout()
        axes_row.addWidget(QLabel("Corriger :"))
        axes_row.addWidget(self.cb_expo)
        axes_row.addWidget(self.cb_wb)
        axes_row.addWidget(self.cb_hsl)
        axes_row.addSpacing(16)
        axes_row.addWidget(self.cb_embedded)
        axes_row.addStretch()
        layout.addLayout(axes_row)

        action_row = QHBoxLayout()
        action_row.addWidget(self.preview_btn)
        action_row.addWidget(self.apply_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        layout.addWidget(self.photo_list)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

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

    def _selected_axes(self) -> frozenset[str]:
        axes = set()
        if self.cb_expo.isChecked():
            axes.add("expo")
        if self.cb_wb.isChecked():
            axes.add("wb")
        if self.cb_hsl.isChecked():
            axes.add("hsl")
        return frozenset(axes)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.preview_btn.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # Test pont
    # ------------------------------------------------------------------ #
    def _on_check(self) -> None:
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
    # Analyse Catalogue — index métadonnées (pas de pixels)
    # ------------------------------------------------------------------ #
    def _on_analyze_catalog(self) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText("Pont inactif — démarrez l'application depuis Lightroom.")
            return
        self.analyze_catalog_btn.setEnabled(False)
        self.photo_list.clear()
        self.status_label.setText("Récupération du catalogue (toutes les photos)…")
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
            f"Catalogue : {len(photos)} photo(s) — {len(seed_photos)} retouchée(s) (WB Custom)."
        )
        for cam, n in sorted(cameras.items(), key=lambda kv: -kv[1]):
            self.photo_list.addItem(f"  {cam} : {n} photo(s)")
        self.status_label.setText(f"Catalogue indexé — {len(photos)} photo(s).")

    def _on_catalog_failed(self, message: str) -> None:
        self.analyze_catalog_btn.setEnabled(True)
        self.status_label.setText(f"Analyse Catalogue échouée : {message}")

    # ------------------------------------------------------------------ #
    # Aperçu / Appliquer — récupère la sélection puis lance AutoCorrectWorker
    # ------------------------------------------------------------------ #
    def _start_autocorrect(self, apply_after: bool) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText("Pont inactif — démarrez l'application depuis Lightroom.")
            return
        axes = self._selected_axes()
        if not axes:
            self.status_label.setText("Cochez au moins un axe (Exposition / WB / HSL).")
            return
        self._apply_after_plan = apply_after
        self._set_actions_enabled(False)
        self.photo_list.clear()
        self.status_label.setText("Récupération de la sélection…")
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_selection_for_auto)
        self._worker.failed.connect(self._on_auto_failed)
        self._worker.start()

    def _on_selection_for_auto(self, result: JobResult) -> None:
        if not result.photos:
            self._set_actions_enabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        n = len(result.photos)
        self.status_label.setText(
            f"{n} photo(s) — lecture RAW + mesure du rendu (peut prendre du temps)…"
        )
        self._auto_worker = AutoCorrectWorker(
            result.photos,
            axes=self._selected_axes(),
            forced_embedded=self.cb_embedded.isChecked(),
        )
        self._auto_worker.progress.connect(self.status_label.setText)
        self._auto_worker.finished_result.connect(self._on_plan_ready)
        self._auto_worker.failed.connect(self._on_auto_failed)
        self._auto_worker.start()

    def _on_plan_ready(self, res: AutoCorrectResult) -> None:
        diag = res.diagnostics
        self.photo_list.clear()
        self.photo_list.addItem(
            f"Mode {diag.mode} — {diag.n_seeds} modèle(s), {diag.n_targets} cible(s), "
            f"{res.n_measured} mesurée(s), {res.n_skipped} sans rendu."
            + (f" Régime WB : {diag.regime}." if diag.regime else "")
        )
        for note in diag.notes:
            self.photo_list.addItem(f"  • {note}")
        # Détail par photo (10 premières).
        for adj in res.adjustments[:10]:
            keys = ", ".join(f"{k}={v}" for k, v in adj.develop.items())
            self.photo_list.addItem(f"  {adj.photo_id[:8]} → {keys}")
        if len(res.adjustments) > 10:
            self.photo_list.addItem(f"  … +{len(res.adjustments) - 10} photo(s)")

        if not res.adjustments:
            self._set_actions_enabled(True)
            self.status_label.setText("Aucune correction nécessaire (ou rien à corriger).")
            return

        if not self._apply_after_plan:
            self._set_actions_enabled(True)
            self.status_label.setText(
                f"Aperçu — {len(res.adjustments)} photo(s) seraient corrigées. "
                f"« Appliquer » pour exécuter."
            )
            return

        # Appliquer : soumettre le job apply_adjustments.
        self.status_label.setText(
            f"Application de {len(res.adjustments)} correction(s) dans Lightroom…"
        )
        payload = {"adjustments": [a.model_dump() for a in res.adjustments]}
        self._apply_worker = JobWorker(JobType.APPLY_ADJUSTMENTS, payload, timeout=180.0)
        self._apply_worker.finished_result.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_auto_failed)
        self._apply_worker.start()

    def _on_apply_done(self, result: JobResult) -> None:
        self._set_actions_enabled(True)
        applied = result.applied if result.applied is not None else "?"
        total = result.total if result.total is not None else "?"
        if result.status == "ok":
            self.status_label.setText(
                f"Appliqué : {applied}/{total} photo(s) dans Lightroom — vérifiez le rendu."
            )
        else:
            self.status_label.setText(f"Application : {applied}/{total} — {result.error}")

    def _on_auto_failed(self, message: str) -> None:
        self._set_actions_enabled(True)
        self.status_label.setText(f"Erreur : {message}")
