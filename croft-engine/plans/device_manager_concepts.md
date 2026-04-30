# Device Manager — 개념 정의

> **상태**: 갱신 (2026-04-28, Sonnet 4.6 — 사용자 합의 누적 반영)
> **위치**: `croft-engine/plans/device_manager_concepts.md` (계획 — 정본 아님)
> **목적**: D1~D6 전 Phase의 *개념 SSOT*. 레이어·자리·Manager·정책·라벨의 정의와 연결을 못박는다.
> **정본 참조**: `architecture/croft-os.md §2·§4·§7·§8`, `architecture/sensor.md §5·§9`, `architecture/setpoint.md §하위 4·5·7`

---

## 0. 이 문서의 역할

`device_manager.md`(Phase plan)을 시작하기 전, 시스템이 *어떤 변화에도 견고히 동작*하도록 개념·자리·연결을 못박는다. 이 문서가 흔들리면 D2 스키마·D3 Registry·D4 통신이 모두 흔들린다.

원칙 (사용자 메모리 + 합의):
- **자동 추출 우선** — 추측 금지. 코드에 박힌 사실에서 출발
- **시스템 완성도** — 갭 처리·정책 적용은 *명시 등록*만 인정. 묵시적 fallback 금지
- **자산 절제** — 미래 카탈로그 추측 X. 현재 코드에 있는 것만 다룸
- **확장에 열림, 변경에 닫힘** — 미래의 변경은 *자리 5개* 안에서 흡수
- **레이어 의존성 한 방향** — 도메인 코어가 외부를 절대 모름

본 문서는 *DB 저장 구조*는 다루지 않는다. 별 § *DB 영역*에서 후속 정리 (현재 보류).

---

## 1. 핵심 추상 — 7종

### 1-1. Farm

**정의**: 농장 1개. 위경도·타임존 등 *외기에 공통적인 사실*을 담는 최상위 단위.

**SSOT 위치**: `croftos/layers/db/schema.py:FARM_DDL` + `architecture/croft-os.md §7`

**역할**:
- 외기 LV(`TEMP_OUTSIDE` 등)의 scope 단위. 한 농장 안에선 외기 동일.
- 여러 동(compartment)을 묶음.

### 1-2. Compartment

**정의**: 물리적 동(구획). 실내 환경이 독립적으로 측정·제어되는 단위.

**SSOT 위치**: `croftos/layers/db/schema.py:COMPARTMENT_DDL` + `architecture/croft-os.md §7`

**역할**:
- 실내 LV(`TEMP_AIR`, `RH` 등)의 scope 단위.
- 1개 농장 = N개 compartment. v0은 `default` 1개.

### 1-3. LogicalVariable (LV)

**정의**: 시간에 따라 변하는 *측정량의 추상명*. 출처(센서/모델/외부)와 무관한 키.

**SSOT 위치**: `croftos/core/logical_variable.py` (현재 18 enum), 정본 `sensor.md §5`.

**메타 (제안 — D2 도입)**:
- `scope: Scope` — `FARM` 또는 `COMPARTMENT`
- `unit: str`
- `category: Category` (ENVIRONMENT / LIGHT / DERIVED)

**역할**:
- sim/real 양쪽이 동일 키로 StateCache에 기록.
- ControlModule이 의존성 선언(`REQUIRES_SENSORS`, `REQUIRES_DERIVED`)에 사용.
- 부팅 시 가용 LV ⊇ REQUIRES 검증 (현재 `validate_module_dependencies` 일부 구현).

**현재 18 LV 분류**:
- ENVIRONMENT (8): TEMP_AIR, TEMP_OUTSIDE, RH, RH_OUTSIDE, CO2, PRESSURE, WIND_SPEED, WIND_DIRECTION
- LIGHT (5): RADIATION_OUTSIDE, RADIATION_INSIDE, PAR_OUTSIDE, PAR_INSIDE, PPFD
- DERIVED (5): VPD_LEAF, VPD_AIR, DEW_POINT, ABSOLUTE_HUMIDITY, HUMIDITY_DEFICIT

### 1-4. SensorDevice

**정의**: 1개 이상의 LV를 *publish*하는 물리/가상 장치.

**관계**: `SensorDevice (1) ↔ (N) LogicalVariable`. 한 장치가 여러 LV 채울 수 있음 (예: temp+rh 복합 센서).

**scope**:
- `FARM` scope SensorDevice — 외기 센서 등 (1개 농장에 1세트)
- `COMPARTMENT` scope SensorDevice — 실내 센서 (동마다)

**제약**: 1 LV ← N SensorDevice 시 *충돌*. 부팅 검증에서 차단.

**예 (현재 코드)**:
- 시뮬: `WorldAdapter`(1개)가 12 LV publish. 논리적 가상 SensorDevice 1개로 취급.
- 운영: 미구현.

### 1-5. ActuatorGroup

**정의**: 함께 제어되는 액추에이터 묶음. *제어의 최소 단위*.

**핵심 속성**: `compartments: list[str]` — 어느 동들에 영향. 1개=동별 독립, N개=공통 제어.

**예**:
- `skywindow_north` — `compartments=[a]` (동별)
- `boiler_main` — `compartments=[a, b, c]` (3동 공통 보일러)

### 1-6. ActuatorDevice

**정의**: 그룹 명령을 받아 분배된 개별 명령을 *consume*하는 물리/가상 장치.

