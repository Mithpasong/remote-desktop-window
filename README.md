# Window Remote 1.0.0

Python으로 만든 윈도우 원격 지원 프로그램입니다. **별도 서버 없이** `WindowRemote.exe` 하나로 PC끼리 직접 연결합니다.

- **Client (원격 지원 받기)**: 실행하면 10자리 ID(영어 대문자 + 숫자)가 만들어지고, 접속을 기다리는(Listening) 상태가 됩니다. 이 ID를 지원해 줄 사람에게 알려줍니다.
- **Agent (원격 지원하기)**: 상대방 ID를 입력하고 **연결**을 누릅니다. 상대방이 **허용**하면 상대 PC 화면을 보면서 마우스와 키보드로 제어할 수 있습니다.

화면은 H.264로 압축해 최대 60FPS로 전송합니다.

## 다운로드

[Releases](https://github.com/Mithpasong/remote-desktop-window/releases) 에서 `WindowRemote-1.0.0.zip` 을 받아 압축을 풀고 `WindowRemote.exe` 를 실행하세요.

## 배포

`dist\WindowRemote.exe` 파일 하나만 각 PC에 복사하면 됩니다(Python 설치 불필요).

처음 실행할 때 **Windows 방화벽 창이 뜨면 "허용"**을 눌러야 다른 PC에서 접속할 수 있습니다.

## 연결 원리

```
[Agent PC] ── TCP 21500 직접 접속 ──> [Client PC (Listening)]
```

1. Client는 TCP 21500 포트를 열고 기다립니다. 같은 PC에서 여러 개를 실행하면 21501~21509를 사용합니다.
2. ID에는 Client PC의 IP 주소가 무작위 값과 섞여 들어 있습니다. ID는 실행할 때마다 바뀝니다.
3. Agent가 ID를 입력하면 다음 순서로 상대 PC를 찾습니다.
   - 같은 네트워크에 UDP 21500 방송으로 "이 ID를 가진 PC가 있나요?"라고 묻습니다.
   - 응답이 없으면 ID에 담긴 IP로 직접 접속합니다(다른 서브넷, VPN 등).
4. Client는 접속 요청의 ID가 자기 ID와 정확히 일치할 때만 허용 여부를 묻습니다. IP만 알아서는 접속할 수 없습니다.

### 연결 가능한 범위

| 환경 | 가능 여부 |
|---|---|
| 같은 공유기 / 같은 사무실 네트워크 | 가능 |
| 사내망의 다른 서브넷, VPN으로 연결된 PC | 가능 (TCP 21500이 통하면) |
| 인터넷 너머 (집 ↔ 회사 등, 공유기 뒤의 PC) | 불가능 — 아래 참고 |

인터넷 너머의 PC는 공유기(NAT) 뒤에 있어 외부에서 직접 접속할 수 없습니다. 이 경우 VPN으로 두 네트워크를 연결해야 합니다.

## 사용 방법

1. 지원 받는 사람: 프로그램을 실행하고 **내 ID**를 지원해 주는 사람에게 알려줍니다.
2. 지원하는 사람: **상대방 ID** 칸에 입력 → **연결**
3. 지원 받는 사람: "원격 지원 요청" 창에서 **허용** (55초 동안 응답이 없으면 자동 거부)
4. 원격 화면 창이 열립니다.
   - 원격 창이 활성화되어 있으면 키 입력이 모두 상대 PC로 전달됩니다(Win 키, Alt+Tab 포함).
     내 PC를 다시 쓰려면 마우스로 원격 창 밖을 클릭하세요.
   - 도구 모음: **전체 화면**, **화면 새로고침**, **연결 종료**
5. 원격 제어를 받는 동안에는 화면 상단에 빨간 알림 막대가 표시되며, **연결 종료**로 언제든지 끊을 수 있습니다.

## 설정 (선택)

`WindowRemote.exe`와 같은 폴더의 `config.json`, 또는 `%APPDATA%\WindowRemote\config.json`으로 바꿀 수 있습니다. `config.example.json` 참고.

| 키 | 기본값 | 설명 |
|---|---|---|
| `port` | `21500` | 접속 대기 포트. **모든 PC가 같은 값**이어야 합니다 |
| `fps` | `60` | 최대 전송 FPS |
| `max_pixels` | `3686400` (2560×1440) | 이보다 큰 화면은 비율을 유지해 축소 전송 |
| `crf` | `23` | 화질 (낮을수록 고화질, 18~30) |
| `max_bitrate_kbps` | `15000` | 최대 비트레이트 |

## 문제 해결

- **"상대 PC에 연결할 수 없습니다"**: 상대 프로그램이 실행 중인지, 같은 네트워크인지, 상대 PC 방화벽에서 Window Remote가 허용되어 있는지 확인하세요.
  방화벽 창에서 실수로 "취소"를 눌렀다면: Windows 보안 → 방화벽 및 네트워크 보호 → 방화벽에서 앱 허용 → WindowRemote 체크.
- **"해당 ID의 PC를 찾을 수 없습니다"**: 상대 PC에는 접속됐지만 ID가 다릅니다. 프로그램을 다시 실행하면 ID가 바뀌므로 새 ID를 확인하세요.
- **자가 진단**: `WindowRemote.exe --selftest` 로 그 PC에서 화면 캡처/인코딩이 되는지 확인할 수 있습니다.
- **로그**: `%APPDATA%\WindowRemote\window_remote.log`
- **관리자 권한 창(작업 관리자, 설치 프로그램 등)이 제어되지 않음**: Windows 보안 정책(UIPI) 때문입니다. 지원 받는 쪽에서 `WindowRemote.exe`를 **관리자 권한으로 실행**하면 제어할 수 있습니다.
- **백신이 exe를 차단**: PyInstaller로 만든 원격 제어 프로그램은 오탐되기 쉽습니다. 정식 배포 시에는 코드 서명 인증서로 서명하는 것을 권장합니다.

## 현재 버전의 제한 사항

- 인터넷 너머(NAT 뒤) PC와는 직접 연결할 수 없습니다(VPN 필요).
- 주 모니터만 전송합니다.
- UAC 확인 창, 잠금 화면, Ctrl+Alt+Del 화면은 제어할 수 없습니다(Windows 서비스 형태가 필요).
- 통신은 암호화되지 않습니다.
- 상대방 PC의 마우스 커서 모양은 화면에 그려지지 않습니다(내 커서로 조작).

## 개발 / 빌드

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py          :: 소스에서 실행
build.bat                              :: dist\WindowRemote.exe 생성
```

### 소스 구성

| 파일 | 역할 |
|---|---|
| `main.py` | 실행 진입점, 로그, 자가 진단 |
| `remote/gui.py` | 메인 창 (내 ID, 상대 ID 연결, 수락 창) |
| `remote/network.py` | 직접 연결: Client 접속 대기/검색 응답, Agent 검색/접속 |
| `remote/ids.py` | ID 생성 (IP + 무작위 값 → 10자리) 및 해석 |
| `remote/agent_viewer.py` | Agent 원격 화면 창 (디코딩/표시, 마우스·키보드 전송) |
| `remote/client_session.py` | Client 세션 (화면 캡처·인코딩·전송, 입력 주입) |
| `remote/capture.py` | 화면 캡처 (DXGI, 실패 시 GDI) |
| `remote/video.py` | H.264 인코더/디코더 |
| `remote/input_inject.py` | SendInput 입력 주입 |
| `remote/keyhook.py` | 저수준 키보드 훅 (시스템 키 전달) |
| `remote/protocol.py` | 메시지 형식 |
