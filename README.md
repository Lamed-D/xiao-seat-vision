# xiao-seat-vision

[![xiao-seat-vision CI](https://github.com/Lamed-D/xiao-seat-vision/actions/workflows/ci.yml/badge.svg)](https://github.com/Lamed-D/xiao-seat-vision/actions/workflows/ci.yml)

**카메라 한 대로 여러 좌석이 비었는지/사용 중인지 자동으로 알려주는 시스템**입니다.

- 작은 카메라 보드(**Seeed Studio XIAO ESP32S3 Sense**)가 좌석을 촬영하고,
- 보드 **안에서 직접** AI(Edge Impulse FOMO, 사람 감지)를 실행해 좌석마다 `비어 있음 / 사용 중 / 확인 불가`를 판단합니다.
- 좌석 상태가 **바뀔 때만** 암호화된 MQTT(TLS)로 PC의 **Docker** 서버에 보내고,
- PC의 브리지 프로그램이 이를 클라우드 DB(**Supabase**)에 저장하며,
- 웹 **대시보드**에서 좌석 현황을 봅니다. (기술 정보는 **개발자 모드**에서만 표시)

> ### ⚠️ 현재 상태를 먼저 확인하세요
> | 항목 | 상태 |
> |---|---|
> | 펌웨어 컴파일 (Arduino CLI, esp32 코어 3.3.12) | ✅ 확인됨 |
> | 펌웨어 로직 / 브리지 / Edge Function / DB 마이그레이션 / Docker 통합 테스트 | ✅ 자동 테스트 통과 |
> | **실제 보드: PSRAM 8 MB + 카메라(OV3660) 320×240 촬영** | ✅ **확인됨** (2026-11-03, FB-OVF 0건, 메모리 안정) |
> | **실제 보드: Wi-Fi + MQTT over TLS → 브로커 → 브리지 → Supabase** | ✅ **확인됨** (online 상태·30초 telemetry가 DB에 저장) |
> | Supabase (테이블·Edge Function 4개·비밀값) | ✅ 배포됨, 인증(401/403) 확인 |
> | 대시보드 (토큰 없이 로컬 조회, 개발자 모드 포함) | ✅ 확인됨 |
> | GitHub Actions CI | ✅ 전체 통과 |
> | **AI 모델 (Edge Impulse FOMO)** | ✅ **보드에서 실행 확인** (추론 122 ms, 3분 연속 크래시 0) — 단, 지금은 **모의 데이터(거리 보행자)** 모델이라 오탐이 많음. 실제 설치 위치 사진으로 재학습 필요 |
> | 스트리밍·테스트 영상·LWT·장애 복구 | ⏳ 직접 확인 필요 → [하드웨어 체크리스트](docs/hardware-test.md) |
>
> "CI 통과 = 컴파일 성공"이며, 보드에서 실제로 동작함을 뜻하지 않습니다.
> 무엇을 언제 어떻게 검증했는지는 [docs/test-results.md](docs/test-results.md)에 기록합니다.

---

## 목차
- [0. 전체 구조 한눈에 보기](#0-전체-구조-한눈에-보기)
- [1. 준비물](#1-준비물)
- [2. 프로그램 설치 (PC)](#2-프로그램-설치-pc)
- [3. 프로젝트 내려받기](#3-프로젝트-내려받기)
- [4. Arduino IDE 설정 (보드 패키지·보드 옵션)](#4-arduino-ide-설정-보드-패키지보드-옵션)
- [5. 1차 업로드: 카메라·PSRAM 동작 확인 (네트워크 없이)](#5-1차-업로드-카메라psram-동작-확인-네트워크-없이)
- [6. Supabase(클라우드 DB) 준비](#6-supabase클라우드-db-준비)
- [7. Docker로 MQTT 브로커·브리지·대시보드 실행](#7-docker로-mqtt-브로커브리지대시보드-실행)
- [8. 펌웨어에 Wi-Fi·MQTT 설정 후 2차 업로드](#8-펌웨어에-wi-fimqtt-설정-후-2차-업로드)
- [9. 좌석 영역(ROI) 설정](#9-좌석-영역roi-설정)
- [10. AI 모델 만들기·넣기 (Edge Impulse)](#10-ai-모델-만들기넣기-edge-impulse)
- [11. 대시보드 사용법](#11-대시보드-사용법)
- [12. 실시간 스트리밍 (선택)](#12-실시간-스트리밍-선택)
- [13. 테스트 영상으로 검증 (가상 카메라)](#13-테스트-영상으로-검증-가상-카메라)
- [14. 매일 쓰는 명령어 모음](#14-매일-쓰는-명령어-모음)
- [15. 문제 해결 (FAQ)](#15-문제-해결-faq)
- [16. 개발자 정보 (테스트·CI·폴더 구조·기여 규칙)](#16-개발자-정보-테스트ci폴더-구조기여-규칙)
- [17. 보안 규칙](#17-보안-규칙)

---

## 0. 전체 구조 한눈에 보기

```text
 [XIAO ESP32S3 Sense 보드]                         [내 PC - Docker]                        [클라우드 - Supabase]
  카메라 촬영 (320x240 JPEG)
     │
  AI 사람 감지 (보드 안에서 실행)
     │
  좌석별 판단 (3회 연속 같으면 확정)
     │  상태가 바뀔 때만
     └── MQTT over TLS (8883) ──▶  Mosquitto 브로커 ──▶ mqtt-bridge ── HTTPS ──▶ Edge Functions ──▶ PostgreSQL DB
                                   (인증·권한 검사)      (검증·재시도)                                  + 이미지 저장소
                                                                                                          │
                                     dashboard (http://127.0.0.1:8080) ◀──────────── dashboard-api ◀──────┘
```

| 구성 요소 | 어디서 실행 | 역할 |
|---|---|---|
| 펌웨어 `firmware/xiao_seat_vision` | XIAO 보드 | 촬영, AI, 좌석 판단, MQTT 전송, (선택) 스트리밍 |
| `mosquitto` | PC Docker | MQTT 우체국. 암호화(TLS), 비밀번호, 장치별 권한(ACL) |
| `mqtt-bridge` | PC Docker | MQTT 메시지를 검사해 Supabase로 전달, 실패 시 재시도 |
| `dashboard` | PC Docker | 좌석 현황 웹 페이지 |
| `video-feeder` (선택) | PC Docker | MP4 영상을 카메라처럼 재생해 테스트 |
| Edge Functions + DB | Supabase | 저장, 중복 방지, 대시보드용 조회 API |

MQTT 토픽 (모든 토픽은 `xiao-seat-vision/` 로 시작):

| 토픽 | 내용 |
|---|---|
| `xiao-seat-vision/{장치ID}/events` | 좌석 상태 **변경** 이벤트 (QoS 1, retain 없음) |
| `xiao-seat-vision/{장치ID}/status` | `online` / 비정상 종료 시 `offline` (LWT, retain) |
| `xiao-seat-vision/{장치ID}/telemetry` | 30초마다 상태·성능·메모리 보고 |
| `xiao-seat-vision/{장치ID}/snapshot` | 사진(JPEG) + 감지 위치 |

이벤트 예시:
```json
{ "event_id": "xiao-01-9f3a12bc-000001", "device_id": "xiao-01", "seat_id": "seat_02",
  "state": "occupied", "confidence": 0.95, "inference_ms": 180,
  "timestamp": "2026-11-03T10:00:00Z", "is_test": false, "source": "camera" }
```

---

## 1. 준비물

**하드웨어**
- Seeed Studio **XIAO ESP32S3 Sense** (카메라 확장 보드가 끼워진 상태)
  - 카메라 센서는 보드 생산 시기에 따라 **OV2640** 또는 **OV3660** 입니다. 펌웨어가 자동으로 감지해 알려줍니다.
- **데이터 전송이 되는** USB-C 케이블 (충전 전용 케이블은 인식되지 않습니다)
- 2.4 GHz Wi-Fi (ESP32-S3는 5 GHz를 지원하지 않습니다)
- PC (Windows 10/11 기준으로 설명, macOS/Linux도 동일한 흐름)

**계정**
- [Supabase](https://supabase.com) 무료 계정 (클라우드 DB)
- [Edge Impulse](https://edgeimpulse.com) 무료 계정 (AI 모델 학습, 10단계에서 필요)
- (선택) GitHub 계정

**PC와 보드는 같은 Wi-Fi(공유기)에 연결**되어 있어야 합니다.

---

## 2. 프로그램 설치 (PC)

| 프로그램 | 용도 | 설치 확인 명령 |
|---|---|---|
| [Git for Windows](https://git-scm.com/download/win) | 코드 받기 + **Git Bash** (아래 `sh` 명령 실행용) | `git --version` |
| [Arduino IDE 2.x](https://www.arduino.cc/en/software) | 펌웨어 편집·업로드 | 실행 확인 |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | 브로커·브리지·대시보드 실행 | `docker info` |
| [Node.js 20 이상](https://nodejs.org) | Supabase CLI(`npx supabase`) 실행 | `node --version` |
| [Python 3.10 이상](https://www.python.org) (선택) | 카메라 스냅샷 저장 도구 | `python --version` |

> 이 문서의 `sh ...`, `cp ...` 명령은 **Git Bash**에서 실행하세요. (시작 메뉴 → Git Bash)
> PowerShell에서는 `cp` 대신 `Copy-Item`을 쓰면 됩니다.

**Docker Desktop 실행 확인**: Docker Desktop을 켜고 고래 아이콘이 "Engine running" 상태가 된 뒤 Git Bash에서:
```bash
docker info --format '{{.ServerVersion}}'    # 숫자가 나오면 정상 (예: 29.9.2)
docker compose version
```
`failed to connect to the docker API` 오류가 나면 Docker Desktop이 아직 실행되지 않은 것입니다.

---

## 3. 프로젝트 내려받기

```bash
cd ~/Documents/GitHub          # 원하는 폴더
git clone https://github.com/Lamed-D/xiao-seat-vision.git
cd xiao-seat-vision
```

**폴더 구조**
```text
xiao-seat-vision/
├── firmware/
│   ├── xiao_seat_vision/      ← Arduino IDE에서 여는 스케치 폴더 (.ino + .h/.cpp)
│   ├── tests/                 ← 펌웨어 로직 단위 테스트 (PC에서 실행)
│   └── tools/capture_snapshot.py  ← USB로 카메라 사진 저장
├── mqtt/                      ← Mosquitto 설정, 인증서/계정 생성 스크립트
├── bridge/                    ← MQTT → Supabase 브리지 (Python)
├── dashboard/                 ← 웹 대시보드
├── video_feeder/              ← MP4 테스트 영상 재생기
├── supabase/                  ← DB 마이그레이션, Edge Functions
├── test-videos/               ← 테스트용 MP4를 넣는 곳 (Git 제외)
├── tests/integration/         ← Docker 통합 테스트
├── docs/                      ← 상세 문서
├── compose.yaml               ← Docker Compose (하나의 파일)
└── .env.example               ← 환경변수 예시
```

---

## 4. Arduino IDE 설정 (보드 패키지·보드 옵션)

### 4-1. ESP32 보드 패키지 설치
1. Arduino IDE 실행 → **File → Preferences**
2. **Additional boards manager URLs** 칸에 아래 주소 입력 → OK
   ```
   https://espressif.github.io/arduino-esp32/package_esp32_index.json
   ```
3. 왼쪽 **Boards Manager**(보드 아이콘) → `esp32` 검색
4. **esp32 by Espressif Systems** 에서 버전 **3.3.12** 선택 → INSTALL
   - 이 프로젝트와 CI는 **3.3.12**로 맞춰져 있습니다. 다른 버전을 쓰면 컴파일 결과가 다를 수 있습니다.
   - 추가로 설치할 라이브러리는 **없습니다**. (Wi-Fi, TLS, MQTT, 카메라, 웹서버 모두 보드 패키지에 포함)

### 4-2. 보드와 옵션 선택 (중요)
보드를 USB로 연결한 뒤 **Tools** 메뉴에서:

| 메뉴 | 선택할 값 | 설명 |
|---|---|---|
| Board | **esp32 → XIAO_ESP32S3** | |
| Port | 보드 포트 (예: `COM5`) | 연결/해제하면서 새로 생기는 포트 |
| **PSRAM** | **OPI PSRAM** | ⚠️ 기본값이 *Disabled*입니다. 반드시 바꾸세요 (카메라·AI에 필수) |
| Flash Size | 8MB (64Mb) | |
| Partition Scheme | Default with spiffs (3MB APP/1.5MB SPIFFS) | 모델이 커서 공간 부족 시 *Maximum APP (7.9MB)* |
| USB CDC On Boot | Enabled | 시리얼 로그 출력용 |
| USB Mode | Hardware CDC and JTAG | |

> 포트가 안 보이면: 다른 USB 케이블(데이터용) 사용 → 그래도 안 되면 보드의 **BOOT 버튼을 누른 채** USB를 꽂으세요.

---

## 5. 1차 업로드: 카메라·PSRAM 동작 확인 (네트워크 없이)

아무 설정 없이도 펌웨어는 컴파일되고 동작합니다(이때 Wi-Fi/MQTT는 꺼져 있음). 먼저 보드와 카메라가 정상인지 확인합니다.

1. Arduino IDE → **File → Open** → `firmware/xiao_seat_vision/xiao_seat_vision.ino`
   - 같은 폴더의 모든 `.h/.cpp` 파일이 탭으로 함께 열립니다.
2. **Upload**(→ 버튼) 클릭. 처음 컴파일은 몇 분 걸릴 수 있습니다.
3. **Tools → Serial Monitor**, 오른쪽 아래 속도 **115200 baud**
4. 보드의 RESET 버튼을 누르면 아래와 비슷한 로그가 나옵니다:

```text
=== xiao-seat-vision firmware ===
[boot] device=xiao-01 config=config.example.h (placeholders - network disabled) chip=ESP32-S3 rev0 cpu=240MHz flash=8388608B core=3.3.12
[mem] heap: total=... free=... min_free=... max_alloc=...
[mem] psram: total=8386279 free=... min_free=... max_alloc=...      ← PSRAM 정상
[camera] ready: sensor=OV3660 (PID 0x3660)                           ← 카메라 정상 (이 보드는 OV3660 확인됨, 다른 보드는 OV2640일 수 있음)
[infer] Edge Impulse model NOT integrated (...)                      ← 아직 모델 없음 (정상)
[boot] 3 seat(s) configured
[wifi] not configured (XSV_WIFI_SSID empty) - networking disabled
[boot] ready - send 'h' over serial for commands
[infer] model not integrated - camera frame 320x240 (9876 bytes) OK, no inference, seats stay unknown, no events
```

**확인 포인트**
- `psram: NOT FOUND` → Tools → PSRAM → **OPI PSRAM**으로 바꾸고 다시 업로드
- `[camera] init failed` → 카메라 확장 보드가 제대로 끼워졌는지 확인 (보드 전원 분리 후)
- 시리얼 모니터 입력창에 `s` 입력 → 상태, `h` → 도움말

**브라우저로 카메라 실시간 확인 (추천, Wi-Fi·Docker 불필요)**
```bash
# Arduino IDE의 시리얼 모니터를 먼저 닫으세요 (포트는 한 프로그램만 사용 가능)
pip install pyserial
python firmware/tools/camera_viewer.py COM5
# → 브라우저에서 http://127.0.0.1:8000
```
- 화면이 USB로 계속 갱신됩니다(초당 몇 장). 해상도·프레임 크기·FPS 표시
- **점검 결과** 칸이 PSRAM 용량, 카메라 센서(OV2640/OV3660), 축소 모드 여부, `FB-OVF` 발생 여부를 자동으로 판정
- `seats_config.h`의 좌석 ROI를 화면 위에 겹쳐 보여줘 좌석 배치를 바로 맞출 수 있음
- **사진 저장** 버튼 → `snapshots/` 폴더 (Edge Impulse 학습 데이터로도 사용)
- 뷰어를 켜 둔 동안에는 Arduino IDE 업로드가 안 됩니다. 업로드 전에 뷰어를 종료하세요(Ctrl+C).
- Windows의 Docker Desktop은 USB 시리얼 포트를 컨테이너로 넘길 수 없어서 이 도구는 PC에서 직접 실행합니다.
  Wi-Fi 설정 후에는 보드 웹 스트리밍(12단계)과 대시보드로도 볼 수 있습니다.

**카메라 사진 저장해 보기** (여러 장을 일정 간격으로 저장할 때)
```bash
# Arduino IDE의 시리얼 모니터를 먼저 닫으세요 (포트는 한 프로그램만 사용 가능)
pip install pyserial
python firmware/tools/capture_snapshot.py COM5 --count 5 --interval 2 --out snapshots
# saved snapshots/20261103-101500-0000.jpg (320x240, 9876 bytes)
```

---

## 6. Supabase(클라우드 DB) 준비

### 6-1. 프로젝트 만들기 (웹)
1. https://supabase.com 로그인 → **New project** → 이름 `xiao-seat-vision`, DB 비밀번호 설정, 가까운 Region 선택
2. 생성 후 **Project Settings → General**의 **Reference ID**(예: `abcdefghijklmnop`)를 메모 → 이하 `<ref>`

### 6-2. DB 테이블 만들기 + 함수 배포 (Git Bash, 프로젝트 폴더에서)
```bash
npx supabase login                         # 브라우저 로그인
npx supabase link --project-ref <ref>      # DB 비밀번호 입력
npx supabase db push                       # 001, 002 마이그레이션 적용 → 테이블·이미지 저장소 생성
```

### 6-3. 비밀 토큰 만들기·등록
토큰은 아무도 추측할 수 없는 긴 무작위 문자열입니다. 아래 명령을 **4번** 실행해 각각 복사해 두세요.
```bash
openssl rand -hex 32
```
```bash
cp supabase/.env.example supabase/.env.secrets     # Git에 올라가지 않는 파일
```
`supabase/.env.secrets` 를 열어 채웁니다:
```ini
BRIDGE_API_TOKENS=<토큰1>              # 브리지 → Supabase (bridge/.env에도 같은 값)
DASHBOARD_VIEWER_TOKENS=<토큰2>        # 대시보드 일반 조회
DASHBOARD_ADMIN_TOKENS=<토큰3>         # 대시보드 개발자 모드·좌석 레이아웃 수정
DASHBOARD_ALLOWED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
```
```bash
npx supabase secrets set --env-file supabase/.env.secrets
npx supabase functions deploy log-detection device-status ingest-snapshot dashboard-api
```
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`는 Supabase가 함수에 자동으로 넣어줍니다. **service role 키를 어떤 파일에도 적지 마세요.**

### 6-4. 동작 확인
```bash
curl -i -X POST "https://<ref>.supabase.co/functions/v1/log-detection" \
  -H "content-type: application/json" -H "x-bridge-token: <토큰1>" \
  -d '{"event_id":"xiao-01-manual-000001","device_id":"xiao-01","seat_id":"seat_01","state":"occupied","confidence":0.9,"inference_ms":100}'
```
- 처음: `HTTP/2 201` `{"status":"created",...}`
- 같은 명령 다시: `HTTP/2 200` `{"status":"duplicate",...}` (중복 저장 방지 확인)
- Supabase 웹 → **Table Editor → seat_events** 에 한 줄이 보이면 성공. (테스트 행은 지워도 됩니다)

자세한 내용: [docs/supabase.md](docs/supabase.md)

---

## 7. Docker로 MQTT 브로커·브리지·대시보드 실행

### 7-1. PC의 LAN IP 확인
보드는 이 주소로 PC에 접속합니다. **`localhost`나 `mosquitto`는 보드에서 사용할 수 없습니다.**
```powershell
ipconfig        # Windows: "무선 LAN 어댑터 Wi-Fi"의 IPv4 주소, 예: 192.168.0.10
```
공유기 설정에서 이 PC에 **고정 IP(DHCP 예약)** 를 주는 것을 권장합니다. (IP가 바뀌면 인증서를 다시 만들어야 함)

> **⚡ 빠른 방법 (권장): 7-2 ~ 7-4와 8단계의 MQTT 항목을 한 번에 자동 설정**
> 먼저 `firmware/xiao_seat_vision/config.h`에 Wi-Fi(SSID/비밀번호)만 입력해 두고 Git Bash에서:
> ```bash
> python scripts/setup_local.py --host 192.168.0.10 --host <PC이름> --supabase-ref <ref>
> ```
> 인증서, MQTT 계정 3개, `.env`, `bridge/.env`, `supabase/.env.secrets`(토큰 3종), `config.h`의 MQTT 주소·비밀번호·CA 인증서·웹 토큰을 만듭니다.
> 이미 있는 값은 덮어쓰지 않고, 비밀값은 화면에 출력하지 않습니다(모두 Git 제외 파일). 끝나면 7-5로 넘어가세요.
> PC 이름은 `hostname` 명령으로 확인합니다. 아래 7-2 ~ 7-4는 같은 작업을 수동으로 하는 방법입니다.

### 7-2. TLS 인증서 만들기 (개발용 자체 CA)
```bash
sh mqtt/scripts/gen-dev-certs.sh 192.168.0.10          # ← 7-1의 IP. 호스트명도 추가 가능: ... 192.168.0.10 my-pc.local
```
생성 결과 (모두 Git 제외):
- `mqtt/certs/public/ca.crt` → **보드와 브리지에 등록할 CA 인증서**
- `mqtt/certs/broker/` → 브로커 전용 (서버 인증서·개인키)
- `mqtt/certs/private/ca.key` → CA 개인키. **절대 공유·커밋 금지**

### 7-3. MQTT 계정 만들기
```bash
sh mqtt/scripts/add-user.sh xiao-01                      # 보드 계정 (사용자명 = 장치 ID). 출력된 비밀번호 메모!
sh mqtt/scripts/add-user.sh xiao-seat-vision-bridge      # 브리지 계정. 비밀번호 메모!
sh mqtt/scripts/add-user.sh healthcheck "<원하는 비밀번호>"  # 브로커 상태 점검용
```
- 비밀번호 없이 실행하면 무작위 비밀번호를 만들어 **한 번만** 보여줍니다.
- 보드를 여러 대 쓰면 `xiao-02`, `xiao-03` ... 처럼 장치마다 계정을 만듭니다.

### 7-4. 환경변수 파일 만들기
```bash
cp .env.example .env
cp bridge/.env.example bridge/.env
```
`.env` (루트):
```ini
MQTT_BIND_ADDRESS=0.0.0.0              # 보안상 7-1의 LAN IP로 지정 권장 (예: 192.168.0.10)
MQTT_TLS_PORT=8883
MQTT_HEALTHCHECK_PASSWORD=<7-3의 healthcheck 비밀번호>
DASHBOARD_PORT=8080
DASHBOARD_API_URL=https://<ref>.supabase.co/functions/v1/dashboard-api
```
`bridge/.env`:
```ini
MQTT_USERNAME=xiao-seat-vision-bridge
MQTT_PASSWORD=<7-3의 브리지 비밀번호>
SUPABASE_FUNCTIONS_URL=https://<ref>.supabase.co/functions/v1
BRIDGE_API_TOKEN=<6-3의 토큰1>
```
(나머지 값은 예시 파일의 기본값 그대로 두면 됩니다.)

### 7-5. 실행
```bash
docker compose config --quiet && echo "구성 OK"   # 설정 파일 검증
docker compose up -d --build                       # 빌드 + 백그라운드 실행 (처음 1~3분)
docker compose ps                                  # 상태 확인
```
정상이라면 세 서비스가 모두 **healthy** 입니다:
```text
NAME                            SERVICE       STATUS                  PORTS
xiao-seat-vision-dashboard-1    dashboard     Up 1 minute (healthy)   127.0.0.1:8080->80/tcp
xiao-seat-vision-mosquitto-1    mosquitto     Up 1 minute (healthy)   0.0.0.0:8883->8883/tcp
xiao-seat-vision-mqtt-bridge-1  mqtt-bridge   Up 1 minute (healthy)
```
로그 보기:
```bash
docker compose logs -f mqtt-bridge        # "MQTT connected to mosquitto:8883" 가 보이면 정상 (Ctrl+C로 빠져나옴)
docker compose logs -f mosquitto
```

### 7-6. Windows 방화벽
보드가 PC에 접속하려면 **8883/TCP 인바운드**를 허용해야 할 수 있습니다 (관리자 PowerShell):
```powershell
New-NetFirewallRule -DisplayName "xiao-seat-vision MQTT TLS" -Direction Inbound -Protocol TCP -LocalPort 8883 -Action Allow -Profile Private
```
⚠️ 공유기에서 8883 포트를 인터넷으로 **포트포워딩하지 마세요.**

자세한 내용: [mqtt/README.md](mqtt/README.md)

---

## 8. 펌웨어에 Wi-Fi·MQTT 설정 후 2차 업로드

1. `firmware/xiao_seat_vision/config.example.h` 를 같은 폴더에 **`config.h`** 로 복사합니다. (`config.h`는 Git에 올라가지 않습니다)
2. Arduino IDE에서 `config.h` 탭을 열어 값을 채웁니다:

```cpp
#define XSV_DEVICE_ID "xiao-01"                 // 7-3에서 만든 보드 계정 이름과 같아야 함
#define XSV_WIFI_SSID "내Wi-Fi이름"
#define XSV_WIFI_PASSWORD "내Wi-Fi비밀번호"

#define XSV_MQTT_URI "mqtts://192.168.0.10:8883" // 7-1의 PC LAN IP. localhost 사용 불가
#define XSV_MQTT_PASSWORD "7-3에서 받은 xiao-01 비밀번호"
#define XSV_MQTT_TOPIC_PREFIX "xiao-seat-vision"

// mqtt/certs/public/ca.crt 파일 내용을 그대로 붙여넣기 (BEGIN/END 줄 포함)
#define XSV_MQTT_CA_CERT R"PEM(
-----BEGIN CERTIFICATE-----
MIIDHzCCAgegAwIBAgIU...
...
-----END CERTIFICATE-----
)PEM"

// 보드의 로컬 웹(스트리밍 ON/OFF, 테스트 영상 입력)용 토큰. 비워두면 웹 기능 꺼짐
#define XSV_WEB_TOKEN "아무도-모를-긴-문자열-8자이상"
```

3. **Upload** → 시리얼 모니터에서 확인:
```text
[boot] device=xiao-01 config=config.h ...
[wifi] connected ip=192.168.0.42 rssi=-55 dBm         ← 보드 IP (스트리밍 때 사용)
[mqtt] configured: mqtts://192.168.0.10:8883 user=xiao-01 topics=xiao-seat-vision/xiao-01/events, ...
[mqtt] client started (connecting in background)
[mqtt] connected (session_present=0)                   ← 브로커 연결 성공
[stream] web server ready (stream OFF by default): http://<device-ip>/?token=...  max 3 fps
```
4. PC에서 `docker compose logs -f mqtt-bridge` → `status from xiao-01 stored` 가 보이면
   **보드 → 브로커 → 브리지 → Supabase** 경로가 연결된 것입니다. Supabase `device_status` 테이블에 `online` 행이 생깁니다.

> 모델을 넣기 전에는 좌석 이벤트가 생기지 않는 것이 **정상**입니다. (10단계에서 모델 추가)

**연결이 안 될 때** → [15. 문제 해결](#15-문제-해결-faq)

---

## 9. 좌석 영역(ROI) 설정

`firmware/xiao_seat_vision/seats_config.h` 에서 좌석마다 사각형 영역을 지정합니다.
좌표는 **이미지 전체를 0~1로 본 비율**입니다 (x=왼쪽, y=위, width=폭, height=높이).

```cpp
static const SeatRoi kSeats[] = {
    // id(영문/숫자/_/-)   표시 이름     x      y      width  height
    {"seat_01",          "Seat 1",    0.13f, 0.25f, 0.24f, 0.60f},
    {"seat_02",          "Seat 2",    0.38f, 0.25f, 0.24f, 0.60f},
    {"seat_03",          "Seat 3",    0.63f, 0.25f, 0.24f, 0.60f},
};
```
- 좌석 수는 자유 (1~32개). 줄을 추가/삭제하면 됩니다.
- 5단계에서 저장한 사진을 보며 좌석(사람 상체가 오는 위치)을 감싸도록 조정하세요. 320×240 사진에서 x=0.5는 가로 160픽셀입니다.
- 좌석 사이에 약간의 간격을 두세요. 겹치는 영역에 사람이 있으면 두 좌석 모두 `확인 불가`가 될 수 있습니다.
- 정사각형 입력 모델은 사진의 **가운데 영역(x 0.125~0.875)** 만 봅니다. 그 밖의 ROI는 부팅 시 경고가 나옵니다.
- 한계(카메라 각도, 가림 등): [docs/architecture.md](docs/architecture.md#limitations-of-centroid-based-seat-assignment-document-for-installers)

---

## 10. AI 모델 만들기·넣기 (Edge Impulse)

> **⚡ 자동 방법 (권장)**: `.env`에 Edge Impulse **Admin** API 키(`EI_API_KEY`)를 넣고
> ```bash
> python scripts/edge_impulse.py upload <사진폴더>   # 사진 + bounding_boxes.labels
> python scripts/edge_impulse.py train               # FOMO 96x96 int8 학습, F1 출력
> python scripts/edge_impulse.py deploy              # firmware/ei-model/xiao-seat-vision-person.zip
> ```
> 그다음 zip을 Arduino IDE에 추가하고 `config.h`에 `#define XSV_EI_MODEL_HEADER "xiao-seat-vision_inferencing.h"`를 넣어 업로드합니다.
> `deploy`는 esp32 코어 3.3.x에서 확인된 ESP-NN 메모리 문제를 피하도록 TFLite 엔진 + 텐서 메모리 2배로 자동 수정합니다
> (EON 기본 내보내기를 그대로 쓰면 `CORRUPT HEAP`으로 재부팅이 반복됩니다 → [docs/edge-impulse.md](docs/edge-impulse.md)).
> 아래는 같은 작업을 Edge Impulse 웹 화면에서 수동으로 하는 방법입니다(수동으로 받은 zip도 같은 수정이 필요).

1. **데이터 모으기**: 실제 설치 위치에서 `capture_snapshot.py`로 사진 수백 장 (빈 좌석, 1~N명, 조명 변화, 가방만 있는 경우 등)
2. Edge Impulse → 새 프로젝트 → **Object detection** → 사진 업로드 → 사람마다 **`person`** 박스 라벨링
3. **Impulse 설계**: Image data 96×96 (또는 160×160), **Resize mode: Fit shortest axis**, Image 블록, **Object Detection → FOMO (MobileNetV2 0.35)** → 학습
4. **Deployment** → **Arduino library** → **Quantized (int8)** → Build → zip 다운로드
5. Arduino IDE → **Sketch → Include Library → Add .ZIP Library…** → 받은 zip 선택
6. zip 안 `src/` 폴더의 헤더 파일명을 확인(예: `xiao-seat-vision_inferencing.h`)하고 `model_config.h` 수정:
   ```cpp
   #define XSV_EI_MODEL_HEADER "xiao-seat-vision_inferencing.h"   // 주석(//) 제거
   ```
7. Upload → 시리얼에서 `[infer] Edge Impulse model: ... type=fomo`, `[infer] 1 person(s) inference=..ms`, `[state] seat_01 -> occupied` 확인
8. (선택) CI에서도 모델 포함 빌드를 하려면 zip을 `firmware/ei-model/` 에 넣어 커밋

상세·호환성 주의사항: [docs/edge-impulse.md](docs/edge-impulse.md)

---

## 11. 대시보드 사용법

1. 브라우저에서 **http://127.0.0.1:8080** → 바로 좌석 현황이 보입니다. **토큰 입력 불필요**.
   - `.env`에 `DASHBOARD_VIEWER_TOKEN`, `DASHBOARD_ADMIN_TOKEN`이 있으면(`scripts/setup_local.py`가 자동으로 채움)
     대시보드 컨테이너가 Supabase 호출에 토큰을 **서버에서 대신 붙여 줍니다**. 토큰은 브라우저로 내려가지 않습니다.
   - 이 기능 때문에 대시보드는 **이 PC(127.0.0.1)에서만** 열리도록 되어 있습니다. `compose.yaml`의 포트를 `0.0.0.0`으로 바꾸지 마세요.
   - 토큰 값을 바꿨다면: `docker compose up -d dashboard`
2. (선택) 다른 PC나 다른 서버를 직접 조회할 때만 **⚙ 설정**에 API URL과 조회 토큰(`supabase/.env.secrets`의 `DASHBOARD_VIEWER_TOKENS`)을 입력합니다. 입력한 토큰이 있으면 그 토큰을 우선 사용합니다.
3. 10초마다 자동 새로고침됩니다.

**기본 화면** (누구나 이해할 수 있는 정보만)
- 위: 전체 좌석 / 사용 중 / 빈 좌석 수
- 가운데: 최근 촬영 사진 위에 좌석별 표시 — 🟢 비어 있음, 🟠 사용 중, ⚪ 확인 불가
- 아래: 마지막 업데이트 시각

**개발자 모드** (⚙ 설정 → 개발자 모드 체크, 기본 OFF. 이 PC의 Docker 대시보드는 관리자 토큰도 자동 사용)
- 좌석별 AI 신뢰도·추론 시간, ROI 경계선과 감지 중심점, FPS, Heap/PSRAM 여유, MQTT 연결·마지막 수신 시각, 모델 이름·버전, 최근 이벤트, 원시 JSON
- 개발자 모드는 **화면 표시 설정**입니다. 상세 데이터는 서버가 관리자 토큰에만 제공합니다.
- "테스트 영상 데이터 보기"를 켜면 테스트 이벤트(`is_test=true`)만 따로 봅니다.

장치가 꺼지거나(LWT) 3분 이상 소식이 없으면 그 장치의 좌석은 모두 **확인 불가**로 표시됩니다.
설정은 이 브라우저에만 저장됩니다. 상세: [docs/dashboard.md](docs/dashboard.md)

---

## 12. 실시간 스트리밍 (선택)

AI 판단이 최우선이며, 스트리밍은 화면 확인용 보조 기능입니다. **기본 OFF**.
- 보드 웹페이지: `http://<보드IP>/?token=<XSV_WEB_TOKEN>` → **Stream ON / OFF** 버튼
- 또는 대시보드 ⚙ 설정에 보드 주소(`http://192.168.0.42`)와 웹 토큰 입력 → "스트리밍 켜기" 버튼 (개발자 모드와 무관)
- 시리얼 모니터에서 `v` 입력으로도 토글

동작 방식: QVGA(320×240), 최대 3 FPS, 시청자 1명. 스트리밍 중에는 AI 추론 주기를 1초 → 2초로 늦춰 AI·MQTT를 우선합니다.
OFF를 누르면 보드에서 실제로 프레임 복사·전송이 멈춥니다. 시청자가 60초 동안 없거나 메모리 부족/카메라 오류가 나면 자동 OFF됩니다.
⚠️ 보드 웹은 암호화되지 않은 HTTP이므로 신뢰할 수 있는 내부 네트워크에서만 사용하세요.

---

## 13. 테스트 영상으로 검증 (가상 카메라)

MP4 영상을 카메라처럼 재생해 시스템을 검증합니다. 테스트 이벤트에는 항상 `"is_test": true, "source": "test_video"` 가 붙습니다.

```bash
cp video_feeder/.env.example video_feeder/.env     # TARGET 등 설정
# test-videos/ 폴더에 MP4 넣기 (예: test-videos/demo.mp4)
docker compose --profile test-video up -d --build video-feeder

curl -X POST "http://127.0.0.1:8090/config?video=demo.mp4&fps=3&loop=1&width=320&height=240"
curl -X POST http://127.0.0.1:8090/play        # 재생   (/pause 일시정지, /stop 정지)
curl http://127.0.0.1:8090/status              # 상태
```

| 모드 (`video_feeder/.env`의 `TARGET`) | 필요한 것 | 검증되는 것 |
|---|---|---|
| `esp32` | 실제 보드 + 모델 + `ESP32_URL`, `ESP32_WEB_TOKEN` | 보드 안 AI 추론 ~ DB 저장 전체 |
| `mqtt` | 보드 없음. `sh mqtt/scripts/add-user.sh xiao-test-01` 로 테스트 장치 계정 + 라벨 CSV | MQTT → 브리지 → Supabase → 대시보드 (AI는 검증 안 됨) |

`mqtt` 모드의 좌석 상태는 AI가 아니라 **사람이 만든 정답 라벨**(`test-videos/<영상이름>.labels.csv`)에서 나옵니다. 상세: [docs/test-video.md](docs/test-video.md)

---

## 14. 매일 쓰는 명령어 모음

```bash
docker compose up -d                    # 시작 (코드 변경 시: up -d --build)
docker compose ps                       # 상태
docker compose logs -f mqtt-bridge      # 브리지 로그
docker compose logs -f mosquitto        # 브로커 로그
docker compose restart mosquitto        # 계정 추가/변경 후
docker compose down                     # 종료 (데이터 보존)
docker compose down -v                  # 종료 + 브로커 저장 데이터 삭제
```
시리얼 명령: `s` 상태, `c` 사진(base64), `v` 스트리밍 토글, `h` 도움말

---

## 15. 문제 해결 (FAQ)

| 증상 | 원인·해결 |
|---|---|
| Arduino IDE에 포트가 안 보임 | 데이터용 USB 케이블 사용, BOOT 누른 채 연결, 다른 USB 포트 |
| `psram: NOT FOUND` | Tools → PSRAM → **OPI PSRAM** |
| `[camera] init failed` | 전원 분리 후 카메라 보드 재장착 |
| COM 포트 `busy` / `PermissionError` | 시리얼 모니터 등 포트를 쓰는 프로그램 종료 |
| `[wifi]`가 계속 재시도 | SSID/비밀번호, **2.4 GHz** 확인 |
| `[mqtt] transport error ... cert_flags=0x...` / `certificate verification failed` | ① `XSV_MQTT_CA_CERT`가 `mqtt/certs/public/ca.crt`와 다름 ② URI의 IP가 인증서에 없음 → `FORCE=1 sh mqtt/scripts/gen-dev-certs.sh <올바른IP>` 후 `docker compose restart mosquitto`, 새 ca.crt를 보드에 넣고 재업로드. **인증서 검증을 끄지 마세요** |
| `[mqtt] connection refused by broker (code 5)` | 사용자명(= `XSV_DEVICE_ID`)/비밀번호 불일치 → `add-user.sh` 다시 실행 후 `docker compose restart mosquitto` |
| 보드에서 연결 시도조차 실패 | PC 방화벽 8883 허용(7-6), PC와 보드가 같은 Wi-Fi인지, `MQTT_BIND_ADDRESS` 확인 |
| `mqtt-bridge`가 `unhealthy`/재시작 반복 | `docker compose logs mqtt-bridge` — `missing required environment variable` → `bridge/.env` 확인, `MQTT connect refused` → 브리지 계정 비밀번호 |
| 브리지 로그 `configuration problem (HTTP 401)` | `bridge/.env`의 `BRIDGE_API_TOKEN` ≠ Supabase `BRIDGE_API_TOKENS` |
| 브리지 로그 `HTTP 404` | `SUPABASE_FUNCTIONS_URL` 오타 또는 함수 미배포 |
| `mosquitto` unhealthy | `.env`의 `MQTT_HEALTHCHECK_PASSWORD`와 `healthcheck` 계정 비밀번호 불일치, `mqtt/certs/broker` 파일 존재 확인 |
| 대시보드 `불러오기 실패: HTTP 401` | 조회 토큰 확인 / `CORS` 오류 → `DASHBOARD_ALLOWED_ORIGINS`에 `http://127.0.0.1:8080` 포함 후 secrets 재등록 |
| 좌석이 모두 "확인 불가" | 모델 미통합(정상), 장치 offline, 3분 이상 수신 없음 |

---

## 16. 개발자 정보 (테스트·CI·폴더 구조·기여 규칙)

**로컬 테스트**
```bash
# 펌웨어 컴파일 (Arduino CLI가 있다면, IDE와 같은 코어)
arduino-cli compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi,FlashSize=8M,PartitionScheme=default_8MB" firmware/xiao_seat_vision
# 펌웨어 로직 단위 테스트 (g++ 없으면 Docker 사용)
docker run --rm -v "$PWD/firmware:/src/firmware:ro" -w /src gcc:14 sh firmware/tests/run.sh
# 브리지 / 비디오 피더 / 대시보드
pip install -r bridge/requirements-dev.txt && python -m pytest -q bridge/tests
pip install -r video_feeder/requirements-dev.txt && python -m pytest -q video_feeder/tests
node --test dashboard/viewmodel.test.js
# Supabase Edge Functions (Deno) + DB 마이그레이션 (Docker)
cd supabase/functions && deno fmt --check && deno lint && deno test --allow-env tests/ && cd -
sh supabase/tests/run_migration_tests.sh
# Docker 통합 테스트 (.env, bridge/.env, mqtt/certs, mqtt/secrets 가 없을 때만 실행됨 — 실제 설정 보호)
bash tests/integration/run.sh
```

**GitHub Actions CI** (`.github/workflows/ci.yml`, push / PR / 수동 실행)
- Arduino CLI + esp32 **3.3.12** 펌웨어 빌드(모델 유무 구분, `.bin` Artifact 업로드), 펌웨어 로직, 브리지·피더 pytest, 대시보드,
  Supabase(Deno + PostgreSQL), Docker 통합 테스트, PR 커밋 메시지 검사. 실제 Supabase 비밀 키 불필요.
- 상세: [docs/ci.md](docs/ci.md)

**기여 규칙** — 브랜치 `feat/…`, `fix/…`, `docs/…`, `refactor/…`, `ci/…` / 커밋 `type(scope): description` (Conventional Commits) → [CONTRIBUTING.md](CONTRIBUTING.md)

**문서**: [test-results](docs/test-results.md) · [architecture](docs/architecture.md) · [setup](docs/setup.md) · [edge-impulse](docs/edge-impulse.md) · [supabase](docs/supabase.md) · [dashboard](docs/dashboard.md) · [test-video](docs/test-video.md) · [ci](docs/ci.md) · [hardware-test](docs/hardware-test.md) · [mqtt](mqtt/README.md)

---

## 17. 보안 규칙
- 절대 커밋 금지: `.env`, `bridge/.env`, `video_feeder/.env`, `supabase/.env.secrets`, `firmware/xiao_seat_vision/config.h`, `mqtt/certs/`, `mqtt/secrets/` (모두 `.gitignore` 처리됨)
- Supabase **service role 키**는 어떤 파일에도 적지 않습니다 (Edge Function에 자동 주입).
- TLS 인증서 검증을 끄는 방식으로 연결 문제를 해결하지 않습니다.
- 브로커 8883 포트를 인터넷에 공개하지 않습니다. 1883(평문)은 외부에 열리지 않도록 구성되어 있습니다.
- 장치마다 별도 MQTT 계정을 쓰며, 장치는 자기 토픽에만 쓸 수 있습니다(ACL).