**관계**: `ActuatorDevice (N) → (1) ActuatorGroup`

**예 (현재 코드)**:
- `motor_sw_n_1` (kind=motor, group=`skywindow_north`)
- `valve_h_1` (kind=valve, group=`heating_pipe_main`)

### 1-7. Source (변수 출처) — 6종 카탈로그

**정의**: 한 LV가 *어떻게 채워지는가*에 대한 분류. **명시 등록만 인정** (§7 갭 정책).

| Source | 의미 | 시뮬 vs 실 |
|--------|------|-----------|
| **MEASURED** | 센서가 직접 측정 | 양쪽 |
| **MODEL_OUTPUT** | 도메인 모델 산출 (ClimateModel·TomatoCrop) | 시뮬 주, 실은 Phase 12+ |
| **DEVICE_COMMAND** | 액추에이터 명령값 echo (readback 포함) | 양쪽 |
| **EXTERNAL** | 외부 API (KMA 등) | 양쪽 |
| **CALCULATED** | 다른 LV로 즉시 계산 (L2 derived) | 양쪽 (동일 공식) |
| **CONSTANT** | 명시 고정값 (rationale 필수) | 시뮬/실 갭 메우기 |

> 명칭 변경 이력: COMMAND_ECHO → DEVICE_COMMAND, DERIVED_L2 → CALCULATED (사용자 합의).

### 1-8. Parameter (변수 아님 — 구분)

**정의**: 시뮬/실 *기간 내 고정*인 값. 시계열 아님.

| | LogicalVariable | Parameter |
|---|------|---------|
| 시간 따라 변화 | 예 | 아니오 |
| StateCache 기록 | 예 | 아니오 |
| 갭 처리 | `ConstantSource(LV→값)` 등록 → LV로 채움 | 모델·디바이스 생성자에 직접 주입 |
| 예 | TEMP_AIR, RH | 작물 종, 온실 면적, LAI 초기값 |

---

## 2. 6-Layer 책임 — 디바이스 관점 풀이

정본 `croft-os.md §2`의 6-Layer를 *Device Manager 관점*에서 다시 풀이. 정의를 *바꾸지 않는다*. 풀어쓸 뿐.

### UI Layer (Frontend)

**하는 일**: 보여주기·받아쓰기. 사용자 입력 → API 호출.

**안 하는 일**: 결정. 알고리즘. 캐시 키 알기. 통신 프로토콜 알기.

### API Layer

**하는 일**: 요청 검증·인증·내부 자산 위임·외부 안전 직렬화·외부 변경 NATS 발행.

**안 하는 일**: 결정. 통신 프로토콜 알기. 장치 라이프사이클 직접 관리.

### Control Layer

**하는 일**: 의도(setpoint) + *이미 정리된 입력* → 그룹 명령. 알고리즘이 사는 *유일한 곳*.

**안 하는 일**: 
- 센서값 출처 알기
- 통신 프로토콜 알기
- 장치 등록 정보 직접 보기
- 컴파트먼트 개수 알기 (라벨이 SISO일 때 자동 보장)

### Device Layer

**하는 일**: 장치 등록·상태·메타·바인딩·라우팅. Sensor Manager + Actuator Manager.

**안 하는 일**: 결정(알고리즘). 변환(프로토콜 ↔ 도메인 값).

### Communication Layer

**하는 일**: *오직 데이터 변환*. 도메인 값 ↔ 프로토콜 비트열.

**안 하는 일**: 결정. 등록부 관리.

### DB Layer

**하는 일**: 영속화. 시계열·마스터·이력.

**안 하는 일**: 결정. 캐시(Core가 함). 변환.

### Core (수직 관통)

**하는 일**: StateCache, NATS bus, AppContext, LogicalVariable 카탈로그, 로그·메트릭.

**규칙**: Core는 *어느 레이어도 import 안 함*. 모든 레이어가 Core를 import 함.

### Models (수직 관통)

**하는 일**: greenhouse / crop / weather 도메인 모델. 시뮬·운영 공용.

**누가 쓰나**: 주로 Control + 시뮬 World.

---

## 3. 자리(Extension Point) 5종

미래 변경이 들어오는 *자리*들. 각 자리는 *추상 인터페이스 1개*와 *Manager 등록 메커니즘 1개*로 표현된다. 새 종류 추가 = 클래스 1개 + 등록 1줄.

### 자리 1 — Sensor Input Strategy

**무엇이 바뀌나**: 여러 컴파트먼트 값을 알고리즘에 어떻게 전달할지 — 합치기/그대로/가중평균/...

**추상 인터페이스 (가칭)**:
```
SensorInputStrategy
├─ resolve(raw_values, context) → 알고리즘 입력 형태
└─ 호환 라벨 (어느 INPUT_LABEL 알고리즘과 맞는지)
```

**소속 레이어**: Device Layer (Sensor Manager).

**현재 카탈로그 (v0 시작)**:
- `AGGREGATED-AVG` — 평균
- `AGGREGATED-MAX` — 최댓값 (보수적)
- `AGGREGATED-MIN` — 최솟값 (적극적)
- `PER_COMPARTMENT` — 합치지 않고 dict[comp_id, value] 그대로

추가 보류: `AGGREGATED-WEIGHTED`(가중평균), `REPRESENTATIVE`(대표 동) — 필요 시점에 도입.

### 자리 2 — ControlModule 알고리즘

