# Device Manager 구축 계획

> **상태**: 갱신 (2026-04-28, 2차 — concepts.md 통합 + YAML 임시 영속화 + DB 마지막 Phase 분리)
> **위치**: `croft-engine/plans/device_manager.md` (계획 — 정본 아님)
> **개념 SSOT**: `croft-engine/plans/device_manager_concepts.md` (자리 5/Manager 6/등록 UX 4계층)
> **정본 참조**: `croft-engine/architecture/croft-os.md §2`, `setpoint.md §하위 4·5`, `sensor.md §5·§9`

---

## 0. 배경

### 0-1. 현재 상태

| 영역 | 현황 |
|------|------|
| `croftos/frontend/src/screens/Devices.tsx` | Placeholder만 (1줄짜리 더미) |
| `croftos/layers/device/actuator_manager.py` | `dispatch()`만 구현. 등록·메타·통신 매핑 부재 |
| `croftos/layers/db/schema.py` | `actuator_output` 시계열 테이블만 (FK 없는 raw TEXT) |
| 장치 정의 | `ControlModule` 생성자에 문자열 하드코딩 (`motor_ids=["motor_sw_n_1"]`) |
| `croftos/layers/communication/adapters/modbus.py` | stub (메모리 더블버퍼만). 개별 장치 주소 매핑 없음 |

### 0-2. 목표

- **장치 추가 = 데이터 입력** 구조 (현재: 코드 수정)
- 운영(Modbus PLC) / 시뮬(WorldAdapter) 동일 인터페이스로 장치 조회
- 프런트엔드 `Devices.tsx`에서 인벤토리 + 상태 + (단계적) CRUD 노출
- `ActuatorManager.dispatch`가 장치 메타(통신 binding)를 참조해 송신
- **자리 5/Manager 6** 아키텍처에 정합 (concepts.md §3·§4)
- **등록 UX 4계층** (템플릿/마법사/카탈로그/저장-시 검증) 받침 (concepts.md §3·등록 UX 노트)

### 0-3. 원칙 (사용자 메모리 + 합의 누적 반영)

- **자동 추출 우선**: 장치 모델 = 현재 코드에서 추출한 것이 SSOT. UI 스케치로 정본 스키마 미리 확정 X
- **자산 절제**: 단일 장치 타입(motor/valve)으로 시작. 추측성 필드 X
- **검증 없는 자동화 거부**: D6 CRUD는 ping/read-back 검증 절차 마련 후
- **단일 모듈도 제어모듈 안**: 장치 페이지는 독립 페이지로 두되, 장치 상세는 그룹 → 모듈 트리에 연결
- **자동발견 X**: Modbus scan 등 자동발견은 채택 안 함 — 등록 UX 4계층(템플릿/마법사/카탈로그/검증)만
- **DB는 마지막 Phase**: D1~D6는 YAML 임시 영속화 + 인메모리. DDL·asyncpg Repo는 D7에서 한 번에

---

## 1. Phase 분할 — 한눈에

| Phase | 제목 | 범위 | 영속화 | 의존 |
|-------|------|------|-------|------|
| **D1** | Inventory 자동 추출 (read-only) | 코드에서 장치·센서·그룹·모듈·라벨 5튜플 추출, GET API, 리스트 UI | 메모리 | — |
| **D2** | Master Repo 인터페이스 + YAML 구현 | `DeviceRepo` 추상 + `DeviceRepoYaml` 구현 + seed 파일 | YAML (임시) | D1 |
| **D3** | DeviceRegistry → AppContext | Registry 도입, ControlModule이 registry에서 조회. 시나리오 회귀 0 | YAML (임시) | D2 |
| **D4** | ActuatorManager 정식 등록·통신 + 자리 3/4 | dispatch가 binding 참조, 분배 전략(UNIFORM/WEIGHTED/ROLE_BASED), 어댑터 라우팅 | YAML (임시) | D3 |
| **D5** | Frontend Devices 페이지 (read) + Validation 표시 | 그룹 트리·상세·상태 배지·R1~R7 검증 결과 표시 | YAML (임시) | D2 이상 |
| **D6** | CRUD + 등록 UX 4계층 | 등록/수정/삭제, 템플릿/마법사/카탈로그/저장-시 검증, ConstantSource 등록 | YAML (임시) | D4·D5 |
| **D7** | **DB 영속화 (마지막)** | DDL + asyncpg Repo 구현 + YAML→DB seed 마이그 + **YAML 임시 코드 일괄 제거** | DB (정본) | D6 |

