# 에러·경고·알람 단일 정립

시스템 전체 구조는 [croft-os.md](croft-os.md). 센서 Quality 분류는 [sensor.md](sensor.md) §7. 장치 상태 enum은 [croft-os.md](croft-os.md) §2 (DB Layer). Recipe 충돌은 [recipe.md](recipe.md) §1-5.

이 문서의 목적: 사람과 LLM이 **한눈에** 사건을 판단할 수 있도록, 시스템 곳곳(센서 Quality, 장치 상태 enum, sensor health status, NATS warning, HTTP 4xx, raise)에 흩어진 에러 개념을 **하나의 모델**로 모은다. 새 enum은 만들지 않는다. 기존 자산을 *변환*만 한다.

---

## 1. 한눈 판단의 6차원

모든 사건(Problem)은 다음 6 필드로 표현된다. 운영자/LLM/UI가 한 줄로 보고 즉시 판단 가능해야 한다.

| 필드 | 의미 | 값 |
|---|---|---|
| `what` | 무슨 사건인가 | 카탈로그 코드 (§2 후보) |
| `where` | 어디서 발생 | `(layer, module, compartment_id, device_id?)` |
| `severity` | 얼마나 심각 | `info` / `warning` / `error` / `critical` |
| `action` | 어떤 처치가 권고되나 | `auto_recover` / `require_human` / `failsafe` (라벨, §7) |
| `when` | 언제 | NTP 동기화 시각 ([croft-os.md](croft-os.md) Core `time`) |
| `event_id` | 동일 사건 묶음 | UUID. 1 사건 = 여러 표현이라도 같은 ID |

---

## 2. 분류 매트릭스 (잠정 — 첫 모듈 운영 전)

천창 + 환기온도 도메인에서 발생 가능한 카탈로그. 두 번째 모듈 시점에 보강.

| `what` 코드 | 1차 severity | 지속 시 escalate | `action` 라벨 | 발행 채널 |
|---|---|---|---|---|
| `sensor.quality_degraded` | warning | error | auto_recover → require_human | Problem |
| `sensor.communication_lost` | error | — | require_human | Problem |
| `recipe.stage_conflict` | warning | — | require_human | Problem **+** HTTP 422(저장 시) |
| `actuator.command_timeout` | error | — | require_human | Problem |
| `actuator.state_error` | error | — | require_human | Problem (장치 상태 enum과 동시 갱신, §6) |
| `compute.numerical_failure` | error | — | require_human | Problem |
| `config.invalid_input` | — | — | — | **Problem 채널 안 탐.** HTTP 422 응답으로 종결 |

`config.invalid_input`처럼 *동기 응답으로 끝나고 운영자 가시성 불필요*한 케이스는 Problem 채널을 타지 않는다 — 가벼움 유지를 위해 명시.

---

## 3. 상태(state) vs 사건(event) 분리

이 분리가 노이즈 통제의 핵심이다.

- **상태**는 cache에 항상 최신값으로 존재. UI 게이지·Control 알고리즘이 동기 조회.
- **사건(Problem)**은 *전이*나 *임계 조건*에서만 발행.

Quality → Problem 변환 규칙:

| 조건 | 결과 |
|---|---|
| GOOD → bad 첫 진입 | emit `sensor.quality_degraded` severity=warning |
| 연속 N회 또는 `expected_period_seconds × K` 지속 | escalate severity=error (같은 event_id로 갱신) |
| bad → GOOD 복구 | resolve(event_id) — info close 발행 |

`N`, `K`의 정확한 수치는 첫 모듈 운영 데이터로 결정 (§12).

escalate 트리거: 같은 `event_id`로 `emit()` 재호출 — `state.problems[event_id]` 덮어쓰기로 자동 갱신 (§10).

---

## 4. 레이어별 책임

| 사건 카테고리 | 감지 위치 | emit 위치 |
|---|---|---|
| Communication 단절 (Modbus polling timeout) | Communication Layer | Communication Layer |
| Quality 전이 / 지속 | Sensor Pipeline (Device Layer, [sensor.md](sensor.md) §9-1) | Sensor Pipeline |
| Recipe stage 충돌 (저장 시) | API Layer | API Layer |
| Recipe stage 충돌 (자정 재계산) | Control Layer (recipe 모듈) | Control Layer |
| Actuator command timeout | Control Layer (개별 모듈) | Control Layer |
| Actuator state error 전이 | Control 또는 PLC 응답 처리 함수 | **같은 함수에서 state update + Problem emit** (§6) |
| 산출 NaN / divide by zero | Control 알고리즘 | Control |

