# Sensor Architecture

CroftOS의 센서 시스템 — 데이터가 어디서 오고, 어떻게 흐르고, 어디에 저장되며, 어떻게 사용자/제어 알고리즘에게 전달되는지를 정의한다.

이 문서는 **사내 다른 프로덕트(Nexus Hub, Nexus Core, Grow Pilot)와 호환성**을 의도적으로 가져가도록 설계되었다. 같은 도메인(농업·온실)의 시스템이므로 ID 체계·LogicalVariable·테이블 구조를 공유한다.

전체 시스템 구조는 [croft-os.md](croft-os.md). Setpoint(제어 의도) 흐름은 [setpoint.md](setpoint.md).

---

## 1. 핵심 개념

### 1-1. 세 가지 추상화 레이어

센서를 다룰 때 이 셋을 항상 분리해서 생각한다.

| 추상화 | 정체 | 예시 |
|---|---|---|
| **물리 센서 (Sensor)** | 실제 하드웨어 인스턴스 | "compartment1 동측 벽에 붙은 온도센서 #3" |
| **논리 변수 (LogicalVariable)** | 측정 대상의 의미 | `TEMP_AIR`, `CO2`, `RH`, `PAR_INSIDE` |
| **매핑 (Mapping)** | 어떤 sensor가 어떤 변수를 측정하는가 | "compartment1의 TEMP_AIR는 sensor_uuid_xyz" |

이 분리가 왜 중요한가:
- Control 알고리즘은 *"compartment1의 TEMP_AIR가 22도"* 만 알면 됨, 어느 센서인지는 무관
- 센서 교체 시 매핑만 바꾸면 끝, 알고리즘 코드 0 변경
- 같은 변수에 여러 센서(주/보조) 가능 → 자동 fallback 구현 용이
- Nexus Hub와 변수 코드 호환 → Grow Pilot이 CroftOS 데이터 그대로 분석 가능

### 1-2. UUID 기반 식별

모든 도메인 엔티티는 UUID PK를 가진다.

```
farm_id          UUID
compartment_id   UUID    ← Nexus의 'compartment' 용어와 통일 (= zone)
sensor_id        UUID
actuator_id      UUID
actuator_group_id UUID
```

이유:
- Nexus Hub와 ID 호환 (향후 통합 시 매핑 작업 0)
- 농장 추가·이전 시 ID 충돌 없음
- 로컬에서 생성 가능 (Hub 의존 X, 오프라인 운영 보장)

### 1-3. Soft Delete

센서를 빼도 과거 이력은 살아있어야 한다 (작년 이맘때 온도 추이 조회 등).
모든 마스터 테이블은 `deleted_at TIMESTAMPTZ NULL` 컬럼을 가진다.

---

## 2. 데이터 흐름 — Hot Path와 Cold Path 분리

### 2-1. 전체 흐름

```
[Hardware 센서]
    │ 4-20mA / 0-10V / 디지털 펄스
    ▼
[PLC]
    │ A/D 변환 + D-area 레지스터
    ▼
[Communication Layer: Modbus 어댑터]
    │ Modbus TCP polling (1~5초 주기)
    ▼
[Device Layer: Sensor Pipeline]
    │
    ├──► State Cache       (즉시 반영, sub-µs)
    ├──► NATS publish      (구독자에게 즉시 방송)
    └──► DB Writer Queue   (비동기 배치 적재)
                  │
                  ▼
            TimescaleDB (raw + Continuous Aggregate)
```

### 2-2. Hot Path (제어 영향 경로)

**제어 결정에 직접 영향을 주는 경로**는 DB 쓰기를 기다리지 않는다.

```
센서 도착 → Cache 갱신 → NATS publish → Control 수신
       (이 경로는 1ms 이내, DB 무관)
```

DB가 잠깐 느려져도 제어가 안 멈춘다. DB가 죽어도 (잠깐) 제어는 계속 (값은 큐에 쌓임).