---

## 2. 영속화 전략 (D1~D6: YAML 임시, D7: DB)

### 2-1. YAML 파일 구조 (D2~D6 동안)

```
croftos/data/devices/                    # @임시-yaml: D7 DB 영속화 시 제거
├── default-farm.yaml                    # farm + compartments
├── default-sensors.yaml                 # SensorDevice + publishes
├── default-actuators.yaml               # ActuatorGroup + ActuatorDevice
├── default-modules.yaml                 # ControlModule instances
└── default-constants.yaml               # ConstantSource (gap fill)
```

> v0 시작 단계는 `default` 1개. 농장간 차이는 D6에서 템플릿 도입.

### 2-2. 임시 마커 컨벤션 (정리 추적용)

**모든 YAML 영속화 관련 코드·파일에 명시 마커**:

```python
# @임시-yaml: Device Manager D7 DB 영속화 시 device_repo_postgres.py로 교체
class DeviceRepoYaml(DeviceRepo):
    ...
```

```yaml
# @임시-yaml: Device Manager D7 진입 시 DB seed로 마이그 후 파일 삭제
farm:
  farm_id: default
  ...
```

**Phase 진입 시점 검증**:
- 매 Phase 마무리: `grep -rn "@임시-yaml" croftos/ tests/` 로 마커 누락 점검
- D7 진입 시: 위 grep 결과 = D7에서 *제거·마이그할 대상 전체 목록*

### 2-3. Repo 인터페이스 (D2에 정의, D7에 swap)

```python
# core/repo/device_repo.py — 인터페이스 (불변)
class DeviceRepo(Protocol):
    async def list_groups(self, farm_id: str) -> list[ActuatorGroup]: ...
    async def list_devices(self, group_id: str) -> list[ActuatorDevice]: ...
    async def list_sensors(self, scope: Scope, scope_id: str) -> list[SensorDevice]: ...
    async def list_modules(self, farm_id: str) -> list[ControlModuleInstance]: ...
    async def list_constants(self, scope, scope_id) -> list[ConstantSource]: ...
    async def upsert_*(...): ...   # D6 CRUD에서 활성

# layers/device/device_repo_yaml.py — 구현체 1 (임시)
# @임시-yaml: D7에서 device_repo_postgres.py로 교체
class DeviceRepoYaml(DeviceRepo):
    ...

# D7에서 신설:
# layers/db/device_repo_postgres.py — 구현체 2 (정본)
class DeviceRepoPostgres(DeviceRepo):
    ...
```

→ **Manager 코드는 `DeviceRepo` 인터페이스만 호출**. D7 swap 시 *Manager·ControlModule 0줄 수정*.

---

## 3. Phase 상세

### Phase D1 — Inventory 자동 추출 (read-only)

**목표**: 현재 코드에 박혀있는 *모든 자산*을 자동 추출. 5튜플로 정규화.

**산출물**
- `croftos/layers/device/inventory.py` (신규)
  - dataclass:
    - `ActuatorGroupExtract(group_id, kind, compartments, module_kind)`
    - `ActuatorDeviceExtract(actuator_id, group_id, kind, role, compartment_id)`
    - `SensorDeviceExtract(sensor_id, scope, scope_id, lvs)` *— 시뮬 WorldAdapter 1개 + KMA WeatherSource 1개로 시작*
    - `ControlModuleExtract(module_kind, group_id, input_label, output_label, sensor_aggregation)`
    - `ConstantSourceExtract(scope, scope_id, lv, value, rationale)` *— 일단 빈 list*
  - `extract_from_app(app: FastAPI | AppContext) -> InventorySnapshot`
- `croftos/layers/api/devices.py` (신규)
  - `GET /devices/inventory` — 5튜플 통합 응답
- `croftos/frontend/src/screens/Devices.tsx`
  - 5개 섹션 단순 테이블 (group / device / sensor / module / constant)
  - 빈 상태 / 에러 상태 분기
- `tests/test_device_inventory.py`
  - SkyWindow + HeatingPipe 인스턴스 → 추출 결과 매칭
  - INPUT/OUTPUT_LABEL 자동 라벨링 검증 (concepts.md §8-3)
- `tests/api/test_devices_inventory.py`
  - GET /devices/inventory 응답 스키마

**의도적으로 안 하는 것**
- 영속화 (메모리만)
- 통신 binding (D4)
- 상태 표시 (D5)
- 등록·수정 폼 (D6)