**무엇이 바뀌나**: 도메인 알고리즘 — 천창·난방·CO2·차광·관수·...

**추상 인터페이스 (현재 코드 + 확장 제안)**:
```
ControlModule
├─ REQUIRES_SENSORS · REQUIRES_DERIVED   ← 입력 의존성 선언
├─ INPUT_LABEL                            ← 자리 1 호환성 (AGGREGATED / PER_COMPARTMENT)
├─ OUTPUT_LABEL                           ← 자리 3 호환성 (UNIFORM / WEIGHTED / ROLE_BASED)
├─ compute_group_command(intent, prepared_inputs) → GroupCommand | list[ActuatorOutput]
└─ distribute(cmd, ctx) → list[ActuatorOutput]    (UNIFORM/WEIGHTED 라벨일 때만)
```

**소속 레이어**: Control Layer (`croftos/layers/control/base.py`).

### 자리 3 — Distribution Strategy

**무엇이 바뀌나**: 그룹 명령 → 개별 장치 분배 방식.

**추상 인터페이스**:
```
DistributionStrategy
├─ distribute(group_command, devices, context) → list[ActuatorOutput]
└─ 호환 라벨 (UNIFORM / WEIGHTED / ROLE_BASED)
```

**소속 레이어**: Device Layer (Actuator Manager) — 단 *역할 기반*은 알고리즘이 직접 list 산출.

**현재 카탈로그 (v0 시작)**:
- `UNIFORM` — 균등 분배 (현재 기본)
- `WEIGHTED` — 등록된 가중치대로 분배 (`actuator_device.distribution_weight` 활용)
- `ROLE_BASED` — 알고리즘이 직접 산출. Distribution Strategy는 *위임만*

### 자리 4 — Communication Adapter

**무엇이 바뀌나**: Modbus / MQTT / BLE / 시뮬 World / ...

**추상 인터페이스 (가칭)**:
```
CommunicationAdapter
├─ send(actuator_id, value, binding)
├─ readback(actuator_id, binding) → value | None
└─ 라벨 (protocol_kind: modbus / mqtt / sim / ...)
```

**소속 레이어**: Communication Layer (`croftos/layers/communication/adapters/`).

**현재 카탈로그**: `modbus` (stub), `simulator-world` (간접 — WorldAdapter가 구현).

### 자리 5 — DataSource (Source 종류)

**무엇이 바뀌나**: §1-7 6종 출처. 새 데이터 출처(예: 새 외부 API) 도입 가능.

**추상 인터페이스 (가칭)**:
```
DataSource
├─ publishes() → frozenset[LV]              ← 이 출처가 채우는 LV 집합
├─ tick(when, cache)                         ← 매 tick에 cache에 값 쓰기
└─ source_kind                               ← MEASURED/EXTERNAL/CALCULATED/CONSTANT/...
```

**소속 레이어**: Device Layer + 외곽 (Communication 또는 Models).

**현재 카탈로그**:
- 시뮬: `WorldAdapter` (MODEL_OUTPUT) + `WeatherSource` (EXTERNAL — KMA 과거)
- 운영: 미구현

---

## 4. Manager 6종

자리 5개를 *런타임에 선택·호출*하는 조정자(coordinator). 각 Manager는 등록·선택·호출 3가지를 책임.

| Manager | 책임 | 등록 대상 |
|---------|------|----------|
| **DeviceRegistry** | 장치·그룹·바인딩 라이프사이클 | SensorDevice, ActuatorDevice, ActuatorGroup, ControlModule 인스턴스 |
| **SensorResolver** | 알고리즘에 입력 전달 | SensorInputStrategy (자리 1) |
| **ControlOrchestrator** | 매 tick 알고리즘 호출 운영 | 활성 ControlModule 인스턴스 (자리 2) |
| **ActuatorManager** | 그룹 명령 → 장치 송신 | DistributionStrategy(자리 3) + CommunicationAdapter(자리 4) |
| **SensorPipeline** | 모든 데이터 출처 깨우기·cache 갱신 | DataSource(자리 5) |
| **CommunicationManager** | 통신 어댑터 라이프사이클 | CommunicationAdapter 인스턴스 |

**규칙**: 각 Manager는 *자리에 무엇이 꽂혀 있는지*만 알고, *그 종류의 내부*는 모른다. 알고리즘 코드는 Manager 종류를 통해 우회 호출 — 직접 구현체 import 금지.

---

## 5. 두 가지 흐름 — 센서 / 명령

레이어 간 연결을 *데이터 흐름*으로 본다.

### 흐름 ① 센서 (외부 → 내부)

```
실 센서 / 외부 API / 시뮬 World
         │
         ▼
[Communication]  raw → 도메인 float
         │
         ▼
[Device · Sensor Manager]
   - DeviceRegistry에서 장치 식별
   - SensorPipeline이 DataSource 호출
   - 결과: (scope, scope_id, LV, value, source_id, quality)
         │
         ▼
[Core · StateCache]
   farm_values[(farm_id, LV)]              ← FARM scope LV
   compartment_values[(comp_id, LV)]       ← COMPARTMENT scope LV
         │ + NATS publish "sensor.update"
         │
         ▼
[DB · 시계열]  영속화 (배치)
```

### 흐름 ② 명령 (내부 → 외부)