emit 진입점은 단일: `core.problem.emit(problem)`. 함수 1개가 log + NATS + state.problems 세 매체로 fan-out (§10).

---

## 5. NATS subject 규약

```
problem.{domain}.{kind}
```

- 전체 듣기 → `problem.>`
- 도메인별 듣기 → `problem.sensor.>`
- 도메인 = 사건이 발원한 의미 카테고리 (`sensor`, `recipe`, `actuator`, `compute`)

§2 카탈로그의 subject 매핑:

| `what` | NATS subject |
|---|---|
| `sensor.quality_degraded` | `problem.sensor.quality_degraded` |
| `sensor.communication_lost` | `problem.sensor.communication_lost` |
| `recipe.stage_conflict` | `problem.recipe.stage_conflict` |
| `actuator.command_timeout` | `problem.actuator.command_timeout` |
| `actuator.state_error` | `problem.actuator.state_error` |
| `compute.numerical_failure` | `problem.compute.numerical_failure` |

### 5-1. 기존 코드/정본의 흡수

기존 정본의 잠정 결정은 다음으로 정렬된다 (코드 사용처 없음 — 이름 변경 가능):

| 기존 | 새 정본 |
|---|---|
| `recipe.md` §1-5 `recipe.warning` NATS 이벤트 | `problem.recipe.stage_conflict` |
| `sensor.md` §9-1 `alarm_bus.publish(reading)` | `core.problem.emit(...)` → `problem.sensor.>` |
| `core/nats_client.py` `SUBJECT_RECIPE_WARNING` 상수 | 폐기. `problem.py`로 통합 |

---

## 6. 기존 enum 호환 표

새 enum을 만들지 않는다. 기존 자산을 그대로 두고 변환만 한다.

### 6-1. `sensor.md` §7-1 `Quality` ↔ Problem

| Quality 값 | Problem 발행 |
|---|---|
| `GOOD` | 발행 없음 (정상). 단, 직전이 bad였으면 resolve(event_id) |
| `STALE` | 1회 → warning · `sensor.quality_degraded` / 지속 → error |
| `OUT_OF_RANGE` | 1회 → warning · `sensor.quality_degraded` / 지속 → error |
| `SUSPECT` | 1회 → warning · `sensor.quality_degraded` / 지속 → error |
| `SENSOR_ERROR` | error · `sensor.quality_degraded` (즉시 escalate) |
| `NO_DATA` | error · `sensor.communication_lost` — Communication Layer가 발행 |

`sensor_quality` cache는 항상 갱신 (지금 정본 그대로). Problem은 그 위의 *변화 채널*.

### 6-2. `sensor.md` §7-4 sensor health snapshot status ↔ Problem

| status | Problem |
|---|---|
| `normal` | 발행 없음 |
| `delayed` | warning · `sensor.quality_degraded` (Quality.STALE과 동일 변환) |
| `error` | error · `sensor.quality_degraded` |
| `no_data` | error · `sensor.communication_lost` |

health snapshot은 *주기적 진단 결과*이고 Problem은 *실시간 사건*. 둘은 독립 트랙으로 운영하되, snapshot이 비정상으로 전이하면 emit (cron/주기 워커 책임).

### 6-3. `croft-os.md` §2 (DB Layer) 장치 상태 enum ↔ Problem

장치 상태 enum: `Ready, error, working, opening, closing, preparing, supplying, finishing`

| 상태 | Problem |
|---|---|
| `error` | 같은 함수에서 state update + `actuator.state_error` 또는 `actuator.command_timeout` emit (§7-3 권장 패턴) |
| 그 외 (운영 상태) | 발행 없음 |

상태는 *현재 모드*(DB 영속), Problem은 *원인 사건*(짧게 살아 있다 timeline으로). 같은 트랜잭션은 안 묶지만 같은 호출 경로에서 둘 다 처리하여 일관성을 코드 리뷰 수준에서 보장.

---

## 7. `action` 필드 — 분류 라벨, 자동 발동 X (1단계)

`action` ∈ {`auto_recover`, `require_human`, `failsafe`}

### 7-1. 의미