**검증 포인트**
- 추출 결과에 빠진 자산 없는가
- `kind` 분류 자연스러운가
- D2 YAML 스키마로 그대로 옮길 만한 모양인가

---

### Phase D2 — Master Repo 인터페이스 + YAML 구현

**목표**: D1에서 검증된 모양으로 **DeviceRepo 인터페이스** 정의 + **YAML 구현체** 작성. seed 파일에 D1 추출 결과 반영.

**산출물**
- `croftos/core/repo/device_repo.py` (신규) — `DeviceRepo` Protocol
- `croftos/layers/device/device_repo_yaml.py` (신규)
  - `# @임시-yaml: D7에서 device_repo_postgres.py로 교체`
  - YAML 5파일 로드 + dataclass 변환 + 캐시
- `croftos/data/devices/default-*.yaml` (신규) — *모두 `@임시-yaml`* 헤더
- `croftos/layers/api/devices.py` 갱신 — D1 인메모리 → YAML repo로 전환
- `tests/test_device_repo_yaml.py`
  - YAML 라운드트립
  - upsert idempotent
  - 누락 파일 / 잘못된 키 에러 처리

**Q1 결정** (D2 시작 시):
- YAML payload 안에서 `comm_binding`은 dict 그대로 (`{"protocol": "modbus", "addr": ...}`). D7에서 JSONB로 옮길 때 그대로 복사.

**Q2 결정** (D2 시작 시):
- `actuator_device → compartment` 직결 X. **`actuator_group.compartments` 리스트로만**. ROLE_BASED일 때 `actuator_device.role`로 위치 표현.

**검증**
- D1 추출 결과 = D2 YAML seed로 변환 후 round-trip 동일
- 부팅 시간 측정 — YAML 5파일 로드 비용 baseline

---

### Phase D3 — DeviceRegistry → AppContext

**목표**: 장치·센서·그룹·모듈 = 코드 인자가 아니라 *런타임 조회 대상*. ControlModule 생성자 단순화.

**산출물**
- `croftos/core/device_registry.py` (신규)
  - `DeviceRegistry(repo, cache)` — lazy-load + 메모리 캐시
  - `list_devices(group_id)`, `list_sensors(...)`, `get_module(instance_id)`, etc
  - `invalidate(scope, scope_id)` (D6 reload용)
- `AppContext`에 `devices: DeviceRegistry` 필드 추가
- `ControlModule` 리팩터:
  - `motor_ids` / `valve_ids` 생성자 인자 제거
  - `distribute()`에서 `ctx.devices.list_devices(self.actuator_group_id)`로 조회
- `simulation.py` / 운영 lifespan: 모듈 생성자 변경 반영
- `tests/test_device_registry.py`
- 기존 시나리오 회귀 — `test_baseline_no_env`, `test_clear_day_curve`, `test_clear_vs_cloudy` 결과 동일

**Q3 결정** (D3 시작 시):
- 그룹 ↔ 모듈 매핑 위치 = **YAML (modules.yaml)에 instance.group_id 필드**. 코드 하드코딩 X.

**검증**
- 시나리오 회귀 0
- `ControlModule.REQUIRES_SENSORS` 같은 클래스 메타와 충돌 없는가

---

### Phase D4 — ActuatorManager 정식 등록·통신 + 자리 3/4

**목표**: dispatch가 registry에서 binding 조회 → 어댑터 라우팅. **DistributionStrategy(자리 3) + CommunicationAdapter(자리 4)** 정식.

**산출물**
- `croftos/layers/device/distribution.py` (신규)
  - `DistributionStrategy` Protocol
  - `UniformDistribution`, `WeightedDistribution` 구현
  - `RoleBasedDistribution` — 위임만 (알고리즘이 list 직접 산출)
- `croftos/layers/communication/adapter_registry.py` (신규)
  - `CommunicationAdapter` Protocol
  - 등록 메커니즘 (`register("modbus", ModbusAdapter)`, `register("simulator-world", ...)`)
- `actuator_manager.py` 리팩터:
  - `dispatch()`: 각 ActuatorOutput에 대해
    1. State Cache 갱신
    2. `device = registry.get(actuator_id)` → `comm_binding.protocol` 참조해 어댑터 선택
    3. `adapter.send(device, value)`
    4. *DB INSERT 보류* — `# @임시-yaml: D7에서 actuator_output INSERT 활성`. 일단 인메모리 ring buffer에 적재
    5. NATS publish
