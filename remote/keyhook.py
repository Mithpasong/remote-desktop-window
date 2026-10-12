"""저수준 키보드 훅 (원격 하는 쪽).

뷰어 창이 활성화되어 있는 동안 Win 키, Alt+Tab 같은 시스템 키까지 가로채
원격 PC 로 보낸다. 훅 콜백은 훅을 설치한 스레드(Qt GUI 스레드)에서 실행된다.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10

LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


class KeyboardHook:
    """키 이벤트마다 on_key(vk, scan, extended, down) 를 호출한다.
    on_key 가 True 를 반환하면 해당 키는 로컬 PC 로 전달되지 않는다."""

    def __init__(self, on_key):
        self._on_key = on_key
        self._hook = None
        self._proc = HOOKPROC(self._callback)  # 참조 유지 필수 (GC 방지)

    def install(self) -> bool:
        if self._hook:
            return True
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            log.warning("SetWindowsHookEx failed: %s", ctypes.get_last_error())
            return False
        return True

    def uninstall(self) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _callback(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                # 다른 프로그램(또는 같은 PC 에서 테스트 중인 Client)이 주입한 입력은 무시
                if not (kb.flags & LLKHF_INJECTED):
                    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    if self._on_key(kb.vkCode, kb.scanCode, bool(kb.flags & LLKHF_EXTENDED), down):
                        return 1
        except Exception:
            log.exception("keyboard hook error")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)