### 2-3. Cold Path (영속화 경로)

**DB 쓰기는 별도 워커가 배치로 처리.**

- 100개 모이면 즉시 flush, 또는
- 1초 지나면 flush (양 적어도)

→ 평균 적재 지연 0.5초, 처리량은 단일 INSERT 대비 50배.

---

## 3. 세 저장소의 역할 분담

| | State Cache | NATS | TimescaleDB |
|---|---|---|---|
| 답하는 질문 | "지금 값이 뭐야?" | "방금 새 값 들어왔어!" | "지난 모든 측정값 줘" |
| 본질 | Python dict (in-memory) | 메시지 라우터 (별도 프로세스) | 영속화 DB |
| 자료 보관 | 최신값 1개 | 전달 후 폐기 | 전체 이력 |
| 부팅 시 | 비어있음 → DB에서 로드 | 메시지 사라짐 | 그대로 살아있음 |
| 누가 사용 | UI 조회, Control 즉시 참조 | Control 트리거, 알람, WebSocket | 차트, 분석, ML 학습 |
| 응답 속도 | sub-microsecond | < 1ms | 수십 ms |

**셋은 중복이 아니라 시간 차원이 다르다.**
- 지금 (Cache) · 방금 (NATS) · 지난 모든 순간 (DB)

---

## 4. 메트릭 레이어 (L0~L3) — Nexus Core 차용

센서 데이터를 단순 raw 그대로만 쓰지 않는다. **단계별로 가공**해서 저장한다.

### 4-1. 레이어 정의

```
L0 (Raw)            ← PLC에서 직접 받은 값, 단위 변환만
   │
   ├── L1 (Resample) ← 1분 또는 5분 버킷 평균/최소/최대
   │
   └── L2 (Derived) ← 공식 기반 파생값 (VPD, 절대습도, 이슬점)
           │
           └── L3 (Aggregate) ← 일/주/월 단위 집계 (DLI, 일평균, 일합계)
```

### 4-2. CroftOS의 단순화

Nexus Core는 별도 Worker 프로세스로 L1/L2/L3을 계산하지만, CroftOS는 규모가 작으므로 **TimescaleDB Continuous Aggregate로 대체**.

| 레이어 | 구현 방식 | 예시 |
|---|---|---|
| L0 (Raw) | `sensor_readings` hypertable에 직접 저장 | 매 polling 결과 |
| L1 (1m/5m 평균) | Continuous Aggregate | `sensor_1m_avg`, `sensor_5m_avg` |
| L2 (파생) | View 또는 Control Layer 내 실시간 계산 | VPD, 이슬점 |
| L3 (일 집계) | Continuous Aggregate | DLI, 일평균 온도 |

→ 별도 Worker 없이 **DB가 알아서** 1분/시간/일 단위 집계 자동 갱신.

### 4-3. L2 파생 변수 — Control Layer가 실시간 계산

L2는 두 가지 방식으로 가져갈 수 있다:
1. **DB View**: TimescaleDB가 raw에서 즉시 계산 (느림, 매번 계산)
2. **Cache에 직접 저장**: Control Layer가 raw를 받을 때 함께 계산해서 캐시 갱신 (빠름, 권장)

**TEMP_AIR + RH가 들어오면 즉시 VPD/이슬점/절대습도 계산해서 Cache에 함께 저장.**

---

## 5. LogicalVariable 카탈로그

CroftOS가 다루는 논리 변수 목록. Nexus Hub와 코드 통일.

### 5-1. 환경 (Environment)

| Code | 한글명 | 단위 | 비고 |
|---|---|---|---|
| `TEMP_AIR` | 공기 온도 | °C | Compartment 내부 |
| `TEMP_OUTSIDE` | 외기 온도 | °C | |
| `RH` | 상대습도 | % | |
| `RH_OUTSIDE` | 외기 상대습도 | % | |
| `CO2` | 이산화탄소 농도 | ppm | |
| `PRESSURE` | 기압 | hPa | |
| `WIND_SPEED` | 풍속 | m/s | 외기 |
| `WIND_DIRECTION` | 풍향 | ° | 외기 |

