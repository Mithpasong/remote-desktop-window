"""설정.

우선순위 (뒤가 앞을 덮어씀):
  1. 아래 DEFAULTS
  2. exe(또는 main.py)와 같은 폴더의 config.json  -> 배포 시 미리 지정
  3. %APPDATA%\\WindowRemote\\config.json        -> PC 별 설정
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_PORT = 21500

DEFAULTS: dict = {
    "port": DEFAULT_PORT,  # 접속 대기 포트 (TCP, 검색용 UDP). 모든 PC 가 같은 값을 써야 한다
    "fps": 60,  # 최대 화면 전송 FPS
    "max_pixels": 2560 * 1440,  # 이보다 큰 화면은 비율을 유지하며 축소해 전송
    "crf": 23,  # 화질 (낮을수록 고화질, 18~30 권장)
    "max_bitrate_kbps": 15000,  # 최대 전송 비트레이트
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "WindowRemote" / "config.json"


def _read(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k in DEFAULTS}
    except (OSError, ValueError):
        pass
    return {}


def load() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(_read(app_dir() / "config.json"))
    cfg.update(_read(user_config_path()))
    return cfg


def save(cfg: dict) -> None:
    path = user_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
