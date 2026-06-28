"""Point d'entrée — démarre le serveur FastAPI (thread) + le GUI PySide6 (thread principal).

Lancement :
    python -m app.main          (depuis Lr_automation/)
    ou  cd app && python main.py
"""

from __future__ import annotations

import sys
import threading

import uvicorn

HOST = "127.0.0.1"
PORT = 5000


def _run_server() -> None:
    """Serveur uvicorn — bloquant, donc lancé dans un thread daemon."""
    # import différé : évite de charger FastAPI avant le fork du thread
    from app.server.api import app

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


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
