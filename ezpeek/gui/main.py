from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..cloud import CloudClient, CloudError, load_session, clear_session, get_saved_server_url
from .windows.login_window import LoginWindow
from .windows.main_window import MainWindow


def run_gui(argv: list[str] | None = None, *, test_pattern: bool = False) -> int:
    if argv is None:
        argv = []

    app = QApplication(["ezpeek", *argv])

    # Try restore session (token + server from last login)
    session = load_session()
    client: CloudClient | None = None
    if session and session.get("token") and (session.get("server_url") or get_saved_server_url()):
        client = CloudClient(
            base_url=session.get("server_url") or get_saved_server_url() or "",
            token=session["token"],
        )
        try:
            client.me()
        except CloudError:
            clear_session()
            client = None

    state: dict = {"main": None, "login": None}

    def show_main(c: CloudClient):
        if state["login"]:
            state["login"].close()
            state["login"] = None
        w = MainWindow(test_pattern=test_pattern, cloud=c)
        state["main"] = w
        w.show()

    if client:
        show_main(client)
    else:
        # Prefill server field from last successful use (settings.json), if any
        login = LoginWindow(server_url=get_saved_server_url() or "")
        state["login"] = login
        login.authenticated.connect(show_main)
        login.show()

    return app.exec()
