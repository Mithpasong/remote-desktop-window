"""Agent(원격 하는 쪽) 뷰어 창: 원격 화면을 표시하고 마우스/키보드 입력을 보낸다."""

from __future__ import annotations

import json
import logging
import threading
import time

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QPainter
from PySide6.QtWidgets import QLabel, QMainWindow, QMessageBox, QToolBar, QWidget

from . import APP_NAME
from . import protocol as P
from .keyhook import KeyboardHook, foreground_window
from .protocol import Connection, ConnectionClosed
from .video import H264Decoder

log = logging.getLogger(__name__)

_QT_BUTTONS = {
    Qt.MouseButton.LeftButton: P.BUTTON_LEFT,
    Qt.MouseButton.RightButton: P.BUTTON_RIGHT,
    Qt.MouseButton.MiddleButton: P.BUTTON_MIDDLE,
    Qt.MouseButton.BackButton: P.BUTTON_X1,
    Qt.MouseButton.ForwardButton: P.BUTTON_X2,
}


def _fit(src_w: int, src_h: int, box_w: float, box_h: float) -> tuple[float, float]:
    if src_w <= 0 or src_h <= 0 or box_w <= 0 or box_h <= 0:
        return 0.0, 0.0
    scale = min(box_w / src_w, box_h / src_h)
    return src_w * scale, src_h * scale