```
[API]  사용자 setpoint 변경 → NATS publish
         │
[Recipe]  setpoint → 현 시각 active 목표값
         │
         ▼
[Control · ControlOrchestrator]  매 tick
   for each active module:
     a) DeviceRegistry에서 module 등록 정보 조회
        (group, INPUT_LABEL, OUTPUT_LABEL, sensor_aggregation)
     b) SensorResolver.resolve(...) 요청
          INPUT_LABEL == AGGREGATED  → 정책으로 합쳐 단일값 dict 전달
          INPUT_LABEL == PER_COMPARTMENT → list 그대로 전달
     c) module.compute_group_command(intent, prepared)
          → GroupCommand (UNIFORM/WEIGHTED) 또는 list[ActuatorOutput] (ROLE_BASED)
     d) OUTPUT_LABEL에 따라 분기:
          UNIFORM → DistributionStrategy.uniform.distribute()
          WEIGHTED → DistributionStrategy.weighted.distribute()
          ROLE_BASED → module이 산출한 list 그대로 사용
         │
         ▼
[Device · ActuatorManager]
   for each ActuatorOutput:
     - DeviceRegistry에서 comm_binding 조회
     - CommunicationManager에 어댑터 라우팅
     - StateCache + NATS publish + DB 시계열 INSERT
         │
         ▼
[Communication]  도메인 float → 프로토콜 비트열
         │
         ▼
실 PLC / 가상 World
```

### NATS 이벤트 (정본 §4)

레이어 간 직접 호출 대신 이벤트 구독을 우선:

```
sensor.update      Device → Control · UI · DB · ...
actuator.command   Device → DB · UI · ...
setpoint.changed   API → Control
state.transition   Device → Alarm · UI
```

직접 호출은 *동기 응답이 필요할 때*만 (Manager 조회 등).

---

## 6. LV × Source 매핑 표

> **자동 추출 근거**:
> - `WorldAdapter.PUBLISHES` (`croftos/sim/world_adapter.py:42-57`)
> - `ClimateInputs/Outputs` (`croftos/models/greenhouse/state.py`)
> - `WeatherSource.sample` (`croftos/sim/world_adapter.py:118-125`)
>
> **표기**: `?` = 사용자 결정 필요, `(N/A)` = 현재 시뮬 미구현, `—` = 해당 없음.

### 6-1. ENVIRONMENT (8)

| LV | scope | 단위 | 시뮬 Source | 실 Source (제안) | 갭 정책 |
|----|-------|------|-------------|-----------------|--------|
| TEMP_AIR | COMPARTMENT | °C | MODEL_OUTPUT (ClimateModel) | MEASURED (실내 온도 센서) | 센서 필수 |
| TEMP_OUTSIDE | FARM | °C | EXTERNAL (WeatherSource — KMA 과거) | MEASURED 또는 EXTERNAL (KMA 실시간) | ? |
| RH | COMPARTMENT | % | MODEL_OUTPUT (ClimateModel) | MEASURED (실내 습도 센서) | 센서 필수 |
| RH_OUTSIDE | FARM | % | EXTERNAL (WeatherSource) | MEASURED 또는 EXTERNAL | ? |
| CO2 | COMPARTMENT | ppm | (N/A) — ClimateModel 미반영 | MEASURED (CO2 센서) | ? |
| PRESSURE | FARM | hPa | EXTERNAL (WeatherSource) | MEASURED 또는 EXTERNAL | ? |
| WIND_SPEED | FARM | m/s | EXTERNAL (WeatherSource) | MEASURED 또는 EXTERNAL | ? |
| WIND_DIRECTION | FARM | ° | EXTERNAL (WeatherSource) | MEASURED 또는 EXTERNAL | ? |

### 6-2. LIGHT (5)

| LV | scope | 단위 | 시뮬 Source | 실 Source (제안) | 갭 정책 |
|----|-------|------|-------------|-----------------|--------|
| RADIATION_OUTSIDE | FARM | W/m² | EXTERNAL (WeatherSource) | MEASURED (외기 일사계) 또는 EXTERNAL | ? |
| RADIATION_INSIDE | COMPARTMENT | W/m² | MODEL_OUTPUT (ClimateModel) | MEASURED (실내 일사계) | 없으면 CALCULATED `RAD_OUT × τ`? CONSTANT 차단막률? |
| PAR_OUTSIDE | FARM | µmol/m²/s | (N/A) | MEASURED 또는 CALCULATED (RAD × 2.02) | ? |
| PAR_INSIDE | COMPARTMENT | µmol/m²/s | (N/A) | MEASURED 또는 CALCULATED | ? |
| PPFD | COMPARTMENT | µmol/m²/s | (N/A) | MEASURED 또는 CALCULATED | ? |

### 6-3. DERIVED (5)

| LV | scope | 단위 | 시뮬 Source | 실 Source | 비고 |
|----|-------|------|-------------|----------|------|
| VPD_LEAF | COMPARTMENT | kPa | (N/A) | CALCULATED (TEMP_LEAF + RH) | TEMP_LEAF 변수 추가 필요? |
| VPD_AIR | COMPARTMENT | kPa | MODEL_OUTPUT (ClimateModel L2) | CALCULATED (TEMP_AIR + RH) | 시뮬·실 동일 공식 권장 |
| DEW_POINT | COMPARTMENT | °C | MODEL_OUTPUT (ClimateModel L2) | CALCULATED (TEMP_AIR + RH) | 동일 |
| ABSOLUTE_HUMIDITY | COMPARTMENT | g/m³ | MODEL_OUTPUT (ClimateModel L2) | CALCULATED (TEMP_AIR + RH) | 동일 |
| HUMIDITY_DEFICIT | COMPARTMENT | g/m³ | (N/A) | CALCULATED | ? |

