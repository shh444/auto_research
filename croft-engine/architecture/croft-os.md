# Croft-OS 시스템 아키텍처

복합환경제어 시스템. IPC(Industrial PC) 위에서 동작하며 LS PLC를 통해 현장 하드웨어를 제어한다.

```
┌──────────────────────────┐                 ┌────────────┐                 ┌────────────┐
│   Climate Computer       │ ◄──Modbus/RS485──► │   LS PLC   │ ◄──Digital I/O──► │  Hardware  │
│   (Croft-OS · IPC)       │                 │  + Safety  │                 │ (팬, 모터, 센서) │
└──────────────────────────┘                 └────────────┘                 └────────────┘
```

핵심 결정:
- **결정 로직은 모두 PC(Croft-OS)에서.** PLC는 저수준 디지털 신호 변환과 모터 이동 그리고 Safety를 담당한다 ([setpoint.md](setpoint.md) §7 참고).
- **레이어 간 통신은 NATS.** 동기 호출이 아니라 비동기 이벤트로 흐른다.
- **Core는 모든 레이어가 import 가능, 단방향.**

---

## 1. 기술 스택

| 레이어 | 스택 |
|---|---|
| UI | React 19, TanStack Query, ECharts, shadcn/ui |
| API | FastAPI (REST/WS), OPC UA Server, Modbus TCP |
| Control | Python (screen, CO2, HeatingPipe, PID/ML 외) |
| Device | Python (Sensor Manager, Actuator Manager) |
| DB | TimescaleDB |
| Communication | Modbus TCP, Simulator Adapter, RS485 |
| Core | Python (logging, metrics, auth, security, feature flag, NTP) |
| Models | greenhouse(Vanthoor), crop, weather |
| Layer 간 통신 | NATS |

---

## 2. 6-Layer 구조

```
┌─────────────────────────────────────────────────────────────────┐
│                          UI Layer                                │
│   - Local Industrial Server 조회 (OPC UA)                        │
│   - 데이터 흐름 시각화 / 수동 제어                                │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                          API Layer                               │
│   - 실시간 데이터 조회 / 실시간 제어 명령                          │
│   - API Server · Command Mapper · Local Industrial Server         │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                        Control Layer                             │
│   - Program · DB 조회 · Optimal Control Module · Driver           │
│   - 제어 알고리즘 (PID, MPC, ML)                                   │
│   - 모듈 예: screen, CO2, HeatingPipe                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                         Device Layer                             │
│   ┌─ Sensor Manager ─┐         ┌─ Actuator Manager ─┐            │
│   │ 등록 · 입력 설정  │         │ 등록 · 출력 설정    │            │
│   │ 데이터 수집 → DB │         │ 데이터 송신          │            │
│   └──────────────────┘         └─────────────────────┘            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     Communication Layer                          │
│   - 데이터 변환 (단일 책임)                                       │
│   - 센서 데이터 표준화 / 특정 인터페이스 입출력                    │
│   - Modbus TCP · RS485 · Simulator Adapter                        │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼ (Modbus / RS485)
                          LS PLC + Hardware
```

DB Layer (TimescaleDB)는 별도 평면에서 모든 레이어가 접근.

### Sensor Manager (Device Layer)
- 센서 등록 / 데이터 입력 설정
- 통신 설정 / 장치 상태 설정
- 데이터 수집 → DB 저장

### Actuator Manager (Device Layer)
- 액추에이터 등록 / 데이터 출력 설정
- 통신 설정 / Control Layer 연결 설정
- 데이터 송신

### Communication Layer
- **단일 책임: 데이터 변환.** 다른 일은 하지 않는다.
- 센서 데이터 → 표준화된 도메인 데이터
- 특정 통신 인터페이스(Modbus TCP, RS485, Digital Output)로 입출력
- Simulator Adapter도 같은 인터페이스를 구현 → Control Layer는 시뮬과 현장을 구분하지 않음 (CROFT-ENGINE §1-5 결정론적 제어 루프)

### Control Layer
- 입출력 데이터 설정 / 제어 알고리즘 / 액추에이터 연동
- 구성 요소: Program · DB 조회 · **Optimal Control Module** · **Driver**
- 제어 모듈 예: screen, CO2, HeatingPipe (각 액추에이터 종류별 1 모듈)
- Setpoint 4단계 변환의 [2]→[3]→[4]를 모두 담당 ([setpoint.md](setpoint.md) §하위 5)