- `ControlModule.distribute()` 갱신:
  - INPUT_LABEL/OUTPUT_LABEL 라우팅
  - `device.distribution_weight` 적용 — UNIFORM 또는 WEIGHTED
- `tests/test_actuator_manager_dispatch.py` 확장 — 분배 검증, 어댑터 분기 검증

**Q4 결정** (D4 시작 시):
- `distribution_weight` 단위 = **자유 가중치** (sum 자동 정규화). NULL = UNIFORM.

**검증**
- modbus stub 사용 — 실제 PLC 연결 X
- WEIGHTED 분배 시 합계 보존
- ROLE_BASED는 알고리즘 산출 그대로 통과

---

### Phase D5 — Frontend Devices 페이지 (read) + Validation 표시

**목표**: 사용자가 인벤토리·상태·**검증 결과**를 시각으로 확인. R1~R7 위반이 있으면 빨갛게.

**산출물**
- `croftos/core/validation.py` (신규)
  - R1~R7 검증 함수 (concepts.md §7-1)
  - 입력: Registry 스냅샷 → 출력: `ValidationReport(violations, gaps, conflicts)`
- `croftos/layers/api/validation.py` (신규)
  - `GET /devices/validation` — ValidationReport 반환
- `croftos/frontend/src/screens/Devices.tsx` 본 구현
  - 좌측: 그룹 트리 (compartment → actuator_group → device) + 센서 트리 + 모듈 list
  - 우측: 선택 상세 + 상태 배지 (last_command_at, last_value, online)
  - 상단: ValidationReport 요약 배지 (OK / N violations)
- `croftos/frontend/src/api/client.ts` — devices/sensors/modules/validation API
- 백엔드 `GET /devices/groups`, `GET /sensors`, `GET /modules`, `GET /devices/:id/status`

**Q5 결정** (D5 시작 시):
- 상태 = **NATS bridge** (sensor.update / actuator.command 구독). polling fallback 1s.

**검증**
- D3 등록 자산이 모두 트리에 노출
- 시뮬 실행 중 last_command 갱신 반영 (NATS bridge 또는 polling)
- 의도적으로 누락된 LV (예: CO2 미등록) → ValidationReport에 빨간 표시

---

### Phase D6 — CRUD + 등록 UX 4계층

**진입 조건** (사용자 명시 승인):
- D4 완료 + 검증 시나리오 (ping, register read-back) 설계 마련
- 자동화 안전장치 (잘못된 binding으로 PLC 손상 차단) 설계 마련

**목표**: 등록 UX 4계층(템플릿/마법사/카탈로그/저장-시 검증) 본격. *YAML 위에서* 동작.

**산출물**
- 계층 [1] **템플릿**:
  - `croftos/data/devices/templates/standard-tomato-4comp.yaml` (예시)
  - 적용 UI: 템플릿 선택 → YAML payload 일괄 적용 → 차이만 사용자 수정
- 계층 [3] **마법사**:
  - 7 abstraction 순서 (Farm → Compartment → SensorDevice → ActuatorGroup → ActuatorDevice → ControlModuleInstance → ConstantSource) step-by-step
  - 이전 step 결과로 다음 step 드롭다운 제한
- 계층 [4] **카탈로그**:
  - `module_kind_catalog.yaml` — 알려진 모듈의 라벨·요구LV 자동
  - `device_kind_catalog.yaml` — 알려진 장치 종 (보일러 모델 X = TEMP+VALVE 자동)
  - `# @임시-yaml: D7에서 DB 테이블로 마이그`
- 계층 [5] **저장-시 검증**:
  - upsert 직전에 R1~R7 시뮬레이션 → 위반이면 응답 reject + 안내
  - "CO2 센서 누락 → ConstantSource 420ppm 등록할까?" 자동 제안 UX
- 등록/수정/soft-delete 폼 (YAML upsert)
- 통신 binding 편집기 (Modbus 주소 / register / scale / offset)
- 감사 로그 (`audit_log.yaml` 임시) — `# @임시-yaml`

**원칙**
- *검증 없는 자동화 거부*. 저장-시 검증 통과 전 활성화 X
- 자동발견(Modbus scan 등) 도입 X — 템플릿이 그 자리를 채움

---

### Phase D7 — DB 영속화 (마지막)

**진입 조건**:
- D6 완료 + 등록 UX 4계층이 YAML 위에서 안정 동작
- Manager 인터페이스 변경 압력 없음