class VideoReceiver(QObject):
    """백그라운드 스레드에서 영상을 받아 디코딩하고, 표시할 크기로 변환해 GUI 로 넘긴다."""

    frame_ready = Signal(QImage, int, int)
    hello = Signal(dict)
    closed = Signal(str)

    def __init__(self, conn: Connection, target_size):
        super().__init__()
        self.conn = conn
        self._target_size = target_size  # () -> (width, height) 표시 영역의 실제 픽셀 크기
        self.bytes_received = 0
        self.frames_decoded = 0
        self._thread = threading.Thread(target=self._run, name="viewer-recv", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        decoder = H264Decoder()
        try:
            while True:
                mtype, payload = self.conn.recv()
                if mtype == P.MSG_VIDEO:
                    self.bytes_received += len(payload) + P.HEADER.size
                    try:
                        frames = decoder.decode(payload)
                    except Exception as exc:
                        log.warning("decode error: %s", exc)
                        decoder = H264Decoder()
                        self.conn.send(P.MSG_KEYFRAME_REQ)
                        continue
                    for frame in frames:
                        self._emit_frame(frame)
                elif mtype == P.MSG_HELLO:
                    self.hello.emit(json.loads(payload))
                elif mtype == P.MSG_BYE:
                    self.closed.emit("상대방이 원격 지원을 종료했습니다.")
                    return
        except ConnectionClosed:
            self.closed.emit("상대방과의 연결이 끊어졌습니다.")
        except Exception as exc:
            log.exception("viewer receive failed")
            self.closed.emit(f"오류가 발생했습니다: {exc}")

    def _emit_frame(self, frame) -> None:
        box_w, box_h = self._target_size()
        w, h = _fit(frame.width, frame.height, box_w, box_h)
        w, h = max(2, int(w)), max(2, int(h))
        plane = frame.reformat(width=w, height=h, format="rgb24", interpolation="BILINEAR").planes[0]
        # 줄마다 정렬용 여백이 있을 수 있으므로 line_size 를 그대로 넘긴다.
        image = QImage(bytes(plane), w, h, plane.line_size, QImage.Format.Format_RGB888).copy()
        self.frames_decoded += 1
        self.frame_ready.emit(image, frame.width, frame.height)


class VideoWidget(QWidget):
    def __init__(self, send):
        super().__init__()
        self._send = send
        self._image: QImage | None = None
        self._src = (0, 0)
        self._target = (1280, 720)
        self._last_move: tuple[int, int] | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(320, 180)

    def target_size(self) -> tuple[int, int]:
        return self._target  # 수신 스레드에서 읽음 (튜플 교체는 원자적)

    def resizeEvent(self, event) -> None:
        dpr = self.devicePixelRatioF()
        self._target = (max(2, int(self.width() * dpr)), max(2, int(self.height() * dpr)))
        super().resizeEvent(event)

    def set_frame(self, image: QImage, src_w: int, src_h: int) -> None:
        image.setDevicePixelRatio(self.devicePixelRatioF())
        self._image = image
        self._src = (src_w, src_h)
        self.update()

    def _image_rect(self) -> QRectF:
        w, h = _fit(self._src[0], self._src[1], self.width(), self.height())
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self._image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(self._image_rect(), self._image)
        else:
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "원격 화면을 기다리는 중...")
        painter.end()

    # --- 마우스 ---------------------------------------------------------------

    def _normalize(self, pos) -> tuple[int, int, bool]:
        r = self._image_rect()
        if r.width() <= 0 or r.height() <= 0:
            return 0, 0, False
        fx = (pos.x() - r.x()) / r.width()
        fy = (pos.y() - r.y()) / r.height()
        inside = 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0
        nx = int(min(max(fx, 0.0), 1.0) * 65535)
        ny = int(min(max(fy, 0.0), 1.0) * 65535)
        return nx, ny, inside

    def _send_move(self, pos, force: bool = False) -> bool:
        nx, ny, inside = self._normalize(pos)
        if not inside and not force:
            return False
        if (nx, ny) != self._last_move:
            self._last_move = (nx, ny)
            self._send(P.MSG_MOUSE_MOVE, P.MOUSE_MOVE.pack(nx, ny))
        return inside

    def mouseMoveEvent(self, event) -> None:
        self._send_move(event.position(), force=bool(event.buttons()))

    def mousePressEvent(self, event) -> None:
        button = _QT_BUTTONS.get(event.button())
        if button is not None and self._image is not None and self._send_move(event.position()):
            self._send(P.MSG_MOUSE_BUTTON, P.MOUSE_BUTTON.pack(button, 1))

    # Qt 는 두 번째 클릭을 press 대신 double-click 으로 보낸다.
    mouseDoubleClickEvent = mousePressEvent

    def mouseReleaseEvent(self, event) -> None:
        button = _QT_BUTTONS.get(event.button())
        if button is not None and self._image is not None:
            self._send_move(event.position(), force=True)
            self._send(P.MSG_MOUSE_BUTTON, P.MOUSE_BUTTON.pack(button, 0))

    def wheelEvent(self, event) -> None:
        if self._image is None:
            return
        delta = event.angleDelta()
        if delta.y():
            self._send(P.MSG_MOUSE_WHEEL, P.MOUSE_WHEEL.pack(max(-32768, min(32767, delta.y())), 0))
        if delta.x():
            self._send(P.MSG_MOUSE_WHEEL, P.MOUSE_WHEEL.pack(max(-32768, min(32767, -delta.x())), 1))
        event.accept()

    def focusNextPrevChild(self, next_: bool) -> bool:
        return False  # Tab 키로 포커스가 이동하지 않도록