### 6-4. DEVICE_COMMAND echo (LV 후보 — 보류)

옵션 1: LV 추가 (`WINDOW_OPENING_PCT`, `HEATING_PCT`) — DEVICE_COMMAND source.
옵션 2: LV 아닌 별도 채널 (`StateCache.actuator_outputs`).

→ **결정 보류 (K1)**. D4에서 다른 모듈이 액추에이터 상태를 *입력*으로 쓸 때 결정.

---

## 7. 갭 처리 정책 — 명시 등록만 인정

### 7-1. 부팅 검증 식

```
가용_LV(scope, scope_id) =
    SensorRegistry.publishes        +
    ModelOutputRegistry.publishes   +
    ExternalRegistry.publishes      +
    CalculatedRegistry.publishes    +
    ConstantRegistry.publishes

요구_LV = ⋃ module.REQUIRES_SENSORS ∪ module.REQUIRES_DERIVED  for module in active_modules

규칙:
  R1. 각 활성 module의 group.compartments 모든 c에 대해:
      가용_LV(COMPARTMENT, c) ⊇ module의 COMPARTMENT-scope 요구
  R2. 가용_LV(FARM, farm_id) ⊇ module의 FARM-scope 요구
  R3. 두 출처가 같은 (scope, scope_id, LV) 조합을 publish 시도 → 부팅 실패
  R4. INPUT_LABEL과 등록된 SensorInputStrategy 호환
  R5. OUTPUT_LABEL과 등록된 DistributionStrategy 호환
  R6. 각 ActuatorDevice.comm_binding.protocol이 등록된 어댑터 중 하나
  R7. 각 SensorDevice.comm_binding.protocol이 등록된 어댑터 중 하나
```

하나라도 실패 → 부팅 거부 + 명확한 에러 메시지.

### 7-2. ConstantSource 사용 정책

```python
ConstantRegistry.register(
    scope=Scope.COMPARTMENT,
    scope_id="default",
    lv=LogicalVariable.CO2,
    value=420.0,
    rationale="실 환경 CO2 센서 미설치 (2026-04). 외기 평균값 가정.",
)
```

- `rationale` **필수** — 왜 고정값인지 (감사 추적용)
- 부팅 로그에 출력
- 운영 환경에서 ConstantRegistry 등록 LV 개수가 임계치 초과 시 경고 (시스템 완성도 지표 — K6)

### 7-3. 묵시적 fallback 금지 사례

❌ `weather_values.get(LV.TEMP_OUTSIDE, 15.0)` — `world_adapter.py:122-125`에 현재 존재.

→ 별 Phase에서 정리 (K7). 본 문서 범위 밖.

---

## 8. 입출력 라벨 카탈로그

ControlModule 알고리즘이 자기 *호환성*을 라벨로 선언. Manager가 라벨 보고 라우팅.

### 8-1. 입력 라벨 (INPUT_LABEL)

| 라벨 | 의미 | 알고리즘이 받는 형태 | 호환 SensorInputStrategy |
|------|------|---------------------|-------------------------|
| `AGGREGATED` | 합쳐서 1개로 받기 | `dict[LV, float]` | AVG / MAX / MIN |
| `PER_COMPARTMENT` | 합치지 않고 그대로 | `dict[LV, dict[comp_id, float]]` | (라벨 동일) |

**선택 기준**:
- 합쳐도 의미 보존되면 AGGREGATED — 코드가 N에 무지, 농장 변화 = 데이터 변화
- 동별 *위치/역할 의미*가 알고리즘에 본질적이면 PER_COMPARTMENT — N에 따라 알고리즘 분기 정상

### 8-2. 출력 라벨 (OUTPUT_LABEL)

| 라벨 | 의미 | 알고리즘이 산출 | 호환 DistributionStrategy |
|------|------|----------------|--------------------------|
| `UNIFORM` | 균등 분배 | `GroupCommand` (단일 값) | UNIFORM |
| `WEIGHTED` | 가중치 분배 | `GroupCommand` (단일 값) | WEIGHTED (등록된 가중치 사용) |
| `ROLE_BASED` | 알고리즘이 직접 분배 | `list[ActuatorOutput]` | (위임만) |

### 8-3. 현재 코드 라벨 분류 (자동 추출)

| 모듈 | INPUT_LABEL | OUTPUT_LABEL |
|------|------------|--------------|
| `SkyWindow` (`croftos/layers/control/skywindow.py`) | AGGREGATED | UNIFORM |
| `HeatingPipe` (`croftos/layers/control/heating.py`) | AGGREGATED | UNIFORM |

→ v0 시작 단계는 *AGGREGATED + UNIFORM*만 구현. 나머지 라벨은 *자리 마련 + 비활성*.

---

## 9. 점진 확장 경로

