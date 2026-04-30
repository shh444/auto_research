# Setpoint 아키텍처

두 개의 분리된 주제를 한 곳에 묶었다:
- **상위 1~6**: Setpoint를 *어디에 어떻게 저장*하는가 (3-place storage)
- **하위 1~8**: Setpoint *값의 4단계 위계* (User Intent → 모터 회전)

서로 직교하는 관심사지만 둘 다 "setpoint"라는 한 단어로 통한다.
시스템 전체 구조는 [croft-os.md](croft-os.md) 참고. 센서 측의 동일한 3-place 패턴 + LogicalVariable 카탈로그는 [sensor.md](sensor.md) 참고.

> **용어 통일**: 구획 단위는 `compartment` 

---

# 상위. 저장 아키텍처 — 어디에 둘 것인가

## 1. 3곳에 두지만 각 곳의 역할이 다르다

### 위치 1. TimescaleDB — Source of Truth

- 모든 setpoint 이력의 원본
- 누가 언제 어떻게 바꿨는지 영구 보관
- 1년치 schedule 저장 가능
- 빠르긴 하지만 디스크 IO가 있어서 매 제어주기마다 조회는 비효율

### 위치 2. In-Memory State Cache (PC 메모리) — 읽기 속도의 답

- Python 프로세스 안의 dict 또는 클래스 객체
- 구조: `cache[compartment_id][actuator_group_id]` → 현재 활성 setpoint
- 모든 레이어가 마이크로초 단위로 접근
- DB 쓰기 시 캐시도 함께 갱신 (write-through)
- PC 시작 시 DB에서 한 번 로드

### 위치 3. PLC Schedule Buffer — PC 죽어도 살아남는 fallback

- PC가 1일 1회 다음 24시간치를 PLC에 통째로 푸시
- PC가 매분 라이브 업데이트도 보내지만, 끊겨도 PLC는 자기 buffer로 진행
- 2일+ 끊기면 마지막 buffer 반복

---

## 2. 세 곳을 동기화하는 흐름

```
┌─ 사용자가 UI에서 setpoint 수정 ─┐
                                 │
                                 ▼
                          ┌─ API Layer ─┐
                          │ (FastAPI)    │
                          └──────┬──────┘
                                 │
                ┌────────────────┼─────────────────┐
                ▼                ▼                 ▼
         ┌─ TimescaleDB ─┐  ┌─ State Cache ─┐  ┌─ NATS publish ─┐
         │ INSERT row    │  │ dict 갱신     │  │ setpoint.changed│
         │ (영구 기록)   │  │ (즉시 반영)   │  │ (구독자에게)    │
         └───────────────┘  └───────────────┘  └────────┬───────┘
                                                        │
                                            ┌───────────┴─────────────┐
                                            ▼                          ▼
                                    ┌─ Control Layer ─┐       ┌─ PLC Sync 모듈 ─┐
                                    │ 다음 사이클     │       │ Modbus 쓰기     │
                                    │ 새 setpoint 적용│       │ PLC buffer 갱신 │
                                    └─────────────────┘       └─────────────────┘
```

**3곳이 다른 책임을 진다** — 영속화, 빠른 조회, 안전 fallback. 한 곳에 몰면 그 곳이 죽으면 다 망함.

---

## 3. 6-Layer 안에서의 위치

| 위치 | Layer | 역할 |
|---|---|---|
| TimescaleDB row | DB Layer | 영속화 |
| State Cache | Core 기반층 (`core/state.py`) | 모든 레이어가 import |
| PLC Schedule Buffer | Communication Layer (Modbus 어댑터가 관리) | PC↔PLC 동기화 |
| Setpoint 변경 API | API Layer | UI/외부 진입점 |
| 변경 → 3곳 동기화 로직 | Control Layer 또는 Recipe 모듈 | 비즈니스 룰 적용 |

**핵심: State Cache는 Core 기반층에 둔다.** 이게 왜 중요한가:

- UI가 보고 싶을 때 → API → Cache (즉시 응답)
- Control이 제어하려 할 때 → Cache (지연 0)
- 새 setpoint 들어왔을 때 → DB + Cache + NATS publish 동시
- Cache는 모든 레이어가 import 가능 (Core이니까)

만약 State Cache를 어느 한 Layer 안에 두면, 다른 Layer가 그 Layer에 의존하게 되어 결합도가 깨진다.