class ViewerWindow(QMainWindow):
    closed_signal = Signal(object)

    def __init__(self, conn: Connection, peer_id: str):
        super().__init__()
        self.conn = conn
        self.peer_id = peer_id
        self._closing = False
        self._pressed: dict[int, tuple[int, bool]] = {}

        self.setWindowTitle(f"{peer_id} - {APP_NAME}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.video = VideoWidget(self._send)
        self.setCentralWidget(self.video)

        toolbar = QToolBar("도구")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self._fullscreen_action = QAction("전체 화면", self)
        self._fullscreen_action.setCheckable(True)
        self._fullscreen_action.triggered.connect(self._toggle_fullscreen)
        toolbar.addAction(self._fullscreen_action)
        refresh = QAction("화면 새로고침", self)
        refresh.triggered.connect(lambda: self._send(P.MSG_KEYFRAME_REQ))
        toolbar.addAction(refresh)
        disconnect = QAction("연결 종료", self)
        disconnect.triggered.connect(self.close)
        toolbar.addAction(disconnect)

        self._stats = QLabel("연결됨")
        self.statusBar().addPermanentWidget(self._stats)
        self._remote_info = ""

        self.receiver = VideoReceiver(conn, self.video.target_size)
        self.receiver.frame_ready.connect(self.video.set_frame)
        self.receiver.hello.connect(self._on_hello)
        self.receiver.closed.connect(self._on_remote_closed)

        self._last_stats = (time.perf_counter(), 0, 0)
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(1000)

        self._hook = KeyboardHook(self._on_hook_key)
        self._hook.install()

        screen = self.screen().availableGeometry()
        self.resize(int(screen.width() * 0.75), int(screen.height() * 0.8))
        self.receiver.start()

    # --- 송신 ---------------------------------------------------------------

    def _send(self, mtype: int, payload: bytes = b"") -> None:
        if self._closing:
            return
        try:
            self.conn.send(mtype, payload)
        except ConnectionClosed:
            pass  # 수신 스레드가 closed 시그널로 처리

    # --- 키보드 -------------------------------------------------------------

    def _is_active(self) -> bool:
        return not self._closing and foreground_window() == int(self.winId())

    def _on_hook_key(self, vk: int, scan: int, extended: bool, down: bool) -> bool:
        if self._is_active():
            if down:
                self._pressed[vk] = (scan, extended)
            else:
                self._pressed.pop(vk, None)
            self._send_key(vk, scan, extended, down)
            return True  # 로컬 PC 에는 전달하지 않음 (Win 키, Alt+Tab 등 포함)
        if not down and vk in self._pressed:
            # 창 밖에서 뗀 키: 원격에서 눌린 채로 남지 않도록 떼기만 전달
            self._pressed.pop(vk, None)
            self._send_key(vk, scan, extended, False)
        return False

    def _send_key(self, vk: int, scan: int, extended: bool, down: bool) -> None:
        flags = (P.KEY_FLAG_DOWN if down else 0) | (P.KEY_FLAG_EXTENDED if extended else 0)
        self._send(P.MSG_KEY, P.KEY.pack(vk & 0xFFFF, scan & 0xFFFF, flags))

    def _release_keys(self) -> None:
        for vk, (scan, extended) in list(self._pressed.items()):
            self._send_key(vk, scan, extended, False)
        self._pressed.clear()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self._release_keys()
        super().changeEvent(event)

    # --- UI -----------------------------------------------------------------

    def _toggle_fullscreen(self, checked: bool) -> None:
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

    def _on_hello(self, info: dict) -> None:
        sw, sh = info.get("screen_width"), info.get("screen_height")
        w, h = info.get("width"), info.get("height")
        self._remote_info = f"원격 화면 {sw}x{sh}" + (f" (전송 {w}x{h})" if (w, h) != (sw, sh) else "")

    def _update_stats(self) -> None:
        now = time.perf_counter()
        t0, frames0, bytes0 = self._last_stats
        frames, nbytes = self.receiver.frames_decoded, self.receiver.bytes_received
        dt = max(now - t0, 1e-6)
        fps = (frames - frames0) / dt
        mbps = (nbytes - bytes0) * 8 / dt / 1_000_000
        self._last_stats = (now, frames, nbytes)
        self._stats.setText(f"{self._remote_info}   |   {fps:.0f} FPS   |   {mbps:.1f} Mbps")

    def _on_remote_closed(self, reason: str) -> None:
        if self._closing:
            return
        self._shutdown()
        QMessageBox.information(self, APP_NAME, f"{self.peer_id}\n\n{reason}")
        self.close()

    def _shutdown(self) -> None:
        if self._closing:
            return
        self._release_keys()
        self._closing = True
        self._hook.uninstall()
        self._stats_timer.stop()
        try:
            self.conn.send(P.MSG_BYE)
        except ConnectionClosed:
            pass
        self.conn.close()

    def closeEvent(self, event) -> None:
        self._shutdown()
        self.closed_signal.emit(self)
        super().closeEvent(event)
