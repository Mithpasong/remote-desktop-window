"""H.264 인코더/디코더 (PyAV / FFmpeg)."""

from __future__ import annotations

import math
from fractions import Fraction

import av
import numpy as np


def fit_size(width: int, height: int, max_pixels: int) -> tuple[int, int]:
    """max_pixels 를 넘지 않도록 비율을 유지하며 줄인 짝수 크기."""
    scale = min(1.0, math.sqrt(max_pixels / float(width * height))) if max_pixels > 0 else 1.0
    w = max(2, int(width * scale) // 2 * 2)
    h = max(2, int(height * scale) // 2 * 2)
    return w, h


class H264Encoder:
    def __init__(self, src_width: int, src_height: int, fps: int, max_pixels: int, crf: int, max_kbps: int):
        self.src_size = (src_width, src_height)
        self.size = fit_size(src_width, src_height, max_pixels)
        self.fps = fps
        self.crf = crf
        self.max_kbps = max_kbps
        self._ctx = self._create()
        self._pts = 0

    def _create(self) -> av.CodecContext:
        ctx = av.CodecContext.create("libx264", "w")
        ctx.width, ctx.height = self.size
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = Fraction(1, self.fps)
        ctx.framerate = Fraction(self.fps, 1)
        ctx.gop_size = self.fps * 10
        ctx.max_b_frames = 0
        bufsize = max(500, self.max_kbps // 4)
        ctx.options = {
            "preset": "ultrafast",
            "tune": "zerolatency",
            "crf": str(self.crf),
            "x264-params": f"vbv-maxrate={self.max_kbps}:vbv-bufsize={bufsize}",
        }
        return ctx

    def force_keyframe(self) -> None:
        # 인코더를 새로 만들면 다음 프레임은 항상 키프레임(IDR)이 된다.
        self._ctx = self._create()

    def encode(self, bgra: np.ndarray) -> list[bytes]:
        frame = av.VideoFrame.from_ndarray(bgra, format="bgra")
        frame = frame.reformat(width=self.size[0], height=self.size[1], format="yuv420p", interpolation="BILINEAR")
        frame.pts = self._pts
        self._pts += 1
        return [bytes(p) for p in self._ctx.encode(frame)]


class H264Decoder:
    def __init__(self) -> None:
        self._ctx = av.CodecContext.create("h264", "r")
        try:
            self._ctx.thread_type = "SLICE"  # 프레임 스레딩은 지연을 늘리므로 사용하지 않음
        except Exception:
            pass

    def decode(self, data: bytes) -> list[av.VideoFrame]:
        return list(self._ctx.decode(av.Packet(data)))
