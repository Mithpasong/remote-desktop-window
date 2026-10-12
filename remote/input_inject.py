"""Windows SendInput 으로 마우스/키보드 입력을 주입한다 (원격 받는 쪽)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from . import protocol as P

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
XBUTTON1, XBUTTON2 = 0x0001, 0x0002

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

_BUTTON_FLAGS = {
    P.BUTTON_LEFT: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    P.BUTTON_RIGHT: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    P.BUTTON_MIDDLE: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    P.BUTTON_X1: (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
    P.BUTTON_X2: (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
}


def _send(inp: INPUT) -> None:
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class InputInjector:
    """원격에서 받은 입력 이벤트를 로컬 PC 에 주입한다.

    rect_provider 는 현재 캡처 중인 화면 영역 (left, top, width, height) 을 반환한다.
    """

    def __init__(self, rect_provider):
        self._rect_provider = rect_provider
        self._keys_down: dict[int, tuple[int, bool]] = {}
        self._buttons_down: set[int] = set()

    def _to_absolute(self, nx: int, ny: int) -> tuple[int, int]:
        left, top, width, height = self._rect_provider()
        px = left + nx * (width - 1) / 65535.0
        py = top + ny * (height - 1) / 65535.0
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = max(2, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        vh = max(2, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        ax = round((px - vx) * 65535.0 / (vw - 1))
        ay = round((py - vy) * 65535.0 / (vh - 1))
        return min(max(ax, 0), 65535), min(max(ay, 0), 65535)

    def mouse_move(self, nx: int, ny: int) -> None:
        ax, ay = self._to_absolute(nx, ny)
        inp = INPUT(type=INPUT_MOUSE)
        inp.u.mi = MOUSEINPUT(ax, ay, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
        _send(inp)

    def mouse_button(self, button: int, down: bool) -> None:
        flags = _BUTTON_FLAGS.get(button)
        if flags is None:
            return
        down_flag, up_flag, data = flags
        inp = INPUT(type=INPUT_MOUSE)
        inp.u.mi = MOUSEINPUT(0, 0, data, down_flag if down else up_flag, 0, 0)
        _send(inp)
        if down:
            self._buttons_down.add(button)
        else:
            self._buttons_down.discard(button)

    def mouse_wheel(self, delta: int, horizontal: bool) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
        inp.u.mi = MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, flag, 0, 0)
        _send(inp)

    def key(self, vk: int, scan: int, extended: bool, down: bool) -> None:
        flags = 0
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        _send(inp)
        if down:
            self._keys_down[vk] = (scan, extended)
        else:
            self._keys_down.pop(vk, None)

    def release_all(self) -> None:
        """연결이 끊겼을 때 눌린 채로 남은 키/버튼을 모두 뗀다."""
        for vk, (scan, extended) in list(self._keys_down.items()):
            self.key(vk, scan, extended, False)
        for button in list(self._buttons_down):
            self.mouse_button(button, False)