---

## 4. State Cache의 성능과 안전성

### 왜 빠른가
- 메모리 dict 조회는 O(1), nanoseconds 단위
- DB 쿼리는 milliseconds
- **차이 = 1만 배**

### 왜 안전한가
- `asyncio.Lock`으로 동시성 보호
- DB와 Cache가 어긋나면 PC 재시작 시 DB에서 다시 로드
- DB가 여전히 SSOT (진실 공급원)

---

## 5. 사용자가 UI에서 setpoint를 빠르게 보는 흐름

```
브라우저 ─GET /setpoints/active?compartment=c1─→ FastAPI
                                                   │
                                                   ▼
                                          setpoint_cache.get_compartment(farm, "c1")
                                                   │ (메모리 조회 1ms 미만)
                                                   ▼
                                              JSON 응답 ─→ 브라우저
```

### 왜 DB가 아니라 Cache에서 읽는가
- 활성 setpoint = 현재 적용 중인 값. 이건 *지금 이 순간*의 상태
- 이력(과거 schedule, 변경 로그)은 DB에서 읽음
- TanStack Query가 5초 polling해도 Cache 조회는 무부하

### 과거 schedule을 보고 싶을 때
- DB에서 직접 조회 (TimescaleDB의 hypertable이 인덱스로 빠름)
- 1주일치도 100ms 내 응답

---

## 6. PLC 동기화의 구체 모양

이게 *"DB와 PLC가 같은 개념으로 보관"*의 답이다.

### 동기화 전략

```
PLC 메모리 안에:
  schedule_buffer[24][actuator_group_count]  (D-area에 매핑)

  D1000~D1023: compartment1 천창 24시간 setpoint
  D1024~D1047: compartment1 환기 24시간 setpoint
  D1048~D1071: compartment1 난방 24시간 setpoint
  ...

PC 동기화 모듈:
  매일 00:00 → 다음 24시간치 통째로 Modbus Write
  매분         → "현재 시각의 setpoint" 한 값 업데이트 (Live override)
  변경 발생 시 → 해당 row만 즉시 업데이트
```

### Atomic 보장

PC가 schedule을 PLC에 쓰는 도중 통신이 끊기면 **반쪽짜리 schedule**이 남으면 안 된다. 해결법:

1. PC: `schedule_version = N+1` 으로 새 영역에 쓰기 시작
2. PC: 모든 데이터 쓰기 완료
3. PC: PLC의 `active_version` 레지스터를 `N+1`로 변경 (마지막 1바이트)
4. PLC: `active_version` 보고 새 영역 사용

**마지막 1바이트 변경이 atomic** — 그 전에 끊기면 PLC는 여전히 N 버전 사용. 데이터베이스 트랜잭션과 같은 원리.

---

## 정리 — 저장 위치

| 위치 | Layer | 역할 |
|---|---|---|
| **TimescaleDB** | DB Layer | SSOT, 영속화, 이력 |
| **State Cache** (Python dict) | Core 기반층 | 빠른 조회, 모든 레이어 공유 |
| **PLC Schedule Buffer** | Communication Layer (Modbus 어댑터) | 통신 단절 fallback |

속도 보장:
- UI 조회 → State Cache (sub-ms)
- 이력 조회 → TimescaleDB hypertable 인덱스 (수십 ms)
- 변경 전파 → NATS publish (즉시 모든 레이어에)

Setting Point + VIP Point:
- 별도 테이블 만들지 말고 `priority` 컬럼 하나로 해결
- 가장 높은 priority의 활성 row가 적용됨

Compartment 처리:
- 모든 테이블/캐시/NATS 토픽에 `compartment_id` 포함
- 1 compartment만 운영해도 `compartment_id="default"`로 시작
- Control 모듈은 `(farm_id, compartment_id)`별로 인스턴스화

---

# 하위. 4단계 값의 위계 (Setpoint Hierarchy)

## 1. 4단계 정의

```
[1] User Setpoint (사용자 의도)
       ↓ "20°C로 유지하고 싶다"

[2] Compartment Setpoint (제어 목표)
       ↓ Control Layer가 해석한 "이 compartment의 온도 목표 = 20°C"

[3] Actuator Group Command (그룹 지시)
       ↓ 천창 30% 열고, 환기팬 50% 가동, 난방 정지

[4] Individual Actuator Output (개별 모터 명령)
       ↓ 천창 모터1 = 30%, 천창 모터2 = 30%, 천창 모터3 = 30%
       (또는 비대칭: 풍향 보정으로 모터1 = 35%, 모터2 = 25%)
```