`action`은 *권고 분류*다. UI/운영자/LLM이 우선순위를 매기는 데 쓴다.

### 7-2. 자동 발동 정책

**1단계는 자동 발동 없음.** 이유:
- [croft-os.md](croft-os.md) §5: Safety는 PLC 책임. PC가 자동 failsafe 발동하면 책임 경계가 흐려진다.
- 자동 복구는 *해당 모듈*이 자기 책임으로 한다 ([sensor.md](sensor.md) §6-1 primary→fallback 자동 전환은 이미 모듈 책임). Problem 채널이 트리거하지 않는다.
- 자동 발동 정책은 첫 모듈 운영 데이터로 결정 — 추측으로 미리 깔지 않는다.

### 7-3. 권장 패턴 — 원인 함수에서 둘 다 처리

```python
# 개념 설명용 (실제 코드는 첫 모듈에서)
async def on_command_timeout(self, actuator_id):
    await self.actuator_mgr.set_state(actuator_id, "error")
    await emit(Problem(
        what="actuator.command_timeout",
        where=("control", "skywindow", compartment_id, actuator_id),
        severity="error",
        action="require_human",
    ))
```

state 변경 watcher 패턴(상태 변화를 감시해 Problem 자동 발행)은 **두 번째 액추에이터 모듈에서 재검토**. 첫 모듈엔 과한 인프라.

---

## 8. UI 단위 — 활성 목록(active list) + timeline

### 8-1. 활성 목록 (기본 화면)

- 같은 `event_id`의 *열려 있는* Problem만 노출.
- 라이프사이클: `open → (ack?) → resolved`
- 보존: in-memory `core/state.py` `problems: dict[event_id, Problem]`
- 운영자가 "지금 뭐가 문제인가"를 첫 화면에서 본다.

### 8-2. timeline (drill-down)

- 종료된 Problem 포함 전체 사건 스트림.
- 보존: TimescaleDB (스키마는 §12 미확정).

### 8-3. ack 단계는 잠정 보류

`open → resolved` 둘만으로 첫 모듈을 살린 뒤 ack 필요성 판단 (§12).

---

## 9. 안전 채널 분리 — Problem이 아닌 것

다음은 **Problem 채널을 타지 않는다**:

- E-Stop / 인터록 / 페일세이프 진입 — PLC 직결, [croft-os.md](croft-os.md) §5, [setpoint.md](setpoint.md) §하위 1·6.
- PLC 단절 시 fallback 자체 — PLC가 자율 진입.

PC가 PLC failsafe 진입을 *관찰*하면 visibility 차원에서 Problem로 발행 가능 (`severity=critical`, `action=failsafe` 라벨로). 그러나 Problem 채널이 failsafe를 *시킬 수는 없다*.

---

## 10. CORE 코드 자산 (목표 청사진)

```
croftos/core/
├── problem.py       # Problem dataclass + Severity/Action enum + emit() / resolve()
├── logging.py       # structured logger (Problem과 동일 6 필드 포맷)
├── metrics.py       # 시스템 부하 메트릭 envelope. event_id로 logging.py와 약결합 (logs ↔ metrics 점프)
└── state.py         # 기존 + problems: dict[event_id, Problem]
```

emit / resolve의 의도된 동작:

```python
# 개념 — 첫 모듈에서 진짜 호출처가 생길 때 작성
async def emit(problem: Problem) -> None:
    """log 한 줄 + NATS publish + state.problems 적재."""
    ...

async def resolve(event_id: str) -> None:
    """state.problems에서 제거 + NATS info close + DB timeline 적재."""
    ...
```

이 파일들은 명세된 진입점(§1, §10)만 만들었다. **카탈로그 항목과 호출처는 천창 모듈에서 검증·확정한다** ([croft-os.md](croft-os.md) §8-3 — 미리 추측해서 카탈로그를 늘리지 않는다).

**기술 선택**: 외부 라이브러리·SaaS 추가 0. stdlib `logging` + 자체 JSON formatter, FastAPI `HTTPException`, NATS, TimescaleDB만 사용. structlog · Sentry · OpenTelemetry 등은 첫 모듈 운영 후 *부족이 명백할 때* 재검토.