### 5-2. 광량 (Light)

| Code | 한글명 | 단위 | 비고 |
|---|---|---|---|
| `RADIATION_OUTSIDE` | 외부 광량 (Watt) | W/m² | |
| `RADIATION_INSIDE` | 온실 광량 (Watt) | W/m² | |
| `PAR_OUTSIDE` | 외부 PAR | µmol/m²/s | |
| `PAR_INSIDE` | 온실 PAR | µmol/m²/s | |
| `PPFD` | PPFD | µmol/m²/s | PAR과 동의어 |

### 5-3. 토양/배지 (Soil / Substrate)

| Code | 한글명 | 단위 | 비고 |
|---|---|---|---|
| `SOIL_TEMP` | 토양 온도 | °C | |
| `SOIL_MOISTURE` | 토양 함수율 | %VWC | |
| `SOIL_EC` | 토양 EC | dS/m | |
| `SUBSTRATE_EC` | 배지 EC | mS/cm | |
| `SUBSTRATE_TEMP` | 배지 온도 | °C | |
| `SUBSTRATE_WVC` | 배지 함수율 | % | |
| `SLAB_WEIGHT` | 배지 무게 | g | |

### 5-4. 관수 (Irrigation)

| Code | 한글명 | 단위 | 비고 |
|---|---|---|---|
| `WATER_AMOUNT` | 관수량 | L/m² | |
| `WATER_EC` | 관수 EC | mS/cm | |
| `WATER_PH` | 관수 pH | pH | |
| `WATER_TEMP` | 관수 온도 | °C | |
| `DRAINAGE_VOLUME` | 배수량 | L | |
| `DRAINAGE_VOLUME_CUM` | 누적 배수량 | L | 일 누적 |
| `DRAINAGE_PERCENT` | 배수율 | % | |
| `DRAIN_EC` | 배수 EC | mS/cm | |
| `DRAIN_TEMP` | 배수 온도 | °C | |
| `VALVE_PHASE_ACTIVE` | 밸브 활성 | 0/1 | **이벤트 기반, 6-2 참조** |

### 5-5. 구조물 상태 (Structural State)

| Code | 한글명 | 단위 | 비고 |
|---|---|---|---|
| `VENT_LEE_POSITION` | 풍하측 벤트 위치 | % | |
| `VENT_WIND_POSITION` | 풍상측 벤트 위치 | % | |
| `CURTAIN_POSITION` | 커튼 위치 | % | |
| `SCREEN_UP_POSITION` | 상부 스크린 위치 | % | |
| `SCREEN_DOWN_POSITION` | 하부 스크린 위치 | % | |
| `HEATING_PIPE_TEMP` | 난방 파이프 온도 | °C | |

### 5-6. 파생 변수 (Derived, L2)

| Code | 한글명 | 단위 | 계산 입력 |
|---|---|---|---|
| `VPD_LEAF` | 잎 VPD | kPa | TEMP_AIR, RH, leaf_temp |
| `VPD_AIR` | 공기 VPD | kPa | TEMP_AIR, RH |
| `DEW_POINT` | 이슬점 | °C | TEMP_AIR, RH |
| `ABSOLUTE_HUMIDITY` | 절대습도 | g/m³ | TEMP_AIR, RH |
| `HUMIDITY_DEFICIT` | 습도 부족분 | g/m³ | TEMP_AIR, RH |

### 5-7. 일 집계 (Daily Aggregates, L3)