각 단계는 **위에서 아래로 변환**된다. 사용자는 모든 단계를 보고 싶어 한다.

---

## 2. 누가 어느 단계를 결정하는가

| 단계 | 결정 주체 | 위치 |
|---|---|---|
| [1] User Setpoint | 사람 (UI 입력) 또는 Recipe | DB + Cache |
| [2] Compartment Setpoint | Recipe Layer가 [1]을 compartment에 매핑 | DB + Cache |
| [3] Actuator Group Command | Control Layer (PID/MPC 알고리즘) | Cache + NATS |
| [4] Individual Output | Control Layer (분배 로직) | Cache + NATS |
| [4] 모터 회전수 변환 | PLC (저수준 디지털 출력) | PLC 내부 |

**[4]까지 PC가 결정**하고, PLC는 그 명령을 받아 모터를 *몇 바퀴 돌릴지*만 처리.

---

## 3. 데이터 모델 — 단계별 분리 저장

이 4단계를 DB에 한 테이블에 다 넣으면 의미가 섞인다. **3개 테이블로 분리**:

```sql
-- [1], [2]: 사용자/Recipe가 정한 의도
TABLE setpoint_intent (
    compartment_id, target_time, variable, value, priority, source, ...
)
-- 예: compartment1, 14:00, "temperature", 20, 0, "recipe"

-- [3]: Control Layer가 산출한 그룹 명령
TABLE actuator_group_command (
    compartment_id, actuator_group_id, issued_at, value, reason
)
-- 예: compartment1, "skywindow_north", 14:00:05, 30, "온도 22도, 목표 20도"

-- [4]: 개별 액츄에이터에 나간 명령
TABLE actuator_output (
    actuator_id, issued_at, value, source_command_id
)
-- 예: motor_sw_n_1, 14:00:05, 30, ...
-- 예: motor_sw_n_2, 14:00:05, 30, ...
-- 예: motor_sw_n_3, 14:00:05, 30, ...
```

### 왜 이렇게 분리하는가
- 사용자가 "내가 설정한 값" 보고 싶을 때 → [1] 조회
- "지금 어떻게 제어하기로 결정했지?" 보고 싶을 때 → [3] 조회
- "실제 모터들이 어떻게 돌고 있지?" 보고 싶을 때 → [4] 조회
- 각 단계의 시계열을 따로 분석 가능 (학습 데이터로도)

---

## 4. 변환 흐름 — 결정 사슬 (Decision Chain)

```
사용자: "온도 20도"
    │
    ▼
Recipe Layer
  compartment1 setpoint(temperature) = 20  ◄── DB 저장 [1][2]
    │
    ▼
Control Layer (1분마다)
  현재 compartment1 온도 = 22도
  알고리즘 판단: 천창 열기 30%, 환기 50%, 난방 OFF
                                       ◄── DB 저장 [3]
    │
    ├─ 천창 그룹: 30%
    │     │
    │     ▼
    │   개별 모터 변환
    │     motor1 = 30%, motor2 = 30%, motor3 = 30%
    │                                ◄── DB 저장 [4]
    │     │
    │     ▼
    │   Actuator Manager → Modbus 쓰기
    │
    ├─ 환기 그룹: 50%
    └─ 난방 그룹: OFF
```

**결정의 모든 단계가 추적 가능.** 나중에 "왜 이때 천창이 열렸지?"를 물으면 시간을 거슬러 올라가며 [4] → [3] → [2] → [1]로 답을 찾을 수 있다.

---

## 5. 6-Layer 안에서의 위치

| 단계 | 어느 Layer가 다루나 |
|---|---|
| [1] User intent | API Layer (입력) → DB Layer (저장) |
| [2] Compartment setpoint | Recipe (Control Layer 안의 sub-module) |
| [3] Group command | Control Layer (PID/MPC 산출) |
| [4] Individual output | Control Layer (분배 로직) → Device Layer (Actuator Manager) |

→ **Control Layer가 [2]→[3]→[4]를 모두 담당.**

---

## 6. Cache 구조 — 단계별 분리

State Cache도 단계별로 분리해서 저장한다:

