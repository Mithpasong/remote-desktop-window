"""중계 서버 없는 직접(P2P) 연결.

Client(원격 받는 쪽)
  - TCP 포트(기본 21500, 사용 중이면 21501~21509)를 열고 Agent 의 접속을 기다린다.
  - UDP 21500 으로 같은 네트워크의 검색 요청("이 ID 를 가진 PC 가 있나요?")에 응답한다.

Agent(원격 하는 쪽)
  1. 같은 네트워크에 UDP 검색을 보내 해당 ID 의 PC 를 찾는다.
  2. 못 찾으면 ID 에 담긴 IP 로 직접 TCP 접속한다.

연결 과정 (TCP, MSG_CTRL JSON)
  Agent  -> {"op": "connect", "target": ID, "from": 내 ID}
  Client -> {"op": "waiting"}                 사용자에게 허용 여부를 묻는 중
  Client -> {"op": "paired"} 또는 {"op": "error", "reason": ...}
이후 같은 TCP 연결에서 세션 메시지(화면/입력)를 주고받는다.
"""

from __future__ import annotations

import json
import logging
import select
import socket
import threading
import time

from .ids import id_to_ip
from .protocol import PROTOCOL_VERSION, Connection, ConnectionClosed, ProtocolError

log = logging.getLogger(__name__)

PORT_COUNT = 10  # 같은 PC 에서 여러 개 실행할 때를 위해 기본 포트부터 10개까지 시도

ERROR_MESSAGES = {
    "not_found": "해당 ID 의 PC 를 찾을 수 없습니다.",
    "rejected": "상대방이 원격 지원 요청을 거절했습니다.",
    "busy": "상대방 PC 가 이미 다른 원격 지원을 받고 있습니다.",
    "timeout": "상대방이 응답하지 않았습니다.",
    "self": "자기 자신의 ID 로는 연결할 수 없습니다.",
}
UNREACHABLE_MESSAGE = (
    "상대 PC 에 연결할 수 없습니다.\n"
    "· 상대방 프로그램이 실행 중인지\n"
    "· 같은 네트워크(또는 VPN)에 있는지\n"
    "· 상대 PC 의 Windows 방화벽에서 Window Remote 를 허용했는지 확인하세요."
)


class SessionError(Exception):
    pass