### API Layer
- 실시간 데이터 조회 / 실시간 제어 명령
- 구성 요소: API Server · **Command Mapper** · **Local Industrial Server (OPC UA)** · DB 조회
- 외부 시스템(클라우드, 다른 IPC)은 OPC UA Server를 통해 접근

### Database Layer — TimescaleDB
- **센서 데이터 테이블** (hypertable, 시계열)
- **장치 상태 정보 테이블** — 상태 enum: `Ready, error, working, opening, closing, preparing, supplying, finishing`
  - `error` 상태 전이는 [error.md](error.md) §6-3 매핑에 따라 `actuator.state_error` 또는 `actuator.command_timeout` Problem과 같은 함수에서 발행.
- Setpoint 관련 테이블은 [setpoint.md](setpoint.md) §하위 3 참고

### UI Layer
- Local Industrial Server 조회 (OPC UA Server 등)
- 데이터 흐름 시각화
- 수동 제어
- 코드 위치: `croftos/frontend/` (Python `croftos/layers/`와 sibling)

---

## 3. Core / Models (수직 관통)

**Core** (모든 레이어가 import 가능, 의존 방향 단방향):
- `logging` · `metrics` · `auth` · `config` · `security` · `time` · `feature flag` · `NTP`
- **State Cache**도 Core에 있음 ([setpoint.md](setpoint.md) §하위 3)
- `metrics` envelope `(ts, layer, metric, labels jsonb, value, event_id, commit_hash)` — TimescaleDB `core_metric_ts` hypertable. commit_hash는 빌드 시 `CROFTOS_COMMIT` env로 주입 (7자), 없으면 `dev-local`. sensor.md §4 `metric_timeseries`(센서 가공)와 책임 분리.

**Models** (도메인 모델, 주로 Control Layer가 사용):
- `greenhouse` (Vanthoor 모델 기반)
- `crop`
- `weather`

---

## 4. 레이어 간 통신 — NATS

- 결합도 최소화를 위해 동기 호출 대신 **이벤트 발행/구독**
- 주요 이벤트:
  - `setpoint.changed` — 사용자 의도 변경
  - `sensor.update` — 센서 값 갱신
  - `actuator.command` — 액추에이터 명령 발행
  - `state.transition` — 장치 상태 전이
- 새 레이어/모듈을 추가할 때 동기 호출보다 이벤트로 연결할지 먼저 검토

---

## 5. Hardware Interface

```
LS PLC
├─ 외부 모듈: 데이터 입출력 (Modbus / RS485)
└─ Safety: 인터록 / E-Stop / 페일세이프 진입은 자동, 해제는 사람이

Hardware (PLC 뒤)
├─ 액추에이터: 유동팬, 환기팬, 모터, ...
└─ 센서:       온도센서, 습도센서, ...
```

핵심 분담:
- **PC**: 모든 결정 (제어 알고리즘, 비대칭 분배, 예외 처리)
- **PLC**: 디지털 신호 변환, Safety 인터록, PC 단절 시 fallback ([setpoint.md](setpoint.md) §하위 1·6)

---

## 6. 데이터 흐름 (한 사이클)

```
                                    [센서]
                                       │
                Communication Layer ◄──┘  (표준화)
                          │
                          ▼
            Device Layer / Sensor Manager
                          │
                          ▼
                      DB Layer (TimescaleDB)
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
         API Layer ──→ UI       Control Layer
                                       │ (알고리즘 + Models)
                                       ▼
                              Actuator Group Command
                                       │
                                       ▼
                              개별 Actuator Output
                                       │
                                       ▼
                          Device Layer / Actuator Manager
                                       │
                                       ▼
                              Communication Layer (변환)
                                       │
                                       ▼
                                     PLC ─→ Hardware
```

병렬로:
- 모든 단계가 **NATS publish**하여 다음 단계가 구독
- Setpoint 변경은 DB + State Cache + NATS를 동시에 갱신 ([setpoint.md](setpoint.md) §하위 2)

---

## 7. 직교성 — 6-Layer + Compartment + Time

코드를 3개의 서로 직교하는 축으로 나눈다:

```
6 Layer (수직)         →  코드를 "무엇을 하는가"로 나눔
Compartment (수평 1)   →  코드를 "어디를 위해서"로 나눔
Time (수평 2)          →  코드를 "언제 적용되는가"로 나눔
```

> **용어**: 구획 단위는 `compartment` (Nexus와 통일). `zone`이 아니다 — [sensor.md](sensor.md) §1-2.

이 직교성의 결과:
- **새 compartment 추가** = 인스턴스 추가, 코드 변경 0
- **새 시간대 schedule** = `setpoint_schedule` 테이블 row 추가, 코드 변경 0
- **새 액추에이터 종류** = Control Layer에 모듈 1개, 다른 코드 변경 0

