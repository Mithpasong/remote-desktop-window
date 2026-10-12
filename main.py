"""Window Remote 실행 진입점."""

import logging
import logging.handlers
import sys

from remote import APP_NAME, __version__
from remote.config import user_config_path


def setup_logging() -> None:
    handlers: list[logging.Handler] = []
    log_path = user_config_path().with_name("window_remote.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:  # --windowed exe 에는 콘솔이 없다
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)

    def excepthook(exc_type, exc, tb):
        logging.getLogger("main").critical("unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook


def selftest() -> int:
    """이 PC 에서 화면 캡처/인코딩/디코딩이 되는지 점검한다. (WindowRemote.exe --selftest)"""
    import time

    from remote.capture import ScreenCapture
    from remote.video import H264Decoder, H264Encoder

    log = logging.getLogger("selftest")
    try:
        cap = ScreenCapture()
        frame = None
        deadline = time.time() + 3
        while frame is None and time.time() < deadline:
            frame = cap.grab()
        if frame is None:
            raise RuntimeError("화면을 캡처하지 못했습니다.")
        h, w = frame.shape[:2]
        enc = H264Encoder(w, h, 60, 2560 * 1440, 23, 15000)
        dec = H264Decoder()
        start = time.perf_counter()
        decoded = 0
        for _ in range(30):
            for packet in enc.encode(frame):
                decoded += len(dec.decode(packet))
        per_frame = (time.perf_counter() - start) / 30 * 1000
        cap.close()
        result = (f"OK  capture={cap.backend} screen={w}x{h} stream={enc.size[0]}x{enc.size[1]} "
                  f"decoded={decoded}/30 encode+decode={per_frame:.1f}ms/frame")
        log.info(result)
        code = 0 if decoded == 30 else 1
    except Exception as exc:
        log.exception("selftest failed")
        result, code = f"FAIL  {exc}", 1

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    QMessageBox.information(None, f"{APP_NAME} {__version__} 자가 진단", result)
    return code


def main() -> int:
    setup_logging()
    logging.getLogger("main").info("%s %s starting", APP_NAME, __version__)

    if "--selftest" in sys.argv:
        return selftest()

    from PySide6.QtWidgets import QApplication

    from remote.gui import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