| 버전 | Compartment | ActuatorGroup scope | ControlModule | 핵심 변경 |
|------|------------|---------------------|---------------|----------|
| **v0 (현재)** | 1 (`default`) | 1 comp | 1:1:1 | 기본 동작 |
| **v1: 멀티 동, 동별 독립** | N | 1 comp 종속 | 동별 인스턴스 | compartment_id 하드코딩 제거. 거의 무료 (이미 차원 존재) |
| **v2: 공통 제어** | N | N comp 가능 | aggregation 정책 도입 | LV scope 메타 + cache 분리 + INPUT_LABEL 라우팅 |
| **v3: Profile 재사용 (deferred)** | N | N | algorithm + params 분리 → 1 profile : N module 인스턴스 | ControlProfile 추출. *준비만, 1동 테스트 유지* |

**Q5 합의**: v3는 *마이그 경로만 plan에 기록*. 미사용 컬럼·테이블 만들지 않음. v3 진입 시 ALTER TABLE.

---

## 10. 약속 5개 — 견고함의 조건

설계가 견고하려면 다음 약속이 *문서뿐 아니라 코드 컨벤션*으로 박혀야 한다.

| # | 약속 | 강제 / 권고 | 검사 방법 |
|---|------|------------|----------|
| 1 | 의존성은 한 방향 (Core ← Models ← Layers, 도메인 코어가 외부 모름) | **강제** | import linter 자동 검사 |
| 2 | 정책은 데이터로, 흐름은 코드로 | 권고 | 코드 리뷰 가이드 |
| 3 | 각 레이어에 *교체 가능한 자리 5개* (추상 인터페이스 + Manager 등록) | **강제** | 추상 인터페이스 정의 강제 (ABC 또는 Protocol) |
| 4 | 레이어 간 대화는 *추상 인터페이스 호출* 또는 *NATS 이벤트*로만 | 권고 | 코드 리뷰 |
| 5 | 부팅 시 모든 자리·LV·라벨 호환성 검증 (§7-1 R1~R7) | **강제** | 부팅 코드 + 테스트 |

→ 강제 3개(1, 3, 5)는 *기계 검사*, 권고 2개(2, 4)는 *사람 리뷰*.

### 10-1. import linter 후보 규칙 (강제 ① 실행안)

```
core            → 어느 layer/도메인도 import 금지
models          → core만 import 가능
layers/control  → core, models, layers/<자기>, *추상 인터페이스* 만
layers/device   → core, layers/<자기>
layers/communication → core만 (도메인 무지)
layers/api      → 모든 레이어 (단 추상 인터페이스 통한 호출)
layers/db       → core만
```

도구 후보: `import-linter`(파이썬). D2~D3에 도입.

---

## 11. 확장 시나리오 검증 — 6개

견고한 설계는 변경의 영향이 작다. 시나리오별로 *어느 레이어만 변경되면 충분한지* 검증.

### S1. 동수가 1 → 5로 늘어남

| 레이어 | 변경 |
|--------|------|
| UI / API / Control / Communication / DB / Core | — |
| Device | **데이터** (compartment 4개 추가, group.compartments 수정) |

→ *코드 0줄*.

### S2. 통신 프로토콜 Modbus → MQTT 추가

| 레이어 | 변경 |
|--------|------|
| UI / API / Control / Device / DB / Core | — |
| Communication | **새 어댑터 클래스 1개** |

→ Communication에 새 파일 1개.

### S3. 새 알고리즘 (정교한 풍향 고려 천창)

| 레이어 | 변경 |
|--------|------|
| UI / API / Device / Communication / DB / Core | — |
| Control | **새 ControlModule 서브클래스 1개** |

→ Control에 새 파일 1개.

### S4. 새 LogicalVariable 도입 (예: 토양 온도)

| 레이어 | 변경 |
|--------|------|
| Core | LogicalVariable enum에 1줄 |
| Device | 새 SensorDevice 종류 등록 (데이터) |
| Control | 그 LV 사용하는 알고리즘 REQUIRES_* 갱신 |
| 다른 레이어 | — |

### S5. 입력 정책 변경 (avg → max)

| 레이어 | 변경 |
|--------|------|
| 모든 코드 | — |
| 데이터 | ControlModule 인스턴스의 sensor_aggregation 1줄 수정 |

### S6. 새 입력 정책 종류 도입 (예: weighted_by_volume)

| 레이어 | 변경 |
|--------|------|
| Device | **새 SensorInputStrategy 클래스 1개** + 등록 |
| 다른 레이어 | — |

---

## 12. 결정 보류 — K-list

| # | 사항 | 결정 시점 | 후보 |
|---|------|-----------|------|
| K1 | DEVICE_COMMAND echo를 LV로 만들지 | D4 (다른 모듈이 액추에이터 상태를 입력으로 쓸 때) | LV 추가 / 별도 채널 유지 |
| K2 | 시뮬에 CO2 모델 추가 시점 | Phase 11+ | ClimateModel 확장 / 별 모델 |
| K3 | LIGHT 영역 PAR/PPFD 도입 시점 | 광 도메인 모듈 추가 시 | 자산 절제 — 추측 X |
| K4 | TEMP_LEAF 변수 추가 (VPD_LEAF용) | crop 모델 확장 시 | 결정 보류 |
| K5 | 다중 출처 충돌 검증 위치 | D2~D3 | `validate_module_dependencies` 확장 / 별 함수 |
| K6 | ConstantRegistry 임계치 (운영 완성도 지표) | D6 또는 운영 도입 시 | 비율(%) / 절대 개수 |
| K7 | 묵시적 fallback (`world_adapter.py:122-125`) 제거 시점 | 별 Phase | 단독 PR |
| K8 | DEVICE_COMMAND를 갭 처리 source 후보로 인정할지 | D4 | 인정 / 별 source 한정 |
| K9 | REPRESENTATIVE 입력 정책 도입 시점 | 필요 시 | `representative_compartment_id` 별 필드 |
| K10 | DB 영역 5개 + 메모리 + 코드 분담 정리 | 별 § 신설 (이번 갱신 범위 밖) | concepts §13 후속 |

