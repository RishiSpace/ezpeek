from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .windows.main_window import MainWindow


def run_gui(argv: list[str] | None = None, *, test_pattern: bool = False) -> int:
    if argv is None:
        argv = []

    app = QApplication(["ezpeek", *argv])

    w = MainWindow(test_pattern=test_pattern)
    w.show()

    return app.exec()