전제:
- 모든 테이블/캐시/NATS 토픽에 `compartment_id` 포함
- 1 compartment만 운영해도 `compartment_id="default"`로 시작
- Control 모듈은 `(farm_id, compartment_id)`별로 인스턴스화

---

## 8. 코드 레이아웃

```
croftos/
├── core/                          # Cross-cutting 기반층
│   ├── logging.py
│   ├── metrics.py
│   ├── auth.py
│   ├── config.py
│   ├── secrets.py
│   ├── feature_flags.py
│   ├── time.py
│   ├── nats_client.py             # NATS 추상화
│   ├── state.py                   # State Cache (모든 레이어 import)
│   └── sensor_health.py           # Health snapshot
│
├── models/                        # 도메인 라이브러리
│   ├── greenhouse/                # Vanthoor 등 (운영 MPC + 시뮬 공용)
│   ├── crop/
│   └── weather/
│
├── frontend/                      # UI Layer (TypeScript + React 19 + Vite + Tailwind + shadcn/ui + ECharts + TanStack Query)
│
├── layers/                        # 6-Layer 구현 (UI 제외, frontend/는 별도 톱레벨)
│   ├── api/
│   ├── control/
│   │   ├── base.py                # ControlModule 추상
│   │   ├── motorized_opening.py   # sky/side 공통 부모
│   │   ├── skywindow.py
│   │   ├── sidewindow.py
│   │   ├── ventilation.py
│   │   ├── heating.py
│   │   ├── co2.py
│   │   └── recipe.py              # User → Compartment 매핑
│   ├── device/
│   │   ├── sensor_manager.py      # 등록·메타
│   │   ├── sensor_pipeline.py     # 3-place fan-out
│   │   └── actuator_manager.py
│   ├── db/
│   │   ├── schema.py
│   │   ├── timeseries.py
│   │   ├── sensor_writer.py       # 비동기 배치 워커
│   │   └── migrations/
│   └── communication/
│       ├── adapters/
│       │   ├── base.py
│       │   ├── modbus.py
│       │   └── simulator/
│       │       ├── world_model.py
│       │       └── virtual_io.py
│       └── protocols/
│
├── tests/
└── deploy/
```

### 8-1. 다른 문서가 명시한 자산

| 위치 | 책임 | 근거 |
|---|---|---|
| `core/state.py` | State Cache. 필드: sensor_values, sensor_quality, derived_values, intents, group_commands, actuator_outputs | [sensor.md](sensor.md) §9-2, [setpoint.md](setpoint.md) §3·§하위 6 |
| `core/sensor_health.py` | Sensor health snapshot (14일 보관) | [sensor.md](sensor.md) §10 |
| `device/sensor_pipeline.py` | Hot path 3-place fan-out 단일 진입점 | [sensor.md](sensor.md) §9-1 |
| `db/sensor_writer.py` | Hot/Cold path 분리 — 비동기 배치 워커 | [sensor.md](sensor.md) §9-3 |
| `control/recipe.py` | [1] User Intent 생성 (스케줄 평가) → [2] Compartment Setpoint 매핑 | [recipe.md](recipe.md), [setpoint.md](setpoint.md) §하위 5 |

### 8-2. 보류된 폴더 결정 (첫 모듈에서 답)

- **`control/` 평탄 listing**: 청사진은 5개 모듈 가정. 실제 도메인은 20+ 모듈이므로 도메인 그룹(환기·난방·차광·시비) 또는 물리 패턴(motorized opening / modulating / pulsed / target value) 축으로 분기 필요. 천창 → 측창 → 수평커튼 순서로 짜며 motorized_opening 공통화 시점에 폴더 분기.
- **`simulator/` 위치**: 청사진은 `communication/adapters/simulator/` 안. 시나리오 엔진·시간 가속·가상 PLC가 더해지면 `simulation/` 별도 톱레벨로 빠질 수 있음. 천창 시뮬을 처음 돌릴 때 어댑터 안 구조가 깔끔한지가 답.

### 8-3. 추가 정책

이 레이아웃은 **목표 청사진**이지 빈 파일을 미리 까는 지시가 아니다 (CROFT-ENGINE §3 추측 금지). 첫 도메인 모듈(천창 권장)을 짜면서 필요한 파일만 점진적으로 추가하고, 두 번째 모듈에서 공통화가 진짜로 필요한지 확인한 뒤 추상 분리.
