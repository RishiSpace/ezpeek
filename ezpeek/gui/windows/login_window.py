from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QMessageBox,
    QFormLayout,
    QFrame,
)

from ...cloud import (
    CloudClient,
    CloudError,
    save_session,
    get_saved_server_url,
    save_server_url,
)


class LoginWindow(QWidget):
    """Login / register gate before MainWindow."""

    authenticated = Signal(object)  # CloudClient

    def __init__(self, server_url: str = "", parent=None):
        super().__init__(parent)
        # Prefer explicit arg, else last saved URL (empty on first run)
        self.server_url = (server_url or get_saved_server_url() or "").strip()
        self.client = CloudClient(base_url=self.server_url) if self.server_url else CloudClient(base_url="")
        self.setWindowTitle("EzPeek — Sign in")
        self.setMinimumSize(420, 380)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel("EzPeek")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: 600; color: white;")
        sub = QLabel("Enter your cloud server URL, then sign in or create an account.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #aaa;")
        sub.setWordWrap(True)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._login_page())
        self.stack.addWidget(self._register_page())

        self.server_edit = QLineEdit(self.server_url)
        self.server_edit.setPlaceholderText("http://your-server:8787")
        server_row = QHBoxLayout()
        server_row.addWidget(QLabel("Server:"))
        server_row.addWidget(self.server_edit)

        root.addWidget(title)
        root.addWidget(sub)
        root.addSpacing(8)
        root.addLayout(server_row)
        root.addWidget(self.stack)
        self.setStyleSheet(
            """
            QWidget { background: #0b0b0b; color: #eee; }
            QLineEdit {
                background: #1a1a1a; border: 1px solid #333; padding: 8px;
                border-radius: 4px; color: white;
            }
            QPushButton {
                background: #2d6cdf; color: white; padding: 10px;
                border: none; border-radius: 4px; font-weight: 600;
            }
            QPushButton:hover { background: #3d7cef; }
            QPushButton#link {
                background: transparent; color: #8ab4ff; font-weight: normal;
            }
            """
        )

    def _login_page(self) -> QWidget:
        w = QFrame()
        form = QFormLayout(w)
        self.login_user = QLineEdit()
        self.login_user.setPlaceholderText("username or email")
        self.login_pass = QLineEdit()
        self.login_pass.setEchoMode(QLineEdit.Password)
        self.login_pass.setPlaceholderText("password")
        btn = QPushButton("Sign in")
        btn.clicked.connect(self._do_login)
        switch = QPushButton("Create an account")
        switch.setObjectName("link")
        switch.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        form.addRow("Login", self.login_user)
        form.addRow("Password", self.login_pass)
        form.addRow(btn)
        form.addRow(switch)
        return w

    def _register_page(self) -> QWidget:
        w = QFrame()
        form = QFormLayout(w)
        self.reg_user = QLineEdit()
        self.reg_email = QLineEdit()
        self.reg_pass = QLineEdit()
        self.reg_pass.setEchoMode(QLineEdit.Password)
        btn = QPushButton("Register")
        btn.clicked.connect(self._do_register)
        switch = QPushButton("Back to sign in")
        switch.setObjectName("link")
        switch.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        form.addRow("Username", self.reg_user)
        form.addRow("Email", self.reg_email)
        form.addRow("Password", self.reg_pass)
        form.addRow(btn)
        form.addRow(switch)
        return w

    def _apply_server(self) -> bool:
        url = self.server_edit.text().strip().rstrip("/")
        if not url:
            QMessageBox.warning(
                self,
                "Server required",
                "Enter your ezpeek cloud server URL first\n"
                "(e.g. http://your-server:8787).",
            )
            return False
        if "://" not in url:
            url = "http://" + url
        self.server_url = url
        save_server_url(url)
        self.client = CloudClient(base_url=url)
        return True

    def _do_login(self):
        if not self._apply_server():
            return
        try:
            self.client.login(self.login_user.text().strip(), self.login_pass.text())
            save_session(
                {
                    "token": self.client.token,
                    "user": self.client.user,
                    "server_url": self.server_url,
                }
            )
            self.authenticated.emit(self.client)
        except CloudError as e:
            QMessageBox.warning(self, "Sign in failed", str(e))

    def _do_register(self):
        if not self._apply_server():
            return
        try:
            self.client.register(
                self.reg_user.text().strip(),
                self.reg_email.text().strip(),
                self.reg_pass.text(),
            )
            save_session(
                {
                    "token": self.client.token,
                    "user": self.client.user,
                    "server_url": self.server_url,
                }
            )
            self.authenticated.emit(self.client)
        except CloudError as e:
            QMessageBox.warning(self, "Register failed", str(e))
