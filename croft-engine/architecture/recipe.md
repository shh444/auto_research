# Recipe — Schedule-based [1] User Intent 생성

`control/recipe.py`가 1분 주기로 평가하여 [1] User Setpoint를 산출한다.
첫 도메인은 **환기온도 스케줄**. 다른 도메인(난방·CO2·차광)은 첫 모듈 검증 후 같은 패턴으로 추가.

값의 위계 [1]~[4] 정의는 [setpoint.md](setpoint.md) §하위 1 참고. 저장은 [setpoint.md](setpoint.md) §상위 1·2 (DB + State Cache + PLC Buffer 3-place).

---

## 1. 환기온도 스케줄

UI에서 사용자가 최대 6단계 입력. 각 단계는 시작시각·기준온도·환경 보정 3종·ramp 시간으로 구성.

### 1-1. 데이터 모델

테이블 `setpoint_schedule` (compartment + domain 단위, TimescaleDB):

| 컬럼 | 타입 | 단위 | 설명 |
|---|---|---|---|
| compartment_id | text | | |
| domain | text | | `"ventilation_temp"` |
| stage_no | int | | 1..6 |
| condition | enum | | 일출전 / 일출후 / 고정시 / 일몰전 / 일몰후 |
| relative_h | int | 시 | signed offset (고정시일 때는 절대 hh의 h) |
| relative_m | int | 분 | signed offset (고정시일 때는 절대 mm) |
| target_temp | float | °C | 단계 기준 환기온도 |
| insolation_adj | float | °C | signed (max offset at top of range) |
| insolation_min | float | W | |
| insolation_max | float | W | |
| accum_insolation_adj | float | °C | signed |
| accum_min | float | J | 일일 누적 |
| accum_max | float | J | 일일 누적 |
| humidity_adj | float | °C | signed |
| humidity_min | float | % | |
| humidity_max | float | % | |
| ramp_min | int | 분 | 이전 단계 → 현재 단계 선형 전이 시간 |

PK: `(compartment_id, domain, stage_no)`.

### 1-2. 단계 시작시각 계산

```
일출전: start = sunrise − offset
일출후: start = sunrise + offset
고정시: start = today  + (relative_h:relative_m)   (절대시각, 농장 로컬)
일몰전: start = sunset  − offset
일몰후: start = sunset  + offset
```

`offset = relative_h시 relative_m분` (signed). Sunrise/sunset은 §1-4.

### 1-3. 평가 함수 (순수 함수)

```python
def compute_active_setpoint(
    schedule: list[Stage],           # 오늘 stages, stage_no 순
    today_sun: SunTimes,             # 오늘 sunrise, sunset
    yesterday_last: StageWithStart,  # 어제 마지막 단계 + 그 단계의 어제 start
    now: datetime,                   # 농장 로컬 타임존
    radiation_w: float,
    accum_j: float,
    humidity_pct: float,
) -> float:
    """1분 주기로 호출. 현재 적용 setpoint (°C) 반환. 순수 함수."""
```

알고리즘:

1. **활성 단계 결정**
   - 각 단계의 start (§1-2) 계산.
   - `now`가 `[active.start, next.start)`에 속하는 단계가 활성.
   - `now < stage1.start`이면 `yesterday_last`가 활성 (전날 마지막 단계로 wrap).

2. **Base setpoint** (ramp 처리)
   - `prev` = 활성 단계의 직전 단계 (1단계의 직전은 `yesterday_last`).
   - `if now < active.start + ramp_min:`
     `progress = (now − active.start) / ramp_min`
     `base = prev.target + (active.target − prev.target) × progress`
   - `else: base = active.target`

3. **환경 보정** (선형 보간, §1-3a)
   - `Δ_일사 = offset_linear(radiation_w,  ins_min,   ins_max,   ins_adj)`
   - `Δ_누적 = offset_linear(accum_j,      accum_min, accum_max, accum_adj)`
   - `Δ_습도 = offset_linear(humidity_pct, hum_min,   hum_max,   hum_adj)`

4. **반환**: `base + Δ_일사 + Δ_누적 + Δ_습도`

#### 1-3a. 선형 보간

```
offset_linear(x, x_min, x_max, max_offset):
  if x ≤ x_min:  return 0
  if x ≥ x_max:  return max_offset
  return max_offset × (x − x_min) / (x_max − x_min)
```

부호: `max_offset`이 음수면 음의 보정. 사용자가 부호 직접 입력.
세 가지 보정 항 모두 동일한 함수 사용.

### 1-4. 일출/일몰 — 천문 계산

`models/weather/sun.py`:
- Python `astral` 라이브러리 wrapping (NOAA SPA, 대기굴절 포함)
- 입력: 농장 위경도 (`core/config.py`) + 농장 로컬 타임존 + 날짜
- 출력: `SunTimes(sunrise: datetime, sunset: datetime)`
- 정확도 ±수 초 ~ 30초. 1분 제어 주기에 충분.
- 외부 네트워크 의존 없음.

### 1-5. 단계 순서 충돌

**저장 시점** (API):
- §1-2로 계산한 start가 단조 증가가 아니면 거부 (HTTP 422).
- 응답 본문에 충돌 단계 번호 포함.

**런타임** (Control):
- 매일 자정 재계산 시 검증.
- 충돌 시 [error.md](error.md) §5에 따라 `problem.recipe.stage_conflict` 발행 → UI 배너.
- 폴백: 재계산 실패 시 어제 스케줄 그대로 사용.

### 1-6. 누적일사 리셋

- 매일 00:00 (농장 로컬 타임존) `accum_j = 0`.
- 누적값 저장 위치: `core/state.py` `derived_values` ([sensor.md](sensor.md) §9-2 패턴).

### 1-7. 1분 주기 호출 흐름

```
매분 0초:
  1. now            = NTP 동기화 시각 (core/time.py)
  2. today_sun      = sun.py (위경도, today)
  3. schedule       = State Cache (DB write-through)
  4. yesterday_last = State Cache (자정 갱신)
  5. radiation, humidity = sensor State Cache
  6. accum_j        = state.derived_values["accum_radiation_today"]
  7. setpoint       = compute_active_setpoint(...)
  8. State Cache.intents 갱신 + NATS publish "setpoint.changed"
  9. DB write-through (TimescaleDB)
```

저장 흐름의 일반 규칙은 [setpoint.md](setpoint.md) §상위 2 참고.

---

## 2. 다른 도메인 (보류)

난방·CO2·차광은 환기온도 모듈 동작 검증 후 같은 패턴으로 추가.
도메인별로 단계 수와 환경 보정 변수가 다를 수 있으므로, `setpoint_schedule.domain` 컬럼으로 분기.

**추측 금지**: 첫 모듈 동작 전에 다른 도메인의 스키마·공식을 미리 정하지 않는다.
