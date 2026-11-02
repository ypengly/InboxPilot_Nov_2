"""Application entry point."""
from __future__ import annotations

import logging
import sys

from .logging_setup import setup_logging

log = logging.getLogger(__name__)

STYLE = """
QFrame#statCard { border: 1px solid palette(mid); border-radius: 8px; background: palette(base); }
QLabel#statValue { font-size: 24px; font-weight: 600; }
QLabel#welcomeTitle { font-size: 26px; font-weight: 600; }
QListWidget#folders { border: none; background: transparent; font-size: 14px; }
QListWidget#folders::item { padding: 7px 8px; border-radius: 6px; }
QListWidget#folders::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QTableView { border: 1px solid palette(mid); }
"""


def main() -> int:
    setup_logging()
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print("PySide6 is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    from .config import APP_NAME
    from .services.context import AppContext
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)

    def excepthook(exc_type, exc, tb):
        log.error("Unhandled exception", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(None, APP_NAME, f"Unexpected error: {exc}\n\nDetails were written to the log file.")

    sys.excepthook = excepthook
    try:
        ctx = AppContext()
    except Exception as exc:
        log.exception("Startup failed")
        QMessageBox.critical(None, APP_NAME, getattr(exc, "user_message", f"Could not start: {exc}"))
        return 1
    window = MainWindow(ctx)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