def primary_ip() -> str | None:
    """기본 경로(인터넷/게이트웨이로 나가는) 네트워크의 IPv4 주소."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # 실제로 패킷을 보내지는 않는다
            ip = s.getsockname()[0]
            if not ip.startswith("0."):
                return ip
    except OSError:
        pass
    ips = local_ip_addresses()
    return ips[0] if ips else None


def local_ip_addresses() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.append(ip)
    except OSError:
        pass
    return ips


# ============================================================================
# Client: 접속 대기
# ============================================================================


class IncomingRequest:
    """Agent 의 연결 요청. GUI 에서 accept()/reject() 중 하나를 호출한다."""

    def __init__(self, conn: Connection, from_id: str, address: str, on_cancel):
        self.conn = conn
        self.from_id = from_id
        self.address = address
        self._on_cancel = on_cancel
        self._decided = threading.Event()
        threading.Thread(target=self._watch, name="incoming-watch", daemon=True).start()

    def _watch(self) -> None:
        """허용 여부를 묻는 동안 Agent 가 요청을 취소(연결 종료)하면 알린다."""
        while not self._decided.is_set():
            try:
                readable, _, _ = select.select([self.conn.sock], [], [], 0.5)
                if readable and not self._decided.is_set():
                    if not self.conn.sock.recv(1, socket.MSG_PEEK):
                        break
            except (OSError, ValueError):
                break
        if not self._decided.is_set():
            self._decided.set()
            self.conn.close()
            self._on_cancel(self)

    def accept(self) -> Connection | None:
        if self._decided.is_set():
            return None
        self._decided.set()
        try:
            self.conn.send_json({"op": "paired"})
        except ConnectionClosed:
            return None
        self.conn.settimeout(None)
        return self.conn

    def reject(self, reason: str = "rejected") -> None:
        if self._decided.is_set():
            return
        self._decided.set()
        try:
            self.conn.send_json({"op": "error", "reason": reason})
        except ConnectionClosed:
            pass
        self.conn.close()


class Listener:
    """Client 기능: TCP 접속과 UDP 검색 요청을 받는다.

    콜백(백그라운드 스레드에서 호출):
      on_incoming(IncomingRequest)
      on_cancel(IncomingRequest)
    is_busy() 가 True 이면 새 요청은 자동으로 거절한다.
    """

    def __init__(self, base_port: int, get_id, is_busy, on_incoming, on_cancel):
        self.base_port = base_port
        self.port: int | None = None
        self._get_id = get_id
        self._is_busy = is_busy
        self._on_incoming = on_incoming
        self._on_cancel = on_cancel
        self._tcp: socket.socket | None = None
        self._udp: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """TCP 포트를 연다. 모두 사용 중이면 OSError."""
        last_error: OSError | None = None
        for port in range(self.base_port, self.base_port + PORT_COUNT):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # Windows 에서 SO_REUSEADDR 은 중복 바인딩을 허용하므로 쓰지 않는다.
                sock.bind(("0.0.0.0", port))
                sock.listen(8)
            except OSError as exc:
                sock.close()
                last_error = exc
                continue
            self._tcp, self.port = sock, port
            break
        if self._tcp is None:
            raise last_error or OSError("no free port")
        threading.Thread(target=self._accept_loop, name="listener-tcp", daemon=True).start()

        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # 같은 PC 에서 여러 개 실행해도 모두 방송 검색을 받을 수 있도록 공유
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind(("0.0.0.0", self.base_port))
            self._udp = udp
            threading.Thread(target=self._discovery_loop, name="listener-udp", daemon=True).start()
        except OSError as exc:
            log.warning("discovery socket unavailable: %s", exc)
        log.info("listening on TCP %d (discovery UDP %d)", self.port, self.base_port)

    def stop(self) -> None:
        self._stop.set()
        for sock in (self._tcp, self._udp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, addr = self._tcp.accept()
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.2)
                continue
            threading.Thread(target=self._handle, args=(sock, addr), name="listener-conn", daemon=True).start()

    def _handle(self, sock: socket.socket, addr) -> None:
        conn = Connection(sock)
        try:
            conn.settimeout(10)
            msg = conn.recv_json()
            if msg.get("op") != "connect":
                conn.close()
                return
            if str(msg.get("target", "")) != self._get_id():
                conn.send_json({"op": "error", "reason": "not_found"})
                conn.close()
                return
            if self._is_busy():
                conn.send_json({"op": "error", "reason": "busy"})
                conn.close()
                return
            conn.send_json({"op": "waiting"})
            conn.settimeout(None)
        except (ConnectionClosed, OSError, ProtocolError, ValueError):
            conn.close()
            return
        log.info("incoming request from %s (%s)", msg.get("from"), addr[0])
        self._on_incoming(IncomingRequest(conn, str(msg.get("from", "")), addr[0], self._on_cancel))

    def _discovery_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._udp.recvfrom(2048)
                msg = json.loads(data)
                if msg.get("op") == "discover" and msg.get("id") == self._get_id() and self.port:
                    reply = {"op": "here", "id": self._get_id(), "port": self.port}
                    self._udp.sendto(json.dumps(reply).encode(), addr)
            except (ValueError, AttributeError):
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.2)


# ============================================================================
# Agent: 상대 PC 찾기 + 접속
# ============================================================================


def discover(target_id: str, base_port: int, timeout: float = 0.8) -> list[tuple[str, int]]:
    """UDP 로 target_id 를 가진 PC 를 찾는다. [(ip, tcp_port), ...]"""
    payload = json.dumps({"op": "discover", "id": target_id, "v": PROTOCOL_VERSION}).encode()
    socks: list[socket.socket] = []
    # 인터페이스마다 소켓을 바인딩해야 해당 네트워크로 방송이 나간다.
    for local_ip in local_ip_addresses() + ["0.0.0.0"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((local_ip, 0))
            s.setblocking(False)
            socks.append(s)
        except OSError:
            continue

    targets = ["255.255.255.255", "127.0.0.1"]
    encoded_ip = id_to_ip(target_id)
    if encoded_ip:
        targets.append(encoded_ip)  # 다른 서브넷이라도 UDP 가 통하면 응답을 받을 수 있다

    found: list[tuple[str, int]] = []
    try:
        for s in socks:
            for target in targets:
                try:
                    s.sendto(payload, (target, base_port))
                except OSError:
                    pass
        deadline = time.monotonic() + timeout
        while socks and time.monotonic() < deadline:
            readable, _, _ = select.select(socks, [], [], max(0.0, deadline - time.monotonic()))
            for s in readable:
                try:
                    data, addr = s.recvfrom(2048)
                    msg = json.loads(data)
                except (OSError, ValueError):
                    continue
                if msg.get("op") == "here" and msg.get("id") == target_id:
                    entry = (addr[0], int(msg.get("port", base_port)))
                    if entry not in found:
                        found.append(entry)
            if found:
                break
    finally:
        for s in socks:
            s.close()
    return found


class SessionRequest:
    """Agent: 상대 ID 로 원격 연결을 요청한다. run() 은 블로킹, cancel() 은 다른 스레드에서 호출."""

    def __init__(self, my_id: str, target_id: str, base_port: int):
        self.my_id = my_id
        self.target_id = target_id
        self.base_port = base_port
        self._conn: Connection | None = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._conn:
            self._conn.close()

    def _candidates(self, on_status) -> list[tuple[str, int, float]]:
        """(ip, port, 접속 대기 시간) 후보 목록."""
        on_status("같은 네트워크에서 상대 PC 를 찾는 중...")
        candidates = [(ip, port, 3.0) for ip, port in discover(self.target_id, self.base_port)]
        ip = id_to_ip(self.target_id)
        if ip:
            for i in range(PORT_COUNT):
                entry = (ip, self.base_port + i, 5.0 if i == 0 else 1.5)
                if (entry[0], entry[1]) not in [(c[0], c[1]) for c in candidates]:
                    candidates.append(entry)
        return candidates

    def run(self, on_status=lambda text: None, on_waiting=lambda: None) -> Connection:
        candidates = self._candidates(on_status)
        if not candidates:
            raise SessionError(UNREACHABLE_MESSAGE)

        reached_any = False
        for ip, port, timeout in candidates:
            if self._cancelled:
                raise SessionError("연결 요청을 취소했습니다.")
            on_status(f"{ip} 에 연결하는 중...")
            try:
                conn = Connection.open(f"{ip}:{port}", timeout=timeout)
            except socket.timeout:
                if port == self.base_port:
                    break  # 기본 포트에서 응답이 없으면 방화벽/네트워크 문제. 다른 포트도 마찬가지.
                continue
            except OSError:
                continue  # 연결 거부: 그 포트에는 프로그램이 없음
            reached_any = True
            self._conn = conn
            try:
                conn.settimeout(10)
                conn.send_json({"op": "connect", "target": self.target_id, "from": self.my_id, "version": PROTOCOL_VERSION})
                resp = conn.recv_json()
                if resp.get("op") == "error" and resp.get("reason") == "not_found":
                    conn.close()  # 같은 PC 의 다른 인스턴스. 다음 포트 시도
                    continue
                if resp.get("op") == "waiting":
                    on_waiting()
                    conn.settimeout(90)
                    resp = conn.recv_json()
                if resp.get("op") == "paired":
                    conn.settimeout(None)
                    return conn
                conn.close()
                reason = str(resp.get("reason", "unknown"))
                raise SessionError(ERROR_MESSAGES.get(reason, f"연결에 실패했습니다. ({reason})"))
            except (ConnectionClosed, OSError, ProtocolError, ValueError) as exc:
                conn.close()
                if self._cancelled:
                    raise SessionError("연결 요청을 취소했습니다.") from exc
                raise SessionError(f"연결 중 오류가 발생했습니다.\n{exc}") from exc

        if reached_any:
            raise SessionError(ERROR_MESSAGES["not_found"] + "\nID 를 다시 확인하세요. (프로그램을 다시 실행하면 ID 가 바뀝니다)")
        raise SessionError(UNREACHABLE_MESSAGE)