```python
# core/state.py
class StateCache:
    intents:           dict           # [1][2] 사용자 의도
    group_commands:    dict           # [3] 그룹 명령
    actuator_outputs:  dict           # [4] 개별 출력값
    sensor_values:     dict           # 실측 센서값  
```


UI는 4가지 모두 한 화면에서 보여줄 수 있음:

```
┌──── Compartment 1 대시보드 ────────────────────────┐
│                                                     │
│ 목표 온도:    20°C    (Recipe, 14:00 적용)          │ ← [1][2]
│ 현재 온도:    22°C    (15분 전 22.3°C에서 하락 중) │ ← sensor
│                                                     │
│ ▼ Control 결정                                      │ ← [3]
│   천창 그룹:  30% 열림                              │
│   환기 그룹:  50%                                   │
│   난방 그룹:  OFF                                   │
│                                                     │
│ ▼ 실제 출력                                         │ ← [4]
│   천창 모터1:  30% (정상)                           │
│   천창 모터2:  30% (정상)                           │
│   천창 모터3:  29% (1% 편차)                        │
│   환기팬1:    50% (정상)                            │
│   환기팬2:    50% (정상)                            │
└─────────────────────────────────────────────────────┘
```

*"내가 설정한 값은? 액츄에이터가 어떻게 돌았지?"* 가 정확히 이 화면.

---

## 7. 비대칭 분배 — Control Layer가 PC에 있어야 하는 이유

[3]→[4] 변환에서 천창 3개를 다 30%씩 균등하게 줄 수도 있지만, **비대칭 분배**가 가능하다는 게 PC에서 결정하는 진짜 가치다:

- **풍향 보정**: 북풍이 강하면 북측 천창은 20%, 남측은 40%
- **습도 편차 보정**: 한쪽이 더 습하면 그쪽 환기팬을 더 가동
- **장비 마모 균등화**: 모터1을 30번 썼으면 다음엔 모터2를 우선 가동

이런 룰들이 [3]→[4] 변환 함수 안에 들어간다. PLC에 있으면 절대 못 하는 일이고, **Control Layer가 PC에 있어야 하는 결정적 이유**.

```python
# Control Layer 안의 분배 로직
class SkyWindowControl:
    def distribute(self, group_command: float, context: dict) -> dict[str, float]:
        """30% 그룹 명령을 개별 모터로 분배"""
        wind_dir = context["wind_direction"]
        if wind_dir == "north" and context["wind_speed"] > 5:
            return {
                "motor_north": group_command * 0.5,
                "motor_south": group_command * 1.2,
                "motor_west":  group_command,
            }
        return {m: group_command for m in self.motors}
```

이 함수가 [3] → [4]의 핵심.

---

## 8. 전체 흐름 정리

```
                 ┌─ 사용자 / Recipe ─┐
                 └───────┬───────────┘
                         │ "20°C 유지"
                         ▼
            [1][2] User/Compartment Intent
                         │
                  ┌──────┴──────┐
                  ▼             ▼
              DB intent     Cache.intents
                                │
                                │ NATS publish: "intent.changed"
                                ▼
                    Control Layer (1분 주기)
                         │
                         │ 알고리즘
                         ▼
                  [3] Group Command
                         │
                  ┌──────┴──────┐
                  ▼             ▼
              DB command   Cache.group_commands
                                │
                                ▼
                     Control 분배 로직
                  (풍향·마모·습도 보정)
                         │
                         ▼
                  [4] Individual Output
                         │
                  ┌──────┴──────┐
                  ▼             ▼
              DB output   Cache.actuator_outputs
                                │
                                ▼
                     Actuator Manager
                                │
                                ▼ Modbus
                              PLC
                                │
                                ▼ Digital Out
                              모터 회전
```

핵심:
- 사용자 의도 → 그룹 명령 → 개별 명령 → 모터 회전 (4단계 변환)
- 각 단계가 DB와 Cache에 동시 저장 → 사용자가 모든 층을 볼 수 있음
- NATS는 "변경 이벤트"를 흘려 다음 단계가 즉시 반응
- PLC는 [4]를 받아 디지털 신호로만 변환

---

## 결론

**"사람이 보는 값"과 "실제 나가는 값"은 4단계로 분리해서 각 단계를 모두 저장(DB)하고 캐싱(Cache)한다.** 사용자는 한 화면에서 모든 단계를 볼 수 있고, 결정의 사슬이 추적 가능하다.
