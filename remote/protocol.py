"""전송 프로토콜.

모든 메시지는 [type: uint8][length: uint32][payload] 형식으로 TCP 위에서 주고받는다.
중계 서버와의 제어 메시지는 MSG_CTRL(JSON), 연결이 짝지어진 뒤에는 세션 메시지를 쓴다.
"""

from __future__ import annotations

import json
import socket
import struct
import threading

from .config import DEFAULT_PORT

PROTOCOL_VERSION = 1

HEADER = struct.Struct("!BI")
MAX_PAYLOAD = 32 * 1024 * 1024

# 중계 서버 제어 메시지 (JSON)
MSG_CTRL = 0x01

# 세션: Client(원격 받는 쪽) -> Agent(원격 하는 쪽)
MSG_HELLO = 0x10  # JSON: 화면 정보
MSG_VIDEO = 0x11  # H.264 패킷

# 세션: Agent -> Client
MSG_MOUSE_MOVE = 0x20
MSG_MOUSE_BUTTON = 0x21
MSG_MOUSE_WHEEL = 0x22
MSG_KEY = 0x23
MSG_KEYFRAME_REQ = 0x30

# 세션: 양방향
MSG_BYE = 0x3F

MOUSE_MOVE = struct.Struct("!HH")  # x, y (0~65535 로 정규화된 화면 좌표)
MOUSE_BUTTON = struct.Struct("!BB")  # button, down
MOUSE_WHEEL = struct.Struct("!hB")  # delta, horizontal
KEY = struct.Struct("!HHB")  # virtual key, scan code, flags

BUTTON_LEFT, BUTTON_RIGHT, BUTTON_MIDDLE, BUTTON_X1, BUTTON_X2 = range(5)
KEY_FLAG_DOWN = 0x01
KEY_FLAG_EXTENDED = 0x02


class ConnectionClosed(Exception):
    pass


class ProtocolError(Exception):
    pass


def parse_address(address: str) -> tuple[str, int]:
    address = address.strip()
    if not address:
        raise ValueError("서버 주소가 비어 있습니다.")
    if address.startswith("["):  # [IPv6]:port
        host, _, rest = address[1:].partition("]")
        port = rest.lstrip(":")
    elif address.count(":") == 1:
        host, port = address.split(":")
    else:
        host, port = address, ""
    return host, int(port) if port else DEFAULT_PORT


class Connection:
    """스레드 안전한 송신을 지원하는 메시지 단위 TCP 연결."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._send_lock = threading.Lock()
        self._closed = False
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

    @classmethod
    def open(cls, address: str, timeout: float = 10.0) -> "Connection":
        host, port = parse_address(address)
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(None)
        return cls(sock)

    @property
    def closed(self) -> bool:
        return self._closed

    def settimeout(self, timeout: float | None) -> None:
        self.sock.settimeout(timeout)

    def send(self, mtype: int, payload: bytes = b"") -> None:
        if self._closed:
            raise ConnectionClosed()
        data = HEADER.pack(mtype, len(payload)) + payload
        try:
            with self._send_lock:
                self.sock.sendall(data)
        except OSError as exc:
            self.close()
            raise ConnectionClosed(str(exc)) from exc

    def send_json(self, obj: dict, mtype: int = MSG_CTRL) -> None:
        self.send(mtype, json.dumps(obj).encode())

    def recv(self) -> tuple[int, bytes]:
        mtype, length = HEADER.unpack(self._recv_exact(HEADER.size))
        if length > MAX_PAYLOAD:
            raise ProtocolError(f"payload too large: {length}")
        return mtype, self._recv_exact(length)

    def recv_json(self) -> dict:
        mtype, payload = self.recv()
        if mtype != MSG_CTRL:
            raise ProtocolError(f"unexpected message type {mtype}")
        return json.loads(payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            try:
                r = self.sock.recv_into(view[got:])
            except socket.timeout:
                raise
            except OSError as exc:
                self.close()
                raise ConnectionClosed(str(exc)) from exc
            if r == 0:
                self.close()
                raise ConnectionClosed("connection closed")
            got += r
        return bytes(buf)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
