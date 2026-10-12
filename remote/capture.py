"""화면 캡처: DXGI Desktop Duplication(dxcam) 우선, 실패 시 GDI(mss) 사용."""

from __future__ import annotations

import ctypes
import logging
import time

import numpy as np

log = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
SM_CXSCREEN, SM_CYSCREEN = 0, 1


def primary_rect() -> tuple[int, int, int, int]:
    """주 모니터 영역 (left, top, width, height). 주 모니터는 항상 (0, 0) 에서 시작한다."""
    return 0, 0, _user32.GetSystemMetrics(SM_CXSCREEN), _user32.GetSystemMetrics(SM_CYSCREEN)


class ScreenCapture:
    """주 모니터를 BGRA numpy 배열로 캡처한다."""

    def __init__(self) -> None:
        self.backend = "none"
        self.rect = primary_rect()
        self._cam = None
        self._mss = None
        self._dxgi_failures = 0
        self._open()

    def _open(self) -> None:
        try:
            import dxcam

            self._cam = dxcam.create(output_color="BGRA")
            self.backend = "dxgi"
            self._update_rect_from_dxcam()
            return
        except Exception as exc:  # GPU 가 없거나 원격 데스크톱 세션 등
            log.warning("DXGI capture unavailable, falling back to GDI: %s", exc)
            self._cam = None
        self._open_mss()

    def _open_mss(self) -> None:
        import mss

        self._mss = mss.mss()
        self.backend = "gdi"

    def _update_rect_from_dxcam(self) -> None:
        try:
            r = self._cam._output.desc.DesktopCoordinates
            self.rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        except Exception:
            self.rect = primary_rect()

    def grab(self) -> np.ndarray | None:
        """새 프레임을 반환한다. 화면 변화가 없으면 None."""
        if self._cam is not None:
            try:
                frame = self._cam.grab()
                self._dxgi_failures = 0
                if frame is not None:
                    h, w = frame.shape[:2]
                    if (w, h) != tuple(self.rect[2:]):
                        self._update_rect_from_dxcam()
                return frame
            except Exception as exc:
                # 잠금 화면/UAC 전환, 해상도 변경 등. dxcam 이 자체 복구를 시도한다.
                self._dxgi_failures += 1
                if self._dxgi_failures == 1:
                    log.warning("DXGI grab failed: %s", exc)
                if self._dxgi_failures > 120:
                    log.warning("DXGI keeps failing, switching to GDI capture")
                    self._release_dxcam()
                    self._open_mss()
                time.sleep(0.05)
                return None

        if self._mss is not None:
            try:
                left, top, width, height = primary_rect()
                self.rect = (left, top, width, height)
                shot = self._mss.grab({"left": left, "top": top, "width": width, "height": height})
                return np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
            except Exception as exc:
                log.warning("GDI grab failed: %s", exc)
                time.sleep(0.1)
        return None

    def _release_dxcam(self) -> None:
        if self._cam is not None:
            try:
                self._cam.release()
            except Exception:
                pass
            self._cam = None

    def close(self) -> None:
        self._release_dxcam()
        if self._mss is not None:
            try:
                self._mss.close()
            except Exception:
                pass
            self._mss = None