| Code | 한글명 | 단위 | 계산 |
|---|---|---|---|
| `DLI` | Daily Light Integral | mol/m²/day | PAR_INSIDE 일 누적 |
| `RADIATION_SUM_DAILY` | 일 누적 광량 | J/cm² | RADIATION 일 누적 (6-3 참조) |
| `PHOTO_PERIOD` | 광일장 | 분 | PAR_OUTSIDE > 100 인 시간 |
| `TEMP_AVG_DAILY` | 일평균 온도 | °C | |
| `TEMP_MAX_DAILY` | 일최고 온도 | °C | |
| `TEMP_MIN_DAILY` | 일최저 온도 | °C | |
| `RTR` | Radiation Temperature Relation | °C/J/cm² | |

### 5-8. 진단 (Diagnostic)

| Code | 한글명 | 단위 | 비고 |
|---|---|---|---|
| `SIGNAL_STRENGTH` | 신호 강도 | dBm | 무선 센서 |
| `BATTERY_VOLTAGE` | 배터리 전압 | V | |
| `BATTERY_LEVEL` | 배터리 잔량 | % | |

### 5-9. 작물/생장 (Plant)

| Code | 한글명 | 단위 | 비고 |
|---|---|---|---|
| `PLANT_WEIGHT` | 작물 무게 | g | 슬랩별 |
| `FRUIT_GROWTH` | 과실 생장 | mm | Trutina 등 |
| `DENDROMETER` | 줄기 직경 | mm | |
| `BIOMASS` | 바이오매스 | kg | |

---

## 6. 특수 케이스 처리

### 6-1. 다중 센서 (같은 변수를 여러 센서가 측정)

한 compartment에 동측·서측 온도센서 2개가 모두 `TEMP_AIR`를 측정할 수 있다.

**처리 방식**:
- `compartment_variable_mapping` 테이블에 `is_primary` 플래그
- 같은 (compartment, logical_variable) 쌍에 여러 sensor_id 등록 가능
- Cache는 *primary 센서값* 만 노출, 보조는 별도 키로
- Primary 센서 fail (quality != GOOD) 시 자동 fallback

```sql
INSERT INTO compartment_variable_mapping VALUES
  ('comp1', 'TEMP_AIR', 'sensor_east_uuid', is_primary=true,  conversion={...}),
  ('comp1', 'TEMP_AIR', 'sensor_west_uuid', is_primary=false, conversion={...});
```

### 6-2. 이벤트 기반 센서 (valve_phase_active)

밸브는 *상태 변경 시에만* 데이터를 보낸다. 1분 polling이 아니라 *변화 감지*.

```
| event_time | value |
| ---------- | ----- |
| 08:05      | 1     |  ← ON 신호
| 08:16      | 0     |  ← OFF 신호
| 10:10      | 1     |  ← 다시 ON
| 10:20      | 0     |  ← 다시 OFF

지속 시간 = (08:16 - 08:05) + (10:20 - 10:10) = 11분 + 10분 = 21분
```

**처리 방식**:
- 센서 정의 시 `event_driven=true` 플래그
- Pipeline은 새 값이 *직전 값과 다를 때만* DB INSERT (중복 제거)
- 지속시간 계산은 별도 view 또는 Continuous Aggregate에서 산출
- 관수량 환산은 별도 룰 (밸브당 L/min 상수와 곱)

```sql
-- ON→OFF 페어로 지속시간 산출하는 view 예시
CREATE VIEW valve_active_duration AS
SELECT
    sensor_id,
    on_event.time AS started_at,
    off_event.time AS ended_at,
    off_event.time - on_event.time AS duration
FROM ...;
```

**중요**: value의 ON/OFF 횟수와 시간만 알 수 있으므로, 실제 관수량은 **시간당 토출량 상수**(센서 메타데이터)와 곱해서 산출. 이 상수는 Grow Team의 수기 검사로만 알 수 있어 별도 테이블에 보관.

### 6-3. 일 누적 변수 (radiation_sum 등)

일부 변수는 *하루 단위 누적값*. 자정에 0으로 리셋.

**예: outside_radiation_sum (J/cm²)**
```python
# 우람님 코드 추출 — 1분 resampling 가정
one_day_data["outside_radiation"].sum() * interval * 60 / 10000
# * interval * 60  → W/m² × s = J/m²
# / 10000          → m² → cm² 변환 → J/cm²
```

