"""Point d'entrée — démarre le serveur FastAPI (thread) + le GUI PySide6 (thread principal).

Lancement :
    python -m app.main          (depuis ABELr/)
    ou  cd app && python main.py
"""

from __future__ import annotations

import logging
import sys
import threading

import uvicorn


class _PollFilter(logging.Filter):
    """Supprime les logs de polling /jobs/pending (204) — trop verbeux."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not ("GET /jobs/pending" in msg and "204" in msg)

HOST = "127.0.0.1"
PORT = 5000


def _run_server() -> None:
    """Serveur uvicorn — bloquant, donc lancé dans un thread daemon.

    Toute exception au démarrage (port déjà occupé par un process Python orphelin,
    etc.) est rendue visible : sinon le thread meurt en silence et le pont paraît
    « cassé » côté plugin alors que la vraie cause est que le serveur n'a jamais
    écouté.
    """
    from app.server.api import app

    logging.getLogger("uvicorn.access").addFilter(_PollFilter())
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    except Exception as exc:
        import traceback

        print(
            f"\n[ABELr] ERREUR : le serveur HTTP n'a pas pu démarrer sur "
            f"{HOST}:{PORT}.\n  Cause : {exc!r}\n  Un process python.exe orphelin "
            f"occupe-t-il déjà le port {PORT} ? (Gestionnaire des tâches → terminer "
            f"python.exe, puis relancer)\n",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc()


def main() -> int:
    # Serveur HTTP dans un thread daemon (s'arrête avec le GUI).
    server_thread = threading.Thread(target=_run_server, daemon=True, name="fastapi")
    server_thread.start()

    # GUI sur le thread principal (requis par Qt).
    from PySide6.QtWidgets import QApplication

    from app.gui.main_window import MainWindow

    qt_app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return qt_app.exec()


if __name__ == "__main__":
    # Permet `python app/main.py` en ajoutant la racine projet au path.
    if __package__ in (None, ""):
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