---

## 13. 사용자 채움 영역 (TODO)

### 13-1. §6 매핑 표의 `?` 셀

우선순위:
1. **§6-1 ENVIRONMENT** 실 Source — 농장 실제 센서 구성 (있으면 그대로, 없으면 EXTERNAL/CONSTANT 결정)
2. **§6-2 RADIATION_INSIDE** 갭 정책 — 실내 일사계 설치? 없으면 CALCULATED 공식 / CONSTANT 투과율?
3. **§6-3 VPD/DEW/AH** CALCULATED 공식 SSOT — 시뮬 ClimateModel 공식과 동일?

### 13-2. 농장 실태 확인 (concepts에 박혀야 매핑 정확)

- 보일러: 1대 → N동 공통? 동별 1대?
- CO2 발생기: 동별? 묶음?
- 천창: 동별 별도 모터군?
- 외기 센서: 농장에 1세트?

### 13-3. K-list 우선 결정

- K1, K7 결정 시점이 임박하면 우선 처리

---

## 14. D1 영향 (Phase plan에 미치는 변화)

`device_manager.md` Phase D1 추가/변경 사항:

1. **inventory 대상 확대**: 액추에이터 + 가상 센서 양쪽
2. **Device 모델 분리**: SensorDevice, ActuatorDevice 두 종류 (또는 공통 상위 + role 필드)
3. **Source 메타 *기록만***: D1에서는 publish 분류만 (MEASURED/MODEL_OUTPUT/EXTERNAL). 갭 처리 코드는 D2 이후
4. **라벨 자동 추출**: §8-3 표대로 SkyWindow=AGGREGATED+UNIFORM 등 메타 추가
5. **묵시적 fallback 제거 (K7)는 D1 범위 X** — 별 Phase

> plan 본문 업데이트는 사용자 승인 후 별도 작업.

---

## 15. 변경 이력

- 2026-04-28 (1차): 초안 — 자동 추출 + `?` 미정 표시. §3 매핑 표 채움 대기.
- 2026-04-28 (2차): 사용자 합의 누적 반영
  - §1에 Farm·ActuatorGroup 추상 추가 (3축 분리)
  - LogicalVariable에 `scope` 메타 추가 (FARM / COMPARTMENT)
  - §1-7 Source 명칭 변경: COMMAND_ECHO → DEVICE_COMMAND, DERIVED_L2 → CALCULATED
  - §2 6-Layer 책임 풀이 추가
  - §3 자리 5개 + §4 Manager 6개 신설
  - §5 두 흐름 (센서 / 명령) + NATS 정리
  - §6 매핑 표에 scope 컬럼 추가
  - §7 부팅 검증식 R1~R7 명문화
  - §8 입출력 라벨 카탈로그 (INPUT_LABEL: AGGREGATED/PER_COMPARTMENT, OUTPUT_LABEL: UNIFORM/WEIGHTED/ROLE_BASED)
  - §9 점진 확장 경로 v0→v3
  - §10 약속 5개 + 강제/권고 분류 + import linter 후보
  - §11 확장 시나리오 6개
  - §12 K-list 확장 (K9, K10)
  - §13 농장 실태 확인 항목
- (예정 — DB 정리): §13 자리에 *DB 영역 5개 + 메모리 + 코드* 분담 추가. 자리 5개 × DB/코드 매트릭스. Manager × DB 매트릭스. 영역 A~E + 메모리 + 코드.

---

## 16. DB 영역 6개 분담 (Phase D7)

> 본 §은 D7 진입 시 신설. K10 결정 사항 정리 + DDL/Repo 매핑.

### 16-1. 6 영역 한눈

| 영역 | 책임 | 테이블/View | 경로 |
|------|------|------------|------|
| **A. Master** | 등록 자산 (CRUD, 거의 정적) | farm·compartment(기존) + sensor_device, sensor_publishes, actuator_group, actuator_group_compartment, actuator_device, control_module_instance, data_source_constant | `croftos/layers/db/device_schema.py` |
| **B. Setpoint** | 의도/명령/출력 (기존) | setpoint_schedule, setpoint_intent, actuator_group_command, actuator_output | `croftos/layers/db/schema.py` |
| **C. Sensor 시계열** | hot path hypertable (별 phase 도입) | sensor_readings, sensor_readings_*m_avg(CA), actuator_readback | (deferred — sensor.md §4) |
| **D. Observability** | 시스템 부하 메트릭 (기존) | core_metric_ts hypertable + 인덱스 | `croftos/layers/db/metrics_schema.py` |
| **E. Catalog/Seed** | 알려진 종 + 템플릿 (등록 UX 가속) | module_kind_catalog, device_kind_catalog, farm_template | `croftos/layers/db/device_schema.py` |
| **F. Validation View** | R1~R7 자동 검증 SQL | v_published_lvs, v_required_lvs, v_lv_gaps, v_lv_conflicts | `croftos/layers/db/device_schema.py` |