```sql
-- Continuous Aggregate가 매일 계산
SELECT
    time_bucket('1 day', time, 'Asia/Seoul') AS bucket,
    compartment_id,
    SUM(value) * sample_interval_seconds / 10000 AS radiation_sum_j_cm2
FROM sensor_readings
WHERE logical_variable = 'RADIATION_OUTSIDE'
GROUP BY bucket, compartment_id;
```

자정에 자동 0 시작 (새 bucket 시작이므로). 별도 reset 로직 불필요.

### 6-4. 누적 변수 (cumulative)

일부 raw 변수는 누적값으로 들어옴 (`drainage_volume_cum`, `lamps_kWh_cumulative`).

**처리 방식**:
- Raw 그대로 저장 (`L0`)
- 차분이 필요할 때는 LAG()로 산출:
  ```sql
  SELECT time, value - LAG(value) OVER (ORDER BY time) AS delta
  FROM sensor_readings
  WHERE sensor_id = '...' AND logical_variable = 'DRAINAGE_VOLUME_CUM';
  ```
- 일자 변경 지점은 음수 나올 수 있음 (리셋) → CASE WHEN으로 처리

### 6-5. 멀티 센서 디바이스

한 디바이스가 여러 view_data_type을 측정할 수 있다 (Trutina 같은 통합 센서).

**처리 방식**:
- `(installed_device_id, view_data_type_id)` 복합 키로 식별 (Grow Pilot 패턴)
- CroftOS에서는 sensor_id (UUID) 단위로 정규화하여 저장
- 한 디바이스의 여러 측정값은 각각 별도 sensor row로 등록

---

## 7. Quality / Status 플래그

센서는 거짓말을 한다. 처음부터 받아들인다.

### 7-1. Quality 분류

```python
class Quality(IntEnum):
    GOOD          = 0   # 정상
    STALE         = 1   # 마지막 업데이트가 너무 오래
    OUT_OF_RANGE  = 2   # 물리적으로 불가능한 값
    SUSPECT       = 3   # 통계적 이상 (이전값과 큰 차이)
    SENSOR_ERROR  = 4   # 센서가 명시적 에러 코드 반환
    NO_DATA       = 5   # 통신 실패
```

Quality bad → Problem 변환 규칙은 [error.md](error.md) §3·§6-1. 알람 채널은 `core.problem.emit()`으로 통합 — `alarm_bus` 어휘는 폐기.

### 7-2. Quality 결정 로직 (Sensor Pipeline 내)

```python
def assess_quality(reading, sensor_config) -> Quality:
    # 1. 통신 실패
    if reading is None:
        return Quality.NO_DATA

    # 2. 명시적 에러 코드 (Modbus 0x8000 등)
    if reading.value in sensor_config.error_codes:
        return Quality.SENSOR_ERROR

    # 3. 물리 범위 체크
    if not (sensor_config.min_physical <= reading.value <= sensor_config.max_physical):
        return Quality.OUT_OF_RANGE

    # 4. Stale 체크
    age = now_utc() - reading.event_time
    if age > timedelta(seconds=sensor_config.expected_period_seconds * 3):
        return Quality.STALE

    # 5. 통계적 이상 (선택)
    if reading.delta_from_previous > sensor_config.max_change_per_period:
        return Quality.SUSPECT

    return Quality.GOOD
```

### 7-3. Control Layer의 Quality 활용

```python
# Control Layer
reading = cache.get_value(compartment_id, "TEMP_AIR")
if reading is None or reading.quality != Quality.GOOD:
    # 보조 센서 시도
    reading = cache.get_value(compartment_id, "TEMP_AIR", fallback=True)

if reading is None or reading.quality != Quality.GOOD:
    # 안전 모드 진입 또는 이전값 유지
    return self.safe_action()
```

### 7-4. Sensor Health Snapshot (Nexus 패턴)