**개발자 디버깅 채널**: `core.logging.dev_log(msg, event_id=..., exc=False, **context)` — 환경변수 `CROFTOS_DEV_LOG=1` 활성 시 별도 파일(`CROFTOS_DEV_LOG_PATH`, 기본 `dev.log`)에 JSON 한 줄. 운영 stderr와 분리(`propagate=False`)되어 운영자/UI에 노출되지 않는다. Problem과 `event_id`로 상관관계 — 사건 발행 직전·직후에 호출자가 의도적으로 컨텍스트(입력값, 중간 상태, 호출 경로)를 dump하면 grep으로 한 줄에 묶인다. **자동 발동 X** (emit이 트리거하지 않음, 노이즈 차단). 비활성 환경에선 noop이라 운영 부담 0.

**두 채널은 같은 위치에서 호출 가능**: 한 함수 안에서 `emit(Problem)`(운영/UI 가시, 짧은 사용자 메시지)과 `dev_log`(개발자 raw 값·중간 상태·traceback)를 같은 `event_id`로 함께 부른다. 같은 사건이지만 *조건*(Problem은 항상 / dev_log는 env 토글)과 *표현*(운영자용 메시지 vs 개발자용 raw)이 다르다.

**관측 포인트 빈도 정책**:
- **저빈도** (이벤트 / 전이 / lifecycle) — 항상 기록. 예: 상태 전이(장치 enum, fallback 트리거), 외부 의존 응답(Modbus/PLC/DB/`astral`), 부팅·종료, 자정/누적 reset.
- **고빈도** (매 cycle / polling 주기) — 평상시 비기록. **Problem 발행 시점**에 호출자가 같은 `event_id`로 `dev_log`에 컨텍스트 dump. 예: hot path latency, 제어 계산 입력, 센서 변환 raw.
- 둘 모두 `CROFTOS_DEV_LOG=1` 토글에 종속 — 운영 환경 noop, 부하 0.

---

## 11. 첫 모듈(천창) 검증 시나리오

§2 카탈로그가 잘 짜였는지 검증할 5개 시나리오. 각각이 다음 4가지를 만족해야 통과:

1. log 한 줄에 6 필드(§1) 모두 보이는가?
2. UI active list에 한 행으로 나타나는가?
3. LLM이 raw NATS payload만 읽고 같은 6 필드 추출 가능한가?
4. 복구 시 active에서 빠지고 timeline에 남는가?

| # | 시나리오 | 예상 Problem |
|---|---|---|
| a | 외부 온도센서 통신 끊김 | `problem.sensor.communication_lost` severity=error, Communication Layer 발행 |
| b | RH 센서 OUT_OF_RANGE 1회 튐 | `problem.sensor.quality_degraded` severity=warning, 다음 reading GOOD이면 resolve |
| c | Recipe stage 순서 충돌 (저장 시점) | HTTP 422 응답 + `problem.recipe.stage_conflict` severity=warning |
| d | 천창 모터 명령 후 PLC 응답 timeout | `problem.actuator.command_timeout` severity=error, 같은 함수에서 device 상태 'error' 갱신 |
| e | `compute_active_setpoint` NaN | `problem.compute.numerical_failure` severity=error, 폴백: 어제 setpoint 유지 |

5개가 모두 통과한 뒤에야 §2 매트릭스와 §6 매핑 표를 *확정*으로 표시. 그 전엔 잠정.

---

## 12. 미확정 — 첫 모듈 검증 후 결정

추측으로 미리 결정하지 않는다 (CROFT-ENGINE §3).

- "지속" 임계값 (`N`회, `K`배수) 정확한 수치
- ack 단계 도입 여부
- timeline DB 스키마 (column 후보: `event_id`, `what`, `where_*`, `severity`, `action`, `opened_at`, `resolved_at`, `payload jsonb`)
- 두 번째 액추에이터 모듈에서 state-watcher 자동 발행 도입 여부 (§7-3 대안)
- 보존 정책 차등 — *환경 사건*(센서) vs *내부 결함*(compute) 보존 기간 다르게 갈지
- `severity=critical` 도입 기준 — 1단계는 사실상 `error`까지로 운영. critical은 PLC failsafe 진입 관찰 같은 명확한 트리거가 생길 때 추가.
- **관측 포인트 카탈로그** (저빈도 항목별 정확한 `dev_log` 호출처, `metric` 채널 도입 여부) — 첫 모듈 운영 시 *실제로 보고 싶었던 항목*만 추가하고 그때 정본화.