### 16-2. 자리 5개 × DB 매트릭스

| 자리 | 데이터 SSOT | 인-메모리 | DB |
|------|----------|---------|-----|
| 1. SensorInputStrategy | code (DEFAULT_STRATEGIES) | DEFAULT_STRATEGIES dict | — (코드 카탈로그) |
| 2. ControlModule | code class + DB row | DeviceRegistry._modules | control_module_instance |
| 3. DistributionStrategy | code (DEFAULT_STRATEGIES) | DEFAULT_STRATEGIES dict | — (코드 카탈로그) |
| 4. CommunicationAdapter | runtime register | AdapterRegistry | — (lifespan 등록) |
| 5. DataSource (6 종) | 자원별 — sensor_device·data_source_constant·external·model_output | DeviceRegistry caches | sensor_device, data_source_constant |

### 16-3. Manager 6개 × DB 매트릭스

| Manager | 읽는 테이블 | 쓰는 테이블 |
|---------|-----------|------------|
| DeviceRegistry | A 영역 + E 카탈로그 | (read-only via repo) |
| SensorResolver | (Registry 경유) | — |
| ControlOrchestrator | (Registry 경유) | actuator_group_command (cmd 내려갈 때 audit) |
| ActuatorManager | actuator_device.comm_binding | actuator_output (D7 활성), actuator_readback (별 phase) |
| SensorPipeline | sensor_device | sensor_readings (별 phase) |
| CommunicationManager | — | — (메모리 라이프사이클만) |

### 16-4. 호환성 ALTER

기존 setpoint 테이블에 FK 추가:
- `actuator_output.actuator_id` → `actuator_device(actuator_id)` REFERENCES (CASCADE 정책 결정 필요)
- `actuator_group_command.actuator_group_id` → `actuator_group(actuator_group_id)` REFERENCES
- `actuator_group_command.compartment_id` → **NULL 허용** (Q8 결정 — v2 공통 제어 호환)

### 16-5. Q-list 결정

- **Q1** (D2 결정 — 평면 dict): `comm_binding` JSONB 컬럼 그대로 (정규화는 v3+).
- **Q6** (D7 결정): 템플릿 카탈로그 = **DB 테이블** (`farm_template`) — payload JSONB. 외부 파일 동기화는 `migrations/yaml_to_db.py`가 1회 seed 후 *YAML 파일은 마이그 source로만 보존*.
- **Q8** (D7 결정): `actuator_group_command.compartment_id` = **NULL 허용**. v2 공통 제어 시 group이 N comp 묶을 때 의미 있는 1 comp 없으므로 NULL.

### 16-6. seed 흐름 (1회)

```
[운영 lifespan startup]
  init_pool() → init_metric_schema/init_weather_schema/init_simulation_schema
       │
       ▼
  init_device_schema()  ← D7 신설 (마스터 + 카탈로그 + Validation View)
       │
       ▼
  seed_yaml_to_db()      ← 1회 idempotent INSERT (ON CONFLICT DO NOTHING)
       │   └─ croftos/data/devices/*.yaml + templates/*.yaml 모두
       ▼
  ctx = make_context(repo=DeviceRepoPostgres(...))
  await ctx.devices.ensure_loaded()
```

> seed는 *DB가 비어있을 때만* 의미. 운영 도입 후엔 사용자 등록이 SSOT — YAML 파일은 *역사적 seed source*로만 보존.

### 16-7. `@임시-yaml` 마커 정리 (D7 완료 조건)

D6까지 누적된 `@임시-yaml` 마커 위치별 정리:

| 마커 위치 | D7 처리 |
|---------|---------|
| `pyproject.toml` pyyaml 의존 | 보존 (마이그 source 파싱에 필요) — 마커는 *seed source* 의미로 변경 |
| `croftos/layers/device/device_repo_yaml.py` | **보존** — 시뮬 환경 (`make_context` default repo)에서 사용. 시뮬 fixture를 DB로 옮기는 작업은 *후속 cleanup PR*로 deferred (시뮬 격리 보존) |
| `croftos/layers/device/audit_log.py` | DB `audit_log` 테이블로 마이그 → 본 모듈 폐기 (또는 *후속 PR*) |
| `croftos/layers/api/main.py` 운영 lifespan | **DB Repo로 swap** — `DeviceRepoPostgres()` 사용. 마커 제거 |
| `croftos/layers/api/simulation.py` 시뮬 | **YAML Repo 유지** — 마커는 *시뮬 fixture* 의미로 변경 |
| `croftos/layers/communication/adapters/simulator_noop.py` | 보존 (시뮬 어댑터) — 마커 제거 (D7 후엔 정식) |
| `croftos/layers/device/actuator_manager.py` ring buffer | DB INSERT로 교체 (DB pool 있을 때만) — 마커 제거 |
| `croftos/data/devices/*.yaml` | **seed source로 보존** — 마커는 그대로 유지 (`@임시-yaml` → `@db-seed-source`로 의미 변경) |
| 테스트 fixture (conftest, test_sim_runner) | 보존 — 시뮬 격리 |

→ **D7 완료 조건 *현실 버전***: 운영 lifespan/actuator_output INSERT/audit_log 등 *운영 경로*는 DB로 전환, *시뮬·테스트 경로*는 YAML 유지. 완전한 grep 0건은 *시뮬 인프라 자체 DB 도입* 별 phase로 deferred.
