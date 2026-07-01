"""Fenêtre principale PySide6 — seeds explicites + correction auto par axe (espace rendu).

Flux (cf. CLAUDE.md / plan de refonte k-NN) :
- **Analyse Catalogue** : index métadonnées (EXIF + develop) de toutes les photos.
- **Ajouter seeds** / **Supprimer seeds** : marque/démarque la sélection comme seeds
  dans le cache SQLite (`is_seed`) — référence de style pour le matching k-NN.
- **Analyser sélection** : mesure RAW source + JPEG boîtier + aperçu rendu (zone
  nette), peuple le cache. Aucune planification ni application.
- Case **[Réf = JPEG embarqué]** : force le JPEG boîtier comme cible (sinon : k-NN
  sur les seeds les plus proches en analyse RAW).
- **Apply Exposition / WB / HSL** : pour chaque axe, mesure (cache frais, l'aperçu
  rendu n'est jamais lu en cache pour l'état courant — toujours redécodé) + calcule
  la cible (embedded ou k-NN seeds) + **applique directement** dans Lightroom.

La mesure tourne dans `AutoCorrectWorker` (QThread) : RAW (demosaic GPU, zone
nette) + JPEG boîtier + aperçu courant (Previews.lrdata / miniature plugin) →
`core.autocorrect.plan` (sauf en mode `analyze_only`). Le pont plugin est requis
pour connaître la sélection et pour appliquer.
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

from ..core import cache as cachemod
from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType
from .autocorrect_worker import AutoCorrectResult, AutoCorrectWorker
from .job_worker import JobWorker

_AXIS_LABELS = {"expo": "Exposition", "wb": "WB", "hsl": "HSL"}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lr Automation — correction auto")
        self.resize(720, 560)

        self._worker: JobWorker | None = None
        self._check_worker: JobWorker | None = None
        self._auto_worker: AutoCorrectWorker | None = None
        self._apply_worker: JobWorker | None = None
        self._seed_worker: JobWorker | None = None
        # Axe en cours (None = analyse seule) — pour le libellé du résultat.
        self._pending_axis: str | None = None
        self._pending_action: str | None = None  # "seed_add" | "seed_remove" | None

        self.bridge_label = QLabel()
        self.status_label = QLabel("Prêt. Sélectionnez des photos dans Lightroom.")
        self.plan_summary_label = QLabel("")
        self.plan_summary_label.setStyleSheet("font-weight: bold;")

        # Outils / diagnostic.
        self.test_btn = QPushButton("Test pont")
        self.analyze_catalog_btn = QPushButton("Analyse Catalogue")

        # Seeds.
        self.add_seeds_btn = QPushButton("Ajouter seeds")
        self.remove_seeds_btn = QPushButton("Supprimer seeds")
        self.analyze_selection_btn = QPushButton("Analyser sélection")

        # Référence + actions par axe.
        self.cb_embedded = QCheckBox("Réf = JPEG embarqué")
        self.cb_embedded.setToolTip(
            "Décoché : cible = k-NN sur les seeds dont l'analyse RAW (zone nette) est\n"
            "la plus proche (utilise leur aperçu déjà retouché comme référence de style).\n"
            "Coché : force le JPEG boîtier comme cible (recale sur l'appareil)."
        )
        self.apply_expo_btn = QPushButton("Apply Exposition")
        self.apply_wb_btn = QPushButton("Apply WB")
        self.apply_hsl_btn = QPushButton("Apply HSL")

        self.photo_list = QListWidget()

        self.test_btn.clicked.connect(self._on_check)
        self.analyze_catalog_btn.clicked.connect(self._on_analyze_catalog)
        self.add_seeds_btn.clicked.connect(lambda: self._start_seed_toggle(True))
        self.remove_seeds_btn.clicked.connect(lambda: self._start_seed_toggle(False))
        self.analyze_selection_btn.clicked.connect(self._start_analyze_selection)
        self.apply_expo_btn.clicked.connect(lambda: self._start_apply_axis("expo"))
        self.apply_wb_btn.clicked.connect(lambda: self._start_apply_axis("wb"))
        self.apply_hsl_btn.clicked.connect(lambda: self._start_apply_axis("hsl"))

        layout = QVBoxLayout()
        layout.addWidget(self.bridge_label)
        layout.addWidget(self.status_label)

        tools_row = QHBoxLayout()
        tools_row.addWidget(self.test_btn)
        tools_row.addWidget(self.analyze_catalog_btn)
        tools_row.addStretch()
        layout.addLayout(tools_row)

        seeds_row = QHBoxLayout()
        seeds_row.addWidget(self.add_seeds_btn)
        seeds_row.addWidget(self.remove_seeds_btn)
        seeds_row.addWidget(self.analyze_selection_btn)
        seeds_row.addStretch()
        layout.addLayout(seeds_row)

        axes_row = QHBoxLayout()
        axes_row.addWidget(self.cb_embedded)
        axes_row.addSpacing(16)
        axes_row.addWidget(self.apply_expo_btn)
        axes_row.addWidget(self.apply_wb_btn)
        axes_row.addWidget(self.apply_hsl_btn)
        axes_row.addStretch()
        layout.addLayout(axes_row)

        layout.addWidget(self.plan_summary_label)
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

    def _set_actions_enabled(self, enabled: bool) -> None:
        for btn in (
            self.add_seeds_btn, self.remove_seeds_btn, self.analyze_selection_btn,
            self.apply_expo_btn, self.apply_wb_btn, self.apply_hsl_btn,
        ):
            btn.setEnabled(enabled)

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
        catalog_path = next((p.catalog_path for p in photos if p.catalog_path), None)
        n_seeds = 0
        if catalog_path:
            try:
                conn = cachemod.open_cache(catalog_path)
                n_seeds = len(cachemod.list_seed_uuids(conn))
                conn.close()
            except Exception:
                pass
        cameras: dict[str, int] = {}
        for p in photos:
            cam = p.exif.camera or "?"
            cameras[cam] = cameras.get(cam, 0) + 1
        self.photo_list.clear()
        self.photo_list.addItem(
            f"Catalogue : {len(photos)} photo(s) — {n_seeds} seed(s) marqué(s)."
        )
        for cam, n in sorted(cameras.items(), key=lambda kv: -kv[1]):
            self.photo_list.addItem(f"  {cam} : {n} photo(s)")
        self.status_label.setText(f"Catalogue indexé — {len(photos)} photo(s).")

    def _on_catalog_failed(self, message: str) -> None:
        self.analyze_catalog_btn.setEnabled(True)
        self.status_label.setText(f"Analyse Catalogue échouée : {message}")

    # ------------------------------------------------------------------ #
    # Ajouter / Supprimer seeds — marquage explicite en DB, pas de décodage pixel
    # ------------------------------------------------------------------ #
    def _start_seed_toggle(self, value: bool) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText("Pont inactif — démarrez l'application depuis Lightroom.")
            return
        self._pending_action = "seed_add" if value else "seed_remove"
        self._set_actions_enabled(False)
        self.status_label.setText("Récupération de la sélection…")
        self._seed_worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._seed_worker.finished_result.connect(
            lambda res: self._on_selection_for_seed_toggle(res, value)
        )
        self._seed_worker.failed.connect(self._on_auto_failed)
        self._seed_worker.start()

    def _on_selection_for_seed_toggle(self, result: JobResult, value: bool) -> None:
        self._set_actions_enabled(True)
        if not result.photos:
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        catalog_path = next((p.catalog_path for p in result.photos if p.catalog_path), None)
        if not catalog_path:
            self.status_label.setText("Aucun catalog_path reçu — impossible de localiser le cache.")
            return
        try:
            conn = cachemod.open_cache(catalog_path)
            for p in result.photos:
                cachemod.put_picture(
                    conn, p.photo_id, path=p.path, catalog_path=p.catalog_path,
                    exif=(p.exif.model_dump() if p.exif else None),
                    current_develop=p.current_develop or {},
                )
                cachemod.set_seed(conn, p.photo_id, value)
            conn.close()
        except Exception as exc:
            self.status_label.setText(f"Marquage seed échoué : {exc}")
            return
        verb = "marquée(s) seed" if value else "retirée(s) des seeds"
        self.status_label.setText(f"{len(result.photos)} photo(s) {verb}.")

    # ------------------------------------------------------------------ #
    # Analyser sélection — peuple le cache (RAW+JPEG boîtier+aperçu), n'applique rien
    # ------------------------------------------------------------------ #
    def _start_analyze_selection(self) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText("Pont inactif — démarrez l'application depuis Lightroom.")
            return
        self._pending_axis = None
        self._set_actions_enabled(False)
        self.photo_list.clear()
        self.plan_summary_label.setText("")
        self.status_label.setText("Récupération de la sélection…")
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_selection_for_analyze)
        self._worker.failed.connect(self._on_auto_failed)
        self._worker.start()

    def _on_selection_for_analyze(self, result: JobResult) -> None:
        if not result.photos:
            self._set_actions_enabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        n = len(result.photos)
        self.status_label.setText(f"{n} photo(s) — analyse RAW + JPEG boîtier + aperçu…")
        self._auto_worker = AutoCorrectWorker(result.photos, analyze_only=True)
        self._auto_worker.progress.connect(self.status_label.setText)
        self._auto_worker.finished_result.connect(self._on_analyze_done)
        self._auto_worker.failed.connect(self._on_auto_failed)
        self._auto_worker.start()

    def _on_analyze_done(self, res: AutoCorrectResult) -> None:
        self._set_actions_enabled(True)
        self.photo_list.clear()
        for note in res.notes:
            self.photo_list.addItem(note)
        self.status_label.setText(
            f"Analyse terminée — {res.n_measured} mesurée(s), {res.n_skipped} sans rendu."
        )

    # ------------------------------------------------------------------ #
    # Apply Exposition / WB / HSL — mesure (aperçu toujours frais) + applique direct
    # ------------------------------------------------------------------ #
    def _start_apply_axis(self, axis: str) -> None:
        if not job_queue.bridge_connected():
            self.status_label.setText("Pont inactif — démarrez l'application depuis Lightroom.")
            return
        self._pending_axis = axis
        self._set_actions_enabled(False)
        self.photo_list.clear()
        self.plan_summary_label.setText("")
        self.status_label.setText("Récupération de la sélection…")
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_selection_for_apply)
        self._worker.failed.connect(self._on_auto_failed)
        self._worker.start()

    def _on_selection_for_apply(self, result: JobResult) -> None:
        if not result.photos:
            self._set_actions_enabled(True)
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        axis = self._pending_axis
        n = len(result.photos)
        self.status_label.setText(
            f"{n} photo(s) — mesure {_AXIS_LABELS.get(axis, axis)} (peut prendre du temps)…"
        )
        self._auto_worker = AutoCorrectWorker(
            result.photos,
            axes=frozenset({axis}),
            forced_embedded=self.cb_embedded.isChecked(),
            force_fresh_preview=True,
        )
        self._auto_worker.progress.connect(self.status_label.setText)
        self._auto_worker.finished_result.connect(self._on_plan_ready)
        self._auto_worker.failed.connect(self._on_auto_failed)
        self._auto_worker.start()

    def _on_plan_ready(self, res: AutoCorrectResult) -> None:
        diag = res.diagnostics
        self.photo_list.clear()
        if diag is not None:
            self.plan_summary_label.setText(
                f"Mode {diag.mode} — {diag.n_seeds} seed(s), {diag.n_targets} cible(s), "
                f"{res.n_measured} mesurée(s), {res.n_skipped} sans rendu."
            )
            for note in diag.notes:
                self.photo_list.addItem(f"  • {note}")
        for adj in res.adjustments[:10]:
            keys = ", ".join(f"{k}={v}" for k, v in adj.develop.items())
            self.photo_list.addItem(f"  {adj.photo_id[:8]} → {keys}")
        if len(res.adjustments) > 10:
            self.photo_list.addItem(f"  … +{len(res.adjustments) - 10} photo(s)")

        if not res.adjustments:
            self._set_actions_enabled(True)
            self.status_label.setText("Aucune correction nécessaire (ou aucune cible exploitable).")
            return

        axis_label = _AXIS_LABELS.get(self._pending_axis, self._pending_axis)
        self.status_label.setText(
            f"Application {axis_label} — {len(res.adjustments)} photo(s) dans Lightroom…"
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
        axis_label = _AXIS_LABELS.get(self._pending_axis, self._pending_axis)
        if result.status == "ok":
            self.status_label.setText(
                f"{axis_label} appliqué : {applied}/{total} photo(s) — vérifiez le rendu."
            )
        else:
            self.status_label.setText(f"{axis_label} : {applied}/{total} — {result.error}")

    def _on_auto_failed(self, message: str) -> None:
        self._set_actions_enabled(True)
        self.status_label.setText(f"Erreur : {message}")