주기적으로 각 센서의 수집 상태를 스냅샷으로 기록 (14일 보관).

| 필드 | 설명 |
|---|---|
| `status` | normal / delayed / error / no_data |
| `last_event_time_utc` | 마지막 수집 시각 |
| `expected_period_seconds` | 예상 수집 주기 |
| `recent_intervals` | 최근 3개 수집 간격 (초) |
| `data_count_24h` | 캡처 시점 기준 24시간 데이터 수 |

---

## 9. 코드 구조

### 9-1. Sensor Pipeline (Device Layer 진입점)

```python
# layers/device/sensor_pipeline.py
class SensorPipeline:
    """3개 도구로 fan-out 하는 단일 진입점"""

    def __init__(self, cache, nats, db_writer, derived_calc):
        self.cache = cache
        self.nats = nats
        self.db_writer = db_writer
        self.derived = derived_calc

    async def handle(self, reading: SensorReading):
        # 1. Quality 평가
        reading.quality = self._assess_quality(reading)

        # 2. Cache 갱신 (즉시)
        self.cache.sensor_values[
            (reading.compartment_id, reading.logical_variable)
        ] = reading

        # 3. NATS 방송 (백그라운드)
        asyncio.create_task(
            self.nats.publish(
                f"sensor.{reading.compartment_id}.{reading.logical_variable}",
                reading.to_bytes()
            )
        )

        # 4. DB 큐 적재 (논블로킹)
        await self.db_writer.enqueue(reading)

        # 5. L2 파생 변수 계산 트리거
        if reading.logical_variable in ("TEMP_AIR", "RH"):
            await self.derived.recompute_for(reading.compartment_id)

        # 6. 알람 검증 — Quality 전이/지속을 error.md §3·§6-1 규칙으로
        #    `core.problem.emit(Problem(...))` 호출 (정확한 시그니처는 천창 모듈에서 확정)
```

### 9-2. State Cache 구조

```python
# core/state.py
class StateCache:
    """6-Layer 모두 import 가능한 in-memory cache"""

    sensor_values: dict      # (compartment_id, logical_variable) → Reading
    sensor_quality: dict     # (compartment_id, logical_variable) → Quality
    derived_values: dict     # 계산된 L2 (VPD, 이슬점 등)
    sensor_meta: dict        # sensor_id → 정적 메타데이터

    def get_value(
        self, compartment_id: UUID, variable: str, fallback: bool = False
    ) -> Optional[Reading]:
        """primary가 bad이고 fallback=True이면 보조 센서 값 반환"""
        ...
```

Setpoint 측 필드(`intents`, `group_commands`, `actuator_outputs`)는 [setpoint.md](setpoint.md) §하위 6 참고. 같은 StateCache의 다른 영역.

### 9-3. DB Writer (배치)

```python
# layers/db/sensor_writer.py
class SensorWriter:
    """Hot path와 분리된 비동기 배치 적재"""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10000)

    async def enqueue(self, reading: SensorReading):
        await self.queue.put(reading)

    async def writer_loop(self):
        batch = []
        while True:
            try:
                reading = await asyncio.wait_for(
                    self.queue.get(), timeout=1.0
                )
                batch.append(reading)
                if len(batch) >= 100:
                    await self._flush(batch)
                    batch = []
            except asyncio.TimeoutError:
                if batch:
                    await self._flush(batch)
                    batch = []

    async def _flush(self, batch):
        await db.executemany(
            "INSERT INTO sensor_readings ... ON CONFLICT (sensor_id, time) DO NOTHING",
            [r.to_tuple() for r in batch]
        )
```

---

## 10. 6-Layer 안에서의 위치

