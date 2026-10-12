"""Client(원격 받는 쪽) 세션: 화면을 캡처/인코딩해 보내고, 받은 입력을 주입한다."""

from __future__ import annotations

import json
import logging
import threading
import time

from . import protocol as P
from .capture import ScreenCapture, primary_rect
from .input_inject import InputInjector
from .protocol import Connection, ConnectionClosed
from .video import H264Encoder

log = logging.getLogger(__name__)


class ClientSession:
    def __init__(self, conn: Connection, peer_id: str, cfg: dict, on_end):
        self.conn = conn
        self.peer_id = peer_id
        self.fps = max(1, min(int(cfg.get("fps", 60)), 120))
        self.max_pixels = int(cfg.get("max_pixels", 2560 * 1440))
        self.crf = int(cfg.get("crf", 23))
        self.max_kbps = int(cfg.get("max_bitrate_kbps", 15000))
        self._on_end = on_end
        self._running = threading.Event()
        self._keyframe = threading.Event()
        self._ended = threading.Lock()
        self._end_reason: str | None = None
        self._rect = primary_rect()
        self._injector = InputInjector(lambda: self._rect)

    def start(self) -> None:
        self._running.set()
        threading.Thread(target=self._stream_loop, name="client-stream", daemon=True).start()
        threading.Thread(target=self._recv_loop, name="client-recv", daemon=True).start()

    def stop(self) -> None:
        if self._running.is_set():
            try:
                self.conn.send(P.MSG_BYE)
            except ConnectionClosed:
                pass
        self._finish("원격 지원을 종료했습니다.")

    def _finish(self, reason: str) -> None:
        with self._ended:
            if self._end_reason is not None:
                return
            self._end_reason = reason
        self._running.clear()
        self.conn.close()
        try:
            self._injector.release_all()
        except Exception:
            log.exception("release_all failed")
        log.info("client session ended: %s", reason)
        self._on_end(reason)

    # --- 화면 전송 -----------------------------------------------------------

    def _stream_loop(self) -> None:
        cap = None
        try:
            cap = ScreenCapture()
            log.info("capture backend: %s, rect=%s", cap.backend, cap.rect)
            encoder: H264Encoder | None = None
            last = None
            interval = 1.0 / self.fps
            next_tick = time.perf_counter()

            while self._running.is_set():
                frame = cap.grab()
                self._rect = cap.rect
                if frame is not None:
                    last = frame
                want_keyframe = self._keyframe.is_set()

                if last is not None and (frame is not None or want_keyframe or encoder is None):
                    h, w = last.shape[:2]
                    if encoder is None or encoder.src_size != (w, h):
                        encoder = H264Encoder(w, h, self.fps, self.max_pixels, self.crf, self.max_kbps)
                        hello = {"width": encoder.size[0], "height": encoder.size[1],
                                 "screen_width": w, "screen_height": h,
                                 "fps": self.fps, "capture": cap.backend}
                        self.conn.send(P.MSG_HELLO, json.dumps(hello).encode())
                        log.info("streaming %s", hello)
                    elif want_keyframe:
                        encoder.force_keyframe()
                    self._keyframe.clear()
                    for packet in encoder.encode(last):
                        self.conn.send(P.MSG_VIDEO, packet)

                next_tick += interval
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.perf_counter()
        except ConnectionClosed:
            self._finish("상대방과의 연결이 끊어졌습니다.")
        except Exception as exc:
            log.exception("stream loop failed")
            self._finish(f"화면 전송 중 오류가 발생했습니다: {exc}")
        finally:
            if cap is not None:
                cap.close()

    # --- 입력 수신 -----------------------------------------------------------

    def _recv_loop(self) -> None:
        inj = self._injector
        try:
            while self._running.is_set():
                mtype, payload = self.conn.recv()
                if mtype == P.MSG_MOUSE_MOVE:
                    inj.mouse_move(*P.MOUSE_MOVE.unpack(payload))
                elif mtype == P.MSG_MOUSE_BUTTON:
                    button, down = P.MOUSE_BUTTON.unpack(payload)
                    inj.mouse_button(button, bool(down))
                elif mtype == P.MSG_MOUSE_WHEEL:
                    delta, horizontal = P.MOUSE_WHEEL.unpack(payload)
                    inj.mouse_wheel(delta, bool(horizontal))
                elif mtype == P.MSG_KEY:
                    vk, scan, flags = P.KEY.unpack(payload)
                    inj.key(vk, scan, bool(flags & P.KEY_FLAG_EXTENDED), bool(flags & P.KEY_FLAG_DOWN))
                elif mtype == P.MSG_KEYFRAME_REQ:
                    self._keyframe.set()
                elif mtype == P.MSG_BYE:
                    self._finish("상대방이 원격 지원을 종료했습니다.")
                    return
        except ConnectionClosed:
            self._finish("상대방과의 연결이 끊어졌습니다.")
        except Exception as exc:
            log.exception("recv loop failed")
            self._finish(f"입력 처리 중 오류가 발생했습니다: {exc}")