**목표**: YAML 임시 영속화 → DB 정본으로 한 번에 마이그. *Manager·ControlModule 코드 0줄 수정*.

**산출물**
- `croft-engine/plans/device_manager_concepts.md` **§16 "DB 영역 6개 분담"** 신설
  - A. Master / B. Setpoint(기존) / C. Sensor 시계열 / D. Observability(기존) / E. Catalog/Seed / F. Validation View
- `croftos/layers/db/device_schema.py` (신규)
  - 마스터 9개 테이블 (farm·compartment 기존 + sensor_device·sensor_publishes·actuator_group·actuator_group_compartment·actuator_device·control_module_instance·data_source_constant)
  - 카탈로그 (module_kind_catalog·device_kind_catalog·farm_template)
  - Validation View (v_published_lvs·v_required_lvs·v_lv_gaps·v_lv_conflicts)
- `croftos/layers/db/device_repo_postgres.py` (신규) — `DeviceRepoPostgres(DeviceRepo)`
- `croftos/layers/db/migrations/yaml_to_db.py` (신규)
  - `croftos/data/devices/*.yaml` → INSERT
  - `croftos/data/devices/templates/*.yaml` → `farm_template.payload`
- 기존 호환:
  - `actuator_output.actuator_id` → `actuator_device(actuator_id)` FK 추가
  - `actuator_group_command.actuator_group_id` → `actuator_group(actuator_group_id)` FK
  - `actuator_group_command.compartment_id` NULL 허용 (v2 호환)
- `core_metric_ts`·`weather_*`·`simulation_*` 손대지 않음
- D4의 `# @임시-yaml: actuator_output INSERT 보류` 해제 — 정식 INSERT 활성
- AppContext lifespan: `DeviceRepoYaml` → `DeviceRepoPostgres` swap

**임시 코드 정리 (필수)**
```bash
grep -rn "@임시-yaml" croftos/ tests/ croftos/data/
```
- 모든 hit 처리 (제거 또는 마이그):
  - `device_repo_yaml.py` → 삭제
  - `croftos/data/devices/*.yaml` → seed 후 *파일 삭제*
  - `actuator_manager.py:@임시-yaml` 라인 → DB INSERT로 교체
  - `audit_log.yaml` → DB `audit_log` 테이블로
- 마무리 `grep` 결과 0건 확인 — *완료 조건*

**검증**
- 시뮬 시나리오 회귀 0
- ValidationReport 결과 동일 (YAML vs DB)
- `actuator_output` 시계열 INSERT 정상
- 부팅 시간 비교

---

## 4. 결정 보류 사항 (각 Phase 시작 전 확정)

| # | 사항 | 후보 | 결정 시점 |
|---|------|------|-----------|
| Q1 | YAML payload `comm_binding` 모양 | 평면 dict / 중첩 | D2 |
| Q2 | `actuator_device ↔ compartment` 관계 | 직결 / group 경유만 | D2 (group 경유로 권장) |
| Q3 | 그룹 ↔ 모듈 매핑 위치 | YAML / 코드 / 둘다 | D3 (YAML 권장) |
| Q4 | `distribution_weight` 단위 | 비율 (sum=1) / 자유 가중치 | D4 (자유 권장) |
| Q5 | 상태 갱신 채널 | NATS bridge / polling | D5 |
| Q6 | 템플릿 카탈로그 위치 (D7 진입 시) | DB 테이블 / 코드 동봉 / 외부 | D7 |
| Q7 | 누락 LV → ConstantSource 자동 제안 UX | 모달 / 인라인 | D6 |
| Q8 | DB 진입 시 `actuator_group_command.compartment_id` 처리 | NULL 허용 / 컬럼 제거 | D7 |

---

## 5. K-list (concepts.md K1~K10 + 본 plan 추가)

| # | 사항 | 결정 시점 |
|---|------|---------|
| K1 | DEVICE_COMMAND echo를 LV로 만들지 | D4 (다른 모듈이 액추에이터 상태 입력으로 쓸 때) |
| K2 | 시뮬에 CO2 모델 추가 시점 | Phase 11+ (시뮬 plan) |
| K3 | LIGHT 영역 PAR/PPFD 도입 시점 | 광 도메인 모듈 추가 시 |
| K4 | TEMP_LEAF 변수 추가 (VPD_LEAF용) | crop 모델 확장 시 |
| K5 | 다중 출처 충돌 검증 위치 | D2~D3 |
| K6 | ConstantRegistry 임계치 (운영 완성도 지표) | D6 또는 운영 도입 시 |
| K7 | 묵시적 fallback (`world_adapter.py:122-125`) 제거 시점 | 별 Phase |
| K8 | DEVICE_COMMAND를 갭 처리 source 후보로 인정할지 | D4 |
| K9 | REPRESENTATIVE 입력 정책 도입 시점 | 필요 시 |
| K10 | DB 영역 6개 + 메모리 + 코드 분담 정리 | **D7 진입 시** (concepts.md §16 신설) |

