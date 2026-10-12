"""메인 창: 내 ID(Client 기능)와 상대 ID 연결(Agent 기능)을 한 화면에서 제공한다."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__
from . import config as config_mod
from .agent_viewer import ViewerWindow
from .client_session import ClientSession
from .ids import generate_id, is_valid_id, normalize_id
from .network import IncomingRequest, Listener, SessionError, SessionRequest, primary_ip

log = logging.getLogger(__name__)

STYLE = """
QWidget { font-family: 'Malgun Gothic', 'Segoe UI'; font-size: 10pt; }
QGroupBox { font-weight: bold; border: 1px solid #c8ccd2; border-radius: 6px; margin-top: 10px; padding: 10px 8px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLabel#myId { font-family: Consolas, 'Courier New'; font-size: 24pt; font-weight: bold; color: #1a56db; letter-spacing: 2px; }
QLabel#hint { color: #666; font-size: 9pt; }
QPushButton#primary { background: #1a56db; color: white; border: none; border-radius: 4px; padding: 6px 16px; font-weight: bold; }
QPushButton#primary:disabled { background: #9db5ef; }
QLineEdit#partnerId { font-family: Consolas, 'Courier New'; font-size: 14pt; padding: 4px; }
"""

INCOMING_TIMEOUT_MS = 55_000


class ControlBanner(QWidget):
    """원격 제어를 받는 동안 화면 상단에 항상 표시되는 알림."""

    def __init__(self, on_disconnect):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setStyleSheet("QWidget { background: #c81e1e; color: white; font-family: 'Malgun Gothic'; font-size: 10pt; }"
                           "QPushButton { background: white; color: #c81e1e; border: none; border-radius: 3px; padding: 3px 10px; font-weight: bold; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel()
        layout.addWidget(self.label)
        button = QPushButton("연결 종료")
        button.clicked.connect(on_disconnect)
        layout.addWidget(button)

    def show_for(self, peer_id: str) -> None:
        self.label.setText(f"●  {peer_id} 에서 이 PC 를 원격 제어 중입니다")
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.x() + (screen.width() - self.width()) // 2, screen.y())
        self.show()


class MainWindow(QWidget):
    sig_incoming = Signal(object)  # IncomingRequest
    sig_incoming_cancelled = Signal(object)  # IncomingRequest
    sig_agent_status = Signal(str)
    sig_agent_done = Signal(object, str, str)  # conn | None, target_id, error
    sig_client_ended = Signal(str)

    def __init__(self):
        super().__init__()
        self.cfg = config_mod.load()
        self.port = int(self.cfg["port"])
        self.my_ip = primary_ip()
        self.my_id = generate_id(self.my_ip)
        self.viewers: list[ViewerWindow] = []
        self.client_session: ClientSession | None = None
        self._incoming: IncomingRequest | None = None
        self._incoming_box: QMessageBox | None = None
        self._request: SessionRequest | None = None

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setStyleSheet(STYLE)
        self._build_ui()
        self.banner = ControlBanner(self._disconnect_client)

        self.sig_incoming.connect(self._on_incoming)
        self.sig_incoming_cancelled.connect(self._on_incoming_cancelled)
        self.sig_agent_status.connect(self.agent_status.setText)
        self.sig_agent_done.connect(self._on_agent_done)
        self.sig_client_ended.connect(self._on_client_ended)

        self.listener = Listener(
            self.port,
            get_id=lambda: self.my_id,
            is_busy=lambda: self.client_session is not None or self._incoming is not None,
            on_incoming=self.sig_incoming.emit,
            on_cancel=self.sig_incoming_cancelled.emit,
        )
        self._start_listener()

    # --- UI 구성 ------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Client: 원격 지원 받기
        client_box = QGroupBox("원격 지원 받기 (Client)")
        cl = QVBoxLayout(client_box)
        cl.addWidget(QLabel("내 ID  -  원격 지원을 해 줄 사람에게 이 ID 를 알려주세요."))
        id_row = QHBoxLayout()
        self.id_label = QLabel(self.my_id)
        self.id_label.setObjectName("myId")
        self.id_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        id_row.addWidget(self.id_label)
        id_row.addStretch(1)
        copy_btn = QPushButton("복사")
        copy_btn.clicked.connect(self._copy_id)
        id_row.addWidget(copy_btn)
        cl.addLayout(id_row)
        self.listen_status = QLabel("")
        self.listen_status.setObjectName("hint")
        self.listen_status.setWordWrap(True)
        cl.addWidget(self.listen_status)
        session_row = QHBoxLayout()
        self.client_status = QLabel("상태: 대기 중")
        session_row.addWidget(self.client_status, 1)
        self.client_disconnect_btn = QPushButton("연결 종료")
        self.client_disconnect_btn.setVisible(False)
        self.client_disconnect_btn.clicked.connect(self._disconnect_client)
        session_row.addWidget(self.client_disconnect_btn)
        cl.addLayout(session_row)
        root.addWidget(client_box)

        # Agent: 원격 지원하기
        agent_box = QGroupBox("원격 지원하기 (Agent)")
        al = QVBoxLayout(agent_box)
        al.addWidget(QLabel("상대방 ID"))
        row = QHBoxLayout()
        self.partner_edit = QLineEdit()
        self.partner_edit.setObjectName("partnerId")
        self.partner_edit.setPlaceholderText("10자리 ID 입력")
        self.partner_edit.setMaxLength(14)
        self.partner_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[A-Za-z0-9\s\-]*")))
        self.partner_edit.textEdited.connect(self._on_partner_edited)
        self.partner_edit.returnPressed.connect(self._connect_clicked)
        row.addWidget(self.partner_edit, 1)
        self.connect_btn = QPushButton("연결")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self._connect_clicked)
        row.addWidget(self.connect_btn)
        al.addLayout(row)
        self.agent_status = QLabel("")
        self.agent_status.setObjectName("hint")
        self.agent_status.setWordWrap(True)
        al.addWidget(self.agent_status)
        root.addWidget(agent_box)

        version = QLabel(f"v{__version__}")
        version.setObjectName("hint")
        version.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(version)

        self.setFixedWidth(440)

    # --- Client: 접속 대기 ---------------------------------------------------

    def _start_listener(self) -> None:
        try:
            self.listener.start()
        except OSError as exc:
            log.error("listener failed: %s", exc)
            self.listen_status.setText(
                f"<span style='color:#c81e1e'>●</span> 접속 대기 포트를 열 수 없습니다 "
                f"({self.port}~{self.port + 9}). 원격 지원을 받을 수 없습니다.")
            return
        ip = self.my_ip or "IP 없음"
        self.listen_status.setText(
            f"<span style='color:#0e9f6e'>●</span> 연결 대기 중  (IP {ip}, 포트 {self.listener.port})<br>"
            "처음 실행할 때 Windows 방화벽 창이 뜨면 <b>허용</b>을 눌러 주세요.")

    def _copy_id(self) -> None:
        QGuiApplication.clipboard().setText(self.my_id)
        self.client_status.setText("상태: ID 를 클립보드에 복사했습니다.")
        QTimer.singleShot(2000, self._reset_client_status)

    def _reset_client_status(self) -> None:
        if self.client_session is None and self._incoming is None:
            self.client_status.setText("상태: 대기 중")

    def _on_incoming(self, request: IncomingRequest) -> None:
        if self.client_session is not None or self._incoming is not None:
            request.reject("busy")
            return
        self._incoming = request

        self.showNormal()
        self.raise_()
        self.activateWindow()
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME,
                          f"{request.from_id or '알 수 없는 ID'} ({request.address}) 에서 원격 지원을 요청했습니다.\n\n"
                          "허용하면 상대방이 이 PC 의 화면을 보고 마우스와 키보드를 제어할 수 있습니다.\n"
                          "허용하시겠습니까?", parent=self)
        allow = box.addButton("허용", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("거부", QMessageBox.ButtonRole.RejectRole)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._incoming_box = box
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(box.reject)  # 응답이 없으면 자동 거부
        timer.start(INCOMING_TIMEOUT_MS)
        box.exec()
        timer.stop()
        accepted = box.clickedButton() is allow
        self._incoming_box = None
        box.deleteLater()

        self._incoming = None
        if not accepted:
            request.reject("rejected")
            self._reset_client_status()
            return
        conn = request.accept()
        if conn is None:
            self.client_status.setText("상태: 대기 중  (상대방이 요청을 취소했습니다)")
            return
        peer_id = request.from_id or request.address
        self.client_session = ClientSession(conn, peer_id, self.cfg, on_end=self.sig_client_ended.emit)
        self.client_session.start()
        self.client_status.setText(f"상태: {peer_id} 에서 원격 제어 중")
        self.client_disconnect_btn.setVisible(True)
        self.banner.show_for(peer_id)

    def _on_incoming_cancelled(self, request: IncomingRequest) -> None:
        if request is self._incoming and self._incoming_box is not None:
            self._incoming_box.reject()

    def _disconnect_client(self) -> None:
        if self.client_session is not None:
            self.client_session.stop()

    def _on_client_ended(self, reason: str) -> None:
        self.client_session = None
        self.banner.hide()
        self.client_disconnect_btn.setVisible(False)
        self.client_status.setText(f"상태: 대기 중  ({reason})")

    # --- Agent: 원격 지원하기 ------------------------------------------------

    def _on_partner_edited(self, text: str) -> None:
        upper = text.upper()
        if upper != text:
            pos = self.partner_edit.cursorPosition()
            self.partner_edit.setText(upper)
            self.partner_edit.setCursorPosition(pos)

    def _connect_clicked(self) -> None:
        if self._request is not None:
            self._request.cancel()
            return
        target = normalize_id(self.partner_edit.text())
        if not is_valid_id(target):
            self.agent_status.setText("ID 는 영어 대문자와 숫자로 된 10자리입니다.")
            return
        if target == self.my_id:
            self.agent_status.setText("자기 자신의 ID 로는 연결할 수 없습니다.")
            return
        self.partner_edit.setText(target)
        request = SessionRequest(self.my_id, target, self.port)
        self._request = request
        self.connect_btn.setText("취소")

        def worker():
            try:
                conn = request.run(
                    on_status=self.sig_agent_status.emit,
                    on_waiting=lambda: self.sig_agent_status.emit("상대방의 수락을 기다리는 중..."),
                )
                self.sig_agent_done.emit(conn, target, "")
            except SessionError as exc:
                self.sig_agent_done.emit(None, target, str(exc))

        threading.Thread(target=worker, name="agent-connect", daemon=True).start()

    def _on_agent_done(self, conn, target: str, error: str) -> None:
        self._request = None
        self.connect_btn.setText("연결")
        if conn is None:
            self.agent_status.setText(error)
            return
        self.agent_status.setText(f"{target} 에 연결되었습니다.")
        viewer = ViewerWindow(conn, target)
        viewer.closed_signal.connect(self._on_viewer_closed)
        self.viewers.append(viewer)
        viewer.show()
        viewer.activateWindow()

    def _on_viewer_closed(self, viewer) -> None:
        if viewer in self.viewers:
            self.viewers.remove(viewer)
            self.agent_status.setText(f"{viewer.peer_id} 와의 연결을 종료했습니다.")

    # --- 종료 ---------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self.client_session is not None or self.viewers:
            answer = QMessageBox.question(self, APP_NAME, "진행 중인 원격 연결이 있습니다. 종료하시겠습니까?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for viewer in list(self.viewers):
            viewer.close()
        if self.client_session is not None:
            self.client_session.stop()
        if self._request is not None:
            self._request.cancel()
        self.listener.stop()
        self.banner.close()
        event.accept()
