"""프로그램 ID 생성 및 해석.

ID 는 영어 대문자와 숫자로 된 10자리(36진수)이며, 약 51비트를 담을 수 있다.
  [IPv4 주소 XOR mask(무작위) 32비트][무작위 19비트]
Agent 는 ID 에서 상대 PC 의 IP 를 알아내 중계 서버 없이 직접 접속한다.
무작위 부분은 실행할 때마다 바뀌고 IP 부분도 그 값으로 섞이므로 ID 전체가 매번 달라진다.
IP 만 알아서는 접속할 수 없다(접속 시 ID 전체가 일치해야 함).
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import string

ID_LENGTH = 10
ID_ALPHABET = string.digits + string.ascii_uppercase  # 36진수 순서
ID_RE = re.compile(rf"^[A-Z0-9]{{{ID_LENGTH}}}$")
RANDOM_BITS = 19
_MAX_VALUE = 1 << (32 + RANDOM_BITS)


def _to_base36(value: int) -> str:
    chars = []
    while value:
        value, rem = divmod(value, 36)
        chars.append(ID_ALPHABET[rem])
    return "".join(reversed(chars)).rjust(ID_LENGTH, "0")


def _mask(rand: int) -> int:
    return int.from_bytes(hashlib.blake2s(rand.to_bytes(3, "big"), digest_size=4).digest(), "big")


def generate_id(ip: str | None) -> str:
    """ip 를 담은 10자리 ID 를 만든다. ip 가 없으면 무작위 값만 담는다(같은 네트워크 검색으로만 연결 가능)."""
    try:
        ip_value = int(ipaddress.IPv4Address(ip)) if ip else 0
    except ValueError:
        ip_value = 0
    rand = secrets.randbits(RANDOM_BITS)
    return _to_base36(((ip_value ^ _mask(rand)) << RANDOM_BITS) | rand)


def id_to_ip(client_id: str) -> str | None:
    """ID 에 담긴 IPv4 주소. 담겨 있지 않으면 None."""
    try:
        value = int(client_id, 36)
    except ValueError:
        return None
    if value >= _MAX_VALUE:
        return None
    rand = value & ((1 << RANDOM_BITS) - 1)
    ip_value = (value >> RANDOM_BITS) ^ _mask(rand)
    return str(ipaddress.IPv4Address(ip_value)) if ip_value else None


def normalize_id(text: str) -> str:
    """사용자 입력에서 공백/하이픈을 제거하고 대문자로 바꾼다."""
    return re.sub(r"[\s\-]", "", text).upper()


def is_valid_id(text: str) -> bool:
    return bool(ID_RE.match(text))