---

## 6. 임시 코드 정리 추적

**원칙**: 모든 YAML 영속화 관련 코드·파일에 `@임시-yaml` 마커. D7 진입 시 *운영 경로*는 DB로 전환.

**Phase별 마커 추가 대상**:

| Phase | 마커 추가 대상 | 정리 시점 |
|-------|-------------|---------|
| D2 | `device_repo_yaml.py`, `croftos/data/devices/*.yaml` | D7 (YAML repo는 시뮬에서 보존) |
| D4 | `actuator_manager.py` 의 `actuator_output INSERT 보류` 라인 | D7 후속 (worker 도입 시) |
| D6 | `templates/*.yaml`, `module_kind_catalog.yaml`, `device_kind_catalog.yaml`, `audit_log.yaml` | D7 (DB seed source로 보존) |

**검증 명령**:
```bash
grep -rn "@임시-yaml" croftos/ tests/ croftos/data/
```
- 매 Phase 마무리 시 실행 → 마커 누락 점검
- D7 진입 시 = 정리 대상 *전체 목록*

**D7 완료 조건 (현실 버전)** — concepts.md §16-7 참조:
- ✅ **운영 경로** (`main.py` lifespan, `repo=DeviceRepoPostgres`) — 마커 제거
- ✅ **DDL + Repo + seed** 마이그 코드 작성
- ⚠️ **시뮬 fixture / 테스트 conftest / `croftos/data/devices/*.yaml`** — 보존 (시뮬 격리 + DB seed source)
  - YAML 파일의 마커 의미 변경: *임시 영속화* → *historical seed source*
  - 시뮬 fixture를 DB 인프라로 옮기는 작업은 **별 후속 PR**로 deferred (시뮬 conftest의 `make_context()` default repo)
- 결과: grep 0건은 *완전한* D7 완료 — 시뮬·테스트 인프라 cleanup PR로 분리.

---

## 7. 작업 흐름 (세션 운용)

1. **세션 1**: 본 문서 검토 → D1 합의 → D1 구현 → 추출 결과 사용자 확인
2. **세션 2**: D1 결과로 Q1·Q2 결정 → D2 구현 (Repo 인터페이스 + YAML) → seed 검증
3. **세션 3**: Q3 결정 → D3 구현 → 시나리오 회귀 0 확인
4. **세션 4**: Q4 결정 → D4 구현 → 분배·어댑터 검증
5. **세션 5**: Q5 결정 → D5 구현 → Validation 시연
6. **세션 6**: D6 진입 가부 별도 논의 → 등록 UX 4계층 구현
7. **세션 7**: D7 진입 가부 → DB 영속화 + YAML 코드 일괄 제거 + concepts.md §16 작성

각 세션 시작 시 본 문서 + `device_manager_concepts.md` + `MEMORY.md`의 `feedback_*` 로드.

---

## 8. 변경 이력

- 2026-04-28 (1차): 초안 — D1~D6 (Sonnet 4.6, Device 페이지 placeholder 출발)
- 2026-04-28 (2차): 합의 누적 반영
  - concepts.md §3·§4·§7·§8 통합 (자리 5/Manager 6/등록 UX 4계층)
  - 영속화 전략 명문화: D1~D6 YAML 임시, D7 DB 영속화 + YAML 코드 제거
  - `@임시-yaml` 마커 컨벤션 + Phase별 추가/정리 추적표 (§6)
  - 자동발견 채택 안 함 (사용자 결정)
  - Phase D5에 Validation 표시 추가 (R1~R7)
  - Phase D6에 등록 UX 4계층 (템플릿/마법사/카탈로그/검증) 명문화
  - **Phase D7 신설** — DB 영속화 + YAML 코드 일괄 제거 + concepts.md §16 작성
  - K-list 정리 (concepts.md K1~K10 통합)
  - Q-list에 Q6~Q8 추가
