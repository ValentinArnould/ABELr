"""Fenêtre principale PySide6 — références (seeds) + correction auto par axe.

Flux de refonte (cf. CLAUDE.md / diagramme workflow) :

- **Analyse Catalogue** : index métadonnées (EXIF + develop) de toutes les photos.
- **Marquer + analyser références** : marque la sélection `is_seed` en DB ET la
  mesure (RAW GPU zone nette + JPEG boîtier + **rendu frais** via le plugin) →
  le seed est immédiatement exploitable par le matching k-NN (plus de « marqué
  mais inutilisable » silencieux).
- **Retirer des références** : démarque `is_seed` (pas de mesure).
- Cases **[Exposition] [WB] [HSL]** : axes à corriger.
- Case **[Réf = JPEG embarqué]** : force le JPEG boîtier comme cible (sinon : k-NN
  sur les seeds les plus proches en analyse RAW).
- **Aperçu** : mesure (rendu frais) + planifie, **affiche les deltas sans appliquer**.
- **Appliquer** : applique le plan d'Aperçu s'il existe pour la sélection courante,
  sinon mesure + planifie + applique en une passe.

Point clé de correctness : **toute mesure de l'état courant part d'un rendu frais
demandé au plugin** (`get_thumbnails` → JPEG écrit par Lightroom), et non plus du
fichier `Previews.lrdata` passif (potentiellement périmé). Si le plugin ne fournit
aucune miniature (aperçus absents), on retombe sur le canal passif (dégradé, non
bloquant).

⚠️ Hypothèse à valider en vrai (verrou) : `requestJpegThumbnail` reflète bien l'état
develop courant et non un cache périmé. Sinon → replier sur `LrExportSession` côté
plugin (cf. `core.measure`).
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import cache as cachemod
from ..server.job_queue import job_queue
from ..server.models import JobResult, JobType
from .autocorrect_worker import AutoCorrectResult, AutoCorrectWorker
from .neutral_preview_worker import NeutralPreviewWorker
from .job_worker import JobWorker

_AXIS_LABELS = {"expo": "Exposition", "wb": "WB", "hsl": "HSL", "calib": "Étalonnage"}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ABELr — correction auto")
        self.resize(760, 600)

        self._worker: JobWorker | None = None
        self._check_worker: JobWorker | None = None
        self._render_worker: JobWorker | None = None
        self._auto_worker: AutoCorrectWorker | None = None
        self._neutral_worker: NeutralPreviewWorker | None = None
        self._apply_worker: JobWorker | None = None
        self._seed_worker: JobWorker | None = None

        # Machine à états minimale : l'op en cours, la sélection et le rendu frais
        # sont conservés entre les sauts (sélection → rendu → mesure).
        self._op: str | None = None                 # "ref"|"preview"|"apply"|"seed_remove"|"neutral"
        self._photos: list = []                     # PhotoResult de la sélection courante
        self._thumb_paths: dict[str, str] = {}       # uuid → JPEG rendu frais (plugin)
        # Plan d'Aperçu réutilisable par Appliquer (même sélection).
        self._pending_adjustments: list | None = None
        self._pending_ids: frozenset[str] = frozenset()

        self.bridge_label = QLabel()
        self.status_label = QLabel("Prêt. Sélectionnez des photos dans Lightroom.")
        self.plan_summary_label = QLabel("")
        self.plan_summary_label.setStyleSheet("font-weight: bold;")

        # Barre de chargement des opérations d'analyse / mesure d'images. Cachée au
        # repos ; déterminée quand un worker fournit (fait, total), animée (busy) sinon.
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)

        # Diagnostic.
        self.test_btn = QPushButton("Test pont")
        self.analyze_catalog_btn = QPushButton("Analyse Catalogue")

        # Références (seeds).
        self.mark_refs_btn = QPushButton("Marquer + analyser références")
        self.unmark_refs_btn = QPushButton("Retirer des références")
        self.calibrate_neutral_btn = QPushButton("Calibrate Neutral Previews")

        # Axes + référence.
        self.cb_expo = QCheckBox("Exposition")
        self.cb_wb = QCheckBox("WB")
        self.cb_hsl = QCheckBox("HSL")
        for cb in (self.cb_expo, self.cb_wb, self.cb_hsl):
            cb.setChecked(True)
        self.cb_calib = QCheckBox("Étalonnage")
        self.cb_calib.setChecked(False)
        self.cb_calib.setToolTip(
            "Transplant k-NN depuis les seeds (ShadowTint, Hue/Saturation R/G/B) —\n"
            "toujours via seeds même en mode 'Réf = JPEG embarqué' : aucune cible\n"
            "d'étalonnage n'est mesurable depuis un rendu, seed ou seed manquant → ignoré."
        )
        self.cb_embedded = QCheckBox("Réf = JPEG embarqué")
        self.cb_embedded.setToolTip(
            "Décoché : cible = k-NN sur les seeds dont l'analyse RAW (zone nette) est\n"
            "la plus proche (utilise leur aperçu déjà retouché comme référence de style).\n"
            "Coché : cible = JPEG boîtier, ancré sur le rendu neutre (WB As Shot,\n"
            "Expo 0, HSL 0) — corrige seulement la déviation PAR PHOTO après\n"
            "soustraction du biais de profil ; valeurs absolues, idempotentes.\n"
            "Le 1ᵉʳ Aperçu après un changement de style recalcule les ancres dans\n"
            "Lightroom (render_probe, ~1-4 s/photo, ensuite servi par le cache)."
        )

        # Correction.
        self.preview_btn = QPushButton("Aperçu")
        self.preview_btn.setToolTip("Mesure + planifie, affiche les deltas SANS appliquer.")
        self.apply_btn = QPushButton("Appliquer")
        self.apply_btn.setToolTip(
            "Applique le plan d'Aperçu s'il existe, sinon mesure + planifie + applique."
        )

        self.photo_list = QListWidget()

        self.test_btn.clicked.connect(self._on_check)
        self.analyze_catalog_btn.clicked.connect(self._on_analyze_catalog)
        self.mark_refs_btn.clicked.connect(lambda: self._begin("ref"))
        self.unmark_refs_btn.clicked.connect(lambda: self._begin("seed_remove"))
        self.calibrate_neutral_btn.clicked.connect(lambda: self._begin("neutral"))
        self.preview_btn.clicked.connect(lambda: self._begin("preview"))
        self.apply_btn.clicked.connect(self._on_apply_click)

        layout = QVBoxLayout()
        layout.addWidget(self.bridge_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)

        tools_row = QHBoxLayout()
        tools_row.addWidget(self.test_btn)
        tools_row.addWidget(self.analyze_catalog_btn)
        tools_row.addStretch()
        layout.addLayout(tools_row)

        refs_row = QHBoxLayout()
        refs_row.addWidget(self.mark_refs_btn)
        refs_row.addWidget(self.unmark_refs_btn)
        refs_row.addWidget(self.calibrate_neutral_btn)
        refs_row.addStretch()
        layout.addLayout(refs_row)

        axes_row = QHBoxLayout()
        axes_row.addWidget(QLabel("Axes :"))
        axes_row.addWidget(self.cb_expo)
        axes_row.addWidget(self.cb_wb)
        axes_row.addWidget(self.cb_hsl)
        axes_row.addWidget(self.cb_calib)
        axes_row.addSpacing(16)
        axes_row.addWidget(self.cb_embedded)
        axes_row.addSpacing(16)
        axes_row.addWidget(self.preview_btn)
        axes_row.addWidget(self.apply_btn)
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
            self.mark_refs_btn, self.unmark_refs_btn, self.calibrate_neutral_btn,
            self.preview_btn, self.apply_btn,
        ):
            btn.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # Barre de chargement (analyse / mesure d'images)
    # ------------------------------------------------------------------ #
    def _progress_busy(self) -> None:
        """Affiche la barre en mode indéterminé (animation) — étape sans compteur."""
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)

    def _on_progress_count(self, done: int, total: int) -> None:
        """Passe la barre en mode déterminé `done/total` (émis par les workers GPU)."""
        if total <= 0:
            self._progress_busy()
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        self.progress_bar.setVisible(True)

    def _progress_done(self) -> None:
        """Masque et réinitialise la barre (fin ou échec de l'opération)."""
        self.progress_bar.reset()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setVisible(False)

    def _checked_axes(self) -> frozenset[str]:
        axes = set()
        if self.cb_expo.isChecked():
            axes.add("expo")
        if self.cb_wb.isChecked():
            axes.add("wb")
        if self.cb_hsl.isChecked():
            axes.add("hsl")
        if self.cb_calib.isChecked():
            axes.add("calib")
        return frozenset(axes)

    def _require_bridge(self) -> bool:
        if not job_queue.bridge_connected():
            self.status_label.setText("Pont inactif — démarrez l'application depuis Lightroom.")
            return False
        return True

    # ------------------------------------------------------------------ #
    # Test pont
    # ------------------------------------------------------------------ #
    def _on_check(self) -> None:
        if not self._require_bridge():
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
        if not self._require_bridge():
            return
        self.analyze_catalog_btn.setEnabled(False)
        self._progress_busy()
        self.photo_list.clear()
        self.status_label.setText("Récupération du catalogue (toutes les photos)…")
        self._worker = JobWorker(JobType.GET_CATALOG_PHOTOS, timeout=120.0)
        self._worker.finished_result.connect(self._on_catalog_result)
        self._worker.failed.connect(self._on_catalog_failed)
        self._worker.start()

    def _on_catalog_result(self, result: JobResult) -> None:
        self.analyze_catalog_btn.setEnabled(True)
        self._progress_done()
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
            f"Catalogue : {len(photos)} photo(s) — {n_seeds} référence(s) marquée(s)."
        )
        for cam, n in sorted(cameras.items(), key=lambda kv: -kv[1]):
            self.photo_list.addItem(f"  {cam} : {n} photo(s)")
        self.status_label.setText(f"Catalogue indexé — {len(photos)} photo(s).")

    def _on_catalog_failed(self, message: str) -> None:
        self.analyze_catalog_btn.setEnabled(True)
        self._progress_done()
        self.status_label.setText(f"Analyse Catalogue échouée : {message}")

    # ------------------------------------------------------------------ #
    # Étape 1 : récupérer la sélection Lr (commune à toutes les opérations)
    # ------------------------------------------------------------------ #
    def _begin(self, op: str) -> None:
        if not self._require_bridge():
            return
        self._op = op
        # Toute nouvelle opération invalide un plan d'Aperçu antérieur.
        self._pending_adjustments = None
        self._pending_ids = frozenset()
        self._set_actions_enabled(False)
        self._progress_busy()
        self.plan_summary_label.setText("")
        self.status_label.setText("Récupération de la sélection…")
        self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
        self._worker.finished_result.connect(self._on_selection)
        self._worker.failed.connect(self._on_auto_failed)
        self._worker.start()

    def _on_selection(self, result: JobResult) -> None:
        if not result.photos:
            self._set_actions_enabled(True)
            self._progress_done()
            self.status_label.setText("Aucune photo sélectionnée dans Lightroom.")
            return
        self._photos = result.photos
        op = self._op

        if op == "seed_remove":
            self._apply_seed_flag(result.photos, False)
            self._set_actions_enabled(True)
            self._progress_done()  # marquage DB seul, pas de mesure d'images
            return

        if op == "neutral":
            n = len(result.photos)
            self.status_label.setText(
                f"{n} photo(s) — rendu neutre de calibration (écriture + rendu Lr, lourd)…"
            )
            self._neutral_worker = NeutralPreviewWorker(result.photos)
            self._neutral_worker.progress.connect(self.status_label.setText)
            self._neutral_worker.progress_count.connect(self._on_progress_count)
            self._neutral_worker.finished_result.connect(self._on_neutral_done)
            self._neutral_worker.failed.connect(self._on_auto_failed)
            self._neutral_worker.start()
            return

        # ref | preview | apply : marquer d'abord (ref), puis rendu frais + mesure.
        if op == "ref":
            self._apply_seed_flag(result.photos, True)

        if op in ("preview", "apply") and not self._checked_axes():
            self._set_actions_enabled(True)
            self.status_label.setText("Cochez au moins un axe (Exposition, WB ou HSL).")
            return

        # Mode embedded (ancré neutre) : aucune mesure du rendu courant → pas de
        # rendu frais à demander au plugin (l'ancre vient du cache/render_probe).
        if op in ("preview", "apply") and self.cb_embedded.isChecked():
            self._thumb_paths = {}
            self._launch_measure(result.photos)
            return

        self._fetch_fresh_render(result.photos)

    # ------------------------------------------------------------------ #
    # Étape 2 : rendu frais via le plugin (get_thumbnails) — état courant fiable
    # ------------------------------------------------------------------ #
    def _fetch_fresh_render(self, photos: list) -> None:
        n = len(photos)
        self.status_label.setText(f"Rendu frais de {n} photo(s) via Lightroom…")
        timeout = max(30.0, n * 0.6)
        payload = {"photo_ids": [p.photo_id for p in photos]}
        self._render_worker = JobWorker(JobType.GET_THUMBNAILS, payload, timeout=timeout)
        self._render_worker.finished_result.connect(self._on_render_ready)
        self._render_worker.failed.connect(self._on_render_failed)
        self._render_worker.start()

    def _on_render_failed(self, message: str) -> None:
        # Le rendu frais a échoué : on continue quand même (repli passif
        # Previews.lrdata dans le worker), en signalant la dégradation.
        self.status_label.setText(f"Rendu frais indisponible ({message}) — repli aperçu passif.")
        self._thumb_paths = {}
        self._launch_measure(self._photos)

    def _on_render_ready(self, result: JobResult) -> None:
        self._thumb_paths = {
            t.photo_id: t.thumbnail_path
            for t in result.thumbnails
            if t.thumbnail_path
        }
        got = len(self._thumb_paths)
        if got == 0:
            self.status_label.setText(
                "Aucun rendu frais fourni par le plugin — repli aperçu passif "
                "(Previews.lrdata). Générez les aperçus si la mesure échoue."
            )
        self._launch_measure(self._photos)

    def _launch_measure(self, photos: list) -> None:
        op = self._op
        if op == "ref":
            self.status_label.setText(
                f"{len(photos)} référence(s) — analyse RAW + JPEG boîtier + rendu…"
            )
            self._auto_worker = AutoCorrectWorker(
                photos, analyze_only=True, thumbnail_paths=self._thumb_paths,
            )
            self._auto_worker.progress.connect(self.status_label.setText)
            self._auto_worker.progress_count.connect(self._on_progress_count)
            self._auto_worker.finished_result.connect(self._on_analyze_done)
            self._auto_worker.failed.connect(self._on_auto_failed)
            self._auto_worker.start()
            return

        # preview | apply
        axes = self._checked_axes()
        self.status_label.setText(
            f"{len(photos)} photo(s) — mesure {'/'.join(_AXIS_LABELS[a] for a in sorted(axes))}…"
        )
        self._auto_worker = AutoCorrectWorker(
            photos,
            axes=axes,
            forced_embedded=self.cb_embedded.isChecked(),
            thumbnail_paths=self._thumb_paths,
            # Apply (mode seeds) : mesure toujours redécodée pour ne pas recalculer
            # un delta sur un rendu périmé. Aperçu : le cache PreviewJPEG suffit.
            # Mode embedded : sans effet (aucune mesure du rendu courant).
            force_fresh_preview=(op == "apply"),
        )
        self._auto_worker.progress.connect(self.status_label.setText)
        self._auto_worker.progress_count.connect(self._on_progress_count)
        self._auto_worker.finished_result.connect(self._on_plan_ready)
        self._auto_worker.failed.connect(self._on_auto_failed)
        self._auto_worker.start()

    # ------------------------------------------------------------------ #
    # Marquage seed en DB (commun à ref / retrait)
    # ------------------------------------------------------------------ #
    def _apply_seed_flag(self, photos: list, value: bool) -> None:
        catalog_path = next((p.catalog_path for p in photos if p.catalog_path), None)
        if not catalog_path:
            self.status_label.setText("Aucun catalog_path reçu — impossible de localiser le cache.")
            return
        try:
            conn = cachemod.open_cache(catalog_path)
            # Transaction UNIQUE (revue Fable 5 DB-03) : 2 commits/photo sur le
            # thread Qt gelaient le GUI plusieurs secondes à 300+ photos.
            for p in photos:
                cachemod.put_picture(
                    conn, p.photo_id, path=p.path, catalog_path=p.catalog_path,
                    exif=(p.exif.model_dump() if p.exif else None),
                    current_develop=p.current_develop or {},
                    commit=False,
                )
                cachemod.set_seed(conn, p.photo_id, value, commit=False)
            conn.commit()
            conn.close()
        except Exception as exc:
            self.status_label.setText(f"Marquage référence échoué : {exc}")
            return
        if not value:
            self.status_label.setText(f"{len(photos)} photo(s) retirée(s) des références.")

    # ------------------------------------------------------------------ #
    # Résultats
    # ------------------------------------------------------------------ #
    def _on_analyze_done(self, res: AutoCorrectResult) -> None:
        self._set_actions_enabled(True)
        self._progress_done()
        self.photo_list.clear()
        for note in res.notes:
            self.photo_list.addItem(note)
        self.status_label.setText(
            f"Références marquées + analysées — {res.n_measured} mesurée(s), "
            f"{res.n_skipped} sans rendu · pool exploitable : {res.seeds_usable}/{res.seeds_marked}."
        )

    def _on_neutral_done(self, message: str) -> None:
        self._set_actions_enabled(True)
        self._progress_done()
        self.status_label.setText(message)

    def _format_adjustment(self, adj) -> str:
        """Ligne d'aperçu « clé: courant → cible (Δ) » — les valeurs embedded sont
        absolues, l'écart avec le réglage courant est donc l'info utile."""
        current = next(
            (p.current_develop or {} for p in self._photos if p.photo_id == adj.photo_id), {}
        )
        parts = []
        for k, v in adj.develop.items():
            cur = current.get(k)
            if isinstance(v, (int, float)) and isinstance(cur, (int, float)):
                parts.append(f"{k}: {cur:g} → {v:g} (Δ{v - cur:+g})")
            elif isinstance(v, (int, float)):
                parts.append(f"{k}: ? → {v:g}")
            else:
                parts.append(f"{k}: {cur} → {v}")
        return f"  {adj.photo_id[:8]} → " + ", ".join(parts)

    def _on_plan_ready(self, res: AutoCorrectResult) -> None:
        diag = res.diagnostics
        self.photo_list.clear()
        if diag is not None:
            mode_label = "embedded (ancré neutre)" if diag.mode == "embedded" else diag.mode
            self.plan_summary_label.setText(
                f"Mode {mode_label} — {diag.n_seeds} seed(s), {diag.n_targets} cible(s), "
                f"{res.n_measured} mesurée(s), {res.n_skipped} non mesurable(s)."
            )
            for note in res.notes:
                self.photo_list.addItem(f"  • {note}")
            for note in diag.notes:
                self.photo_list.addItem(f"  • {note}")

        # Repli embedded silencieux : des seeds sont marqués mais inexploitables
        # (analyse RAW absente) → l'utilisateur croit corriger « au style seeds ».
        if (
            diag is not None and diag.mode == "embedded"
            and not self.cb_embedded.isChecked()
            and res.seeds_marked > res.seeds_usable
        ):
            self.photo_list.insertItem(
                0,
                f"⚠ {res.seeds_marked - res.seeds_usable} référence(s) marquée(s) "
                f"mais non analysée(s) → repli JPEG boîtier. Lancez « Marquer + "
                f"analyser références » sur vos repères d'abord.",
            )

        for adj in res.adjustments[:10]:
            self.photo_list.addItem(self._format_adjustment(adj))
        if len(res.adjustments) > 10:
            self.photo_list.addItem(f"  … +{len(res.adjustments) - 10} photo(s)")

        if not res.adjustments:
            self._set_actions_enabled(True)
            self._progress_done()
            self.status_label.setText(
                "Aucune correction nécessaire — photos conformes au profil "
                "(ou aucune cible exploitable, cf. notes)."
            )
            return

        if self._op == "preview":
            self._pending_adjustments = res.adjustments
            self._pending_ids = frozenset(p.photo_id for p in self._photos)
            self._set_actions_enabled(True)
            self._progress_done()
            self.status_label.setText(
                f"Aperçu prêt — {len(res.adjustments)} correction(s). "
                f"Cliquez « Appliquer » pour valider."
            )
            return

        # op == "apply" : appliquer directement le plan qu'on vient de calculer
        # (la barre reste affichée jusqu'à la fin de l'application par le plugin).
        self._submit_apply(res.adjustments)

    # ------------------------------------------------------------------ #
    # Appliquer — plan d'Aperçu réutilisé, sinon mesure+plan+apply
    # ------------------------------------------------------------------ #
    def _on_apply_click(self) -> None:
        if not self._require_bridge():
            return
        if self._pending_adjustments is not None:
            # Revue Fable 5 B-01 : ne pas rejouer un plan d'Aperçu si la sélection
            # Lr a changé entre-temps (apply partiel/incohérent sinon). On re-fetch
            # la sélection et on compare à `_pending_ids` avant de soumettre.
            self._set_actions_enabled(False)
            self._progress_busy()
            self.status_label.setText("Vérification de la sélection avant application…")
            self._worker = JobWorker(JobType.GET_SELECTED_PHOTOS)
            self._worker.finished_result.connect(self._on_apply_selection_check)
            self._worker.failed.connect(self._on_auto_failed)
            self._worker.start()
        else:
            self._begin("apply")

    def _on_apply_selection_check(self, result: JobResult) -> None:
        adjustments = self._pending_adjustments
        pending_ids = self._pending_ids
        self._pending_adjustments = None
        self._pending_ids = frozenset()
        if adjustments is None:  # défensif : plan consommé entre-temps
            self._set_actions_enabled(True)
            self._progress_done()
            self.status_label.setText("Plan d'Aperçu introuvable — relancez Aperçu.")
            return
        current_ids = frozenset(p.photo_id for p in result.photos)
        if current_ids != pending_ids:
            self.status_label.setText(
                "Sélection modifiée depuis l'Aperçu — nouvelle mesure + planification…"
            )
            self._begin("apply")
            return
        self._submit_apply(adjustments)

    def _submit_apply(self, adjustments: list) -> None:
        if not adjustments:
            self._set_actions_enabled(True)
            self._progress_done()
            self.status_label.setText("Rien à appliquer.")
            return
        self._progress_busy()
        self.status_label.setText(
            f"Application — {len(adjustments)} photo(s) dans Lightroom…"
        )
        payload = {"adjustments": [a.model_dump() for a in adjustments]}
        # Timeout ∝ n (revue Fable 5 B-05) : le plugin applique par lots de 50 avec
        # heartbeat, mais une grosse sélection dépasse largement 180 s au total.
        timeout = max(180.0, len(adjustments) * 2.0)
        self._apply_worker = JobWorker(JobType.APPLY_ADJUSTMENTS, payload, timeout=timeout)
        self._apply_worker.finished_result.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_auto_failed)
        self._apply_worker.start()

    def _on_apply_done(self, result: JobResult) -> None:
        self._set_actions_enabled(True)
        self._progress_done()
        applied = result.applied if result.applied is not None else "?"
        total = result.total if result.total is not None else "?"
        if result.status == "ok":
            msg = f"Appliqué : {applied}/{total} photo(s) — vérifiez le rendu dans Lightroom."
            if result.errors_summary:
                # Apply partiel (revue Fable 5 L-04) : causes d'échec affichées.
                msg += f" Échecs : {result.errors_summary}"
                self.photo_list.insertItem(0, f"⚠ Apply partiel — {result.errors_summary}")
            self.status_label.setText(msg)
        else:
            self.status_label.setText(f"Application : {applied}/{total} — {result.error}")

    def _on_auto_failed(self, message: str) -> None:
        self._set_actions_enabled(True)
        self._progress_done()
        self.status_label.setText(f"Erreur : {message}")