| 작업 | Layer | 모듈 |
|---|---|---|
| Modbus polling | Communication | `adapters/modbus.py` |
| Sensor 등록·메타 관리 | Device | `sensor_manager.py` |
| Sensor Pipeline (fan-out) | Device | `sensor_pipeline.py` |
| State Cache 보관 | Core | `core/state.py` |
| NATS 발행 | Core | `core/nats_client.py` |
| L2 파생 계산 (VPD 등) | Control | `control/derived_metrics.py` |
| DB Writer (배치) | DB | `db/sensor_writer.py` |
| 스키마 / Continuous Aggregate | DB | `db/schema.py`, `migrations/` |
| Quality 알람 발행 | Control 또는 Alarm | `validators.py` |
| API 조회 (현재값/이력) | API | `api/routers/sensors.py` |
| UI 차트 / 실시간 게이지 | UI | (Frontend) |
| 헬스 모니터링 스냅샷 | Core | `core/sensor_health.py` |

---

## 11. API 엔드포인트 (개요)

```
GET  /api/sensors                              센서 목록
GET  /api/sensors/{sensor_id}                  센서 상세 + 헬스
GET  /api/compartments/{id}/current-values     현재값 (Cache 조회, sub-ms)
GET  /api/sensors/{id}/history                 이력 조회 (자동 해상도 선택)
     ?start=...&end=...&resolution=auto        (raw / 1m / 5m / 1h / 1d)

WS   /api/events                               알람·실시간 이벤트 push
```

이력 조회의 자동 해상도 선택:
```python
duration = end - start
if duration < timedelta(hours=2):       table = "sensor_readings"      # raw
elif duration < timedelta(days=2):      table = "sensor_1m_avg"
elif duration < timedelta(days=14):     table = "sensor_5m_avg"
elif duration < timedelta(days=60):     table = "sensor_1h_avg"
else:                                    table = "sensor_1d_agg"
```

---

## 12. 향후 확장 — Nexus 통합 시나리오

CroftOS가 미래에 Nexus Hub로 등록되면:

```
[Nexus Hub]                          [CroftOS]
  Hub가 ID 발급                       로컬 운영

  farm_id (UUID) ──────────────────→ farm_id 그대로
  compartment_id ──────────────────→ compartment_id 그대로
  logical_variable code ───────────→ 동일 코드 사용

  ┌──────────────────────────────┐
  │ canonical_sensor_data         │ ◄── sensor_readings 주기 동기화
  │ metric_timeseries (L1/L2/L3)  │ ◄── Continuous Aggregate 결과 동기화
  └──────────────────────────────┘
                                       ▼
                                Grow Pilot 분석 가능
```

CroftOS는 **오프라인 자율 운영**이 원칙이지만, 인터넷 연결 시 *백로그(backlog)로* Hub에 데이터를 푸시.

---

## 13. 정리

### 13-1. 데이터의 시간 차원
- **지금** (Cache): UI/Control이 묻는 즉시 답
- **방금** (NATS): 변화 발생 시 모든 구독자에게 즉시 방송
- **지난 모든 순간** (TimescaleDB): 영구 보관, 분석/ML/MPC 학습 데이터

### 13-2. 데이터의 가공 차원
- **L0 raw** (변환만)
- **L1 1분/5분 평균** (Continuous Aggregate)
- **L2 파생** (VPD, 이슬점 — Control Layer 실시간 계산)
- **L3 일 집계** (DLI, 일평균 — Continuous Aggregate)

### 13-3. 추상화의 차원
- **Sensor**: 물리 인스턴스
- **LogicalVariable**: 의미 추상화
- **Mapping**: 둘을 잇는 SSOT

### 13-4. 호환성 차원
- Nexus Hub와 ID·LogicalVariable 코드 통일
- 향후 Grow Pilot에 데이터 그대로 공급 가능
- 단, CroftOS는 **실시간 제어**가 목적이라 Nexus의 Worker 시스템은 채택하지 않고 TimescaleDB Continuous Aggregate로 단순화

이 4개 차원을 모두 의식적으로 설계함으로써, *"센서 추가는 매핑 1줄, 새 변수는 카탈로그 1행, 새 파생은 함수 1개"* 의 단순성을 달성한다.
