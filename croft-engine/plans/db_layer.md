# DB Layer 통합 Plan (DB1 ~ DB8)

YAML 임시 영속화를 모두 도태하고 DB Layer를 SSOT로 만든다.

---

## 책임

| 문서 | 역할 |
|------|------|
| **본 문서 (db_layer.md)** | YAML 도태 + DB SSOT 전환 phase plan |
| [db_layer_erd.md](db_layer_erd.md) | 현재 DB 스키마 정본 (실제 DDL 기반) |
| [erd_6layer.md](erd_6layer.md) | 6-Layer 종착지 데이터 모델 (갭 분석) |
| [device_manager.md](device_manager.md) | Device Manager D1~D7 plan (D7은 이미 끝, 본 plan은 D7의 *완성*) |

**본 plan 범위:**
- 운영·시뮬·테스트 모든 경로에서 YAML 제거
- DB Repo 단일 SSOT
- audit log + actuator output INSERT 활성
- pytest DB fixture 인프라

**범위 밖 (별도 plan 필요):**
- `sensor_value_l0~l3` 4단계 시계열 — 별도 `sensor_timeseries.md`
- `greenhouse_params`, `crop_params` DB 이전 — 별도 `models_db.md`
- 인증·세션 — 별도 `auth.md` (deferred)

---

## 종착지

`grep -rn "DeviceRepoYaml\|yaml.safe_load\|@임시-yaml" croftos/ tests/` 결과:
- `croftos/data/devices/*.yaml`: 0건 (파일 자체 삭제 또는 `seeds/`로 이전)
- `croftos/layers/device/device_repo_yaml.py`: 0건 (모듈 삭제)
- `croftos/layers/device/audit_log.py`: 0건 (`db/audit_repo.py`로 이전)
- `croftos/layers/device/template_loader.py`: 0건 (DB read로 전환)
- `croftos/layers/device/catalog.py`: 0건 (DB read로 전환)
- `tests/test_device_repo_yaml.py`, `tests/test_repo_upsert.py`: 삭제
- `tests/api/conftest.py`: DB fixture 사용

**`pyproject.toml`의 `pyyaml` 의존:** KMA parser·기타 사용처 검토 후 결정 (DB7).

---

## 현황 (2026-04-30)

운영 lifespan은 `DeviceRepoPostgres` 사용. 그러나 *YAML이 7군데에서 살아있음*:

| # | 위치 | 상태 |
|---|------|------|
| 1 | `core/app_context.py` make_context default | YAML silent fallback 🔴 |
| 2 | `layers/api/simulation.py:129` | 시뮬 ctx YAML repo 🟠 |
| 3 | `layers/device/device_repo_yaml.py` | 모듈 자체 |
| 4 | `layers/device/audit_log.py` | **운영 경로 안에서** YAML write 🔴 |
| 5 | `layers/device/template_loader.py` | 템플릿 YAML read |
| 6 | `layers/device/catalog.py` | 카탈로그 YAML read |
| 7 | `layers/device/actuator_manager.py` ring buffer | DB INSERT 미구현 🟠 |

DB schema는 거의 완비 ([db_layer_erd.md](db_layer_erd.md) 참조). 결손:
- `device_audit_log` 테이블 (DB1 신설)
- `schema_migrations` 테이블 (DB1 신설)

---

## Phase 분할

각 Phase는 1세션 단위. 끝날 때마다:
- ✅ 467 tests 재통과
- ✅ `grep "@임시-yaml" | wc -l` 카운트 감소 확인
- ✅ 운영 lifespan 정상 부팅 확인 (`croft-up.bat`)

---

### Phase DB1 — Foundation 보강

**목표:** 결손 테이블 신설 + silent fallback 제거 + 트랜잭션 격리 명시.

**작업:**
1. `croftos/layers/db/device_schema.py`에 `device_audit_log` DDL 추가
   ```sql
   CREATE TABLE IF NOT EXISTS device_audit_log (
     audit_id BIGSERIAL PRIMARY KEY,
     ts TIMESTAMPTZ NOT NULL DEFAULT now(),
     actor TEXT,
     resource_type TEXT NOT NULL,
     resource_id TEXT NOT NULL,
     action TEXT NOT NULL,
     result TEXT NOT NULL,
     detail JSONB
   );
   CREATE INDEX device_audit_log_ts_idx ON device_audit_log (ts DESC);
   CREATE INDEX device_audit_log_resource_idx ON device_audit_log (resource_type, resource_id);
   ```
2. `croftos/layers/db/migrations_schema.py` 신설
   ```sql
   CREATE TABLE IF NOT EXISTS schema_migrations (
     version TEXT PRIMARY KEY,
     applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
     checksum TEXT,
     description TEXT
   );
   ```
3. `core/app_context.py`의 `make_context(repo=None)` → repo 미주입 시 `ValueError` raise
4. `init_pool`에 isolation level 명시 (READ COMMITTED 또는 REPEATABLE READ — Q-META 결정)
5. `db_layer_erd.md` §6 → §1로 승격 (planned → 구현됨)

**검증:**
- `make_context()` (인자 없음) → `ValueError`
- 운영 부팅 시 두 테이블 존재 확인

**산출물:** DDL 2개, fail-fast 1줄, 문서 1개 동기화

**위험:** 낮음

---

### Phase DB2 — audit_log → DB

**목표:** 운영 경로의 YAML write 제거.

**작업:**
1. `croftos/layers/db/audit_repo.py` 신설
   - `async def append_audit(pool, *, actor, resource_type, resource_id, action, result, detail)`
   - `async def read_audit_log(pool, *, resource_type=None, resource_id=None, limit=100)`
2. `croftos/layers/api/devices.py`의 `append_audit` 호출 → 신규 audit_repo로 교체
3. `croftos/layers/device/audit_log.py` 삭제
4. `croftos/data/devices/audit_log.yaml` 삭제
5. 기존 `tests/api/test_constants_crud.py::test_audit_log_records_create_and_reject` 재작성 (DB read)

**검증:**
- 운영 lifespan에서 CRUD 1번 → `device_audit_log` row 1개
- `audit_log.yaml` 파일 *생성 안 됨*
- `grep -rn "audit_log.yaml" .` = 0

**산출물:** repo 1, 모듈 1 삭제, 파일 1 삭제

**위험:** 낮음 — 단방향 흐름

---

### Phase DB3 — actuator_output ring → DB

**목표:** `[3]→[4]` 추적 활성. 인메모리 ring buffer 폐기.

**작업:**
1. `croftos/layers/db/actuator_output_repo.py` 신설
   - `async def insert_outputs(pool, outputs: list[ActuatorOutput])` — batch INSERT
2. `croftos/layers/device/actuator_manager.py`
   - `dispatch()` 끝에 `await self._output_repo.insert_outputs(...)` 추가
   - ring buffer는 `enabled_for_debug=False` 옵션으로만 유지 (또는 완전 제거)
3. 운영 ctx에 pool 주입 (lifespan)
4. `actuator_group_command` INSERT도 동시에 활성 (Q-CTRL 권고: 추적 위해)

**검증:**
- 운영 1분 가동 후 `SELECT COUNT(*) FROM actuator_output WHERE issued_at > now() - interval '5min'` > 0
- ring buffer test 삭제 또는 debug 모드 전용으로 전환

**산출물:** repo 1, 모듈 1 수정

**위험:** 중간 — DB write 경로가 매분 hot path. batch + 비동기 보장 필요.

---

### Phase DB4 — pytest DB fixture (가장 큰 phase) ⚠

**목표:** 단위 테스트가 DB Repo 사용하도록 인프라 구축. **시뮬·conftest의 YAML repo를 DB로 옮길 토대.**

**전략 결정 — Q-TEST (default: schema-per-test)**:

| 옵션 | 격리 | 속도 | 의존 |
|------|------|------|------|
| A. testcontainers | 컨테이너/세션 | 느림 | docker-py |
| B. 공유 DB + tx rollback | 트랜잭션/테스트 | 빠름 | docker-compose 상시 |
| **C. schema per test** ✅ | schema/테스트 | 매우 빠름 | docker-compose 상시 |

→ **C 채택** (별도 결정 없으면 default).

**작업:**
1. `tests/conftest.py` 루트 신설
   ```python
   @pytest.fixture(scope="session")
   async def db_pool():
       pool = await asyncpg.create_pool(TEST_DSN, ...)
       yield pool
       await pool.close()

   @pytest.fixture
   async def db_schema(db_pool, request):
       schema = f"test_{uuid4().hex[:8]}"
       async with db_pool.acquire() as conn:
           await conn.execute(f"CREATE SCHEMA {schema}")
           await conn.execute(f"SET search_path TO {schema}")
           # 모든 DDL 실행
       yield schema
       async with db_pool.acquire() as conn:
           await conn.execute(f"DROP SCHEMA {schema} CASCADE")
   ```
2. `tests/api/conftest.py`의 `make_context(repo=DeviceRepoYaml())` → `make_context(repo=DeviceRepoPostgres(test_pool))`
3. 기존 단위 테스트 중 YAML repo 의존 부분 (`test_actuator_manager.py`, `test_device_registry.py` 등) 검토 — fixture 추가
4. CI가 timescaledb 의존하도록 `docker-compose.test.yml` 업데이트

**검증:**
- 모든 467 tests 통과
- `grep -rn "DeviceRepoYaml" tests/` = 0건 (단, `test_device_repo_yaml.py`는 DB6에서 삭제 예정이므로 제외)

**산출물:** conftest 1, fixture 2, docker-compose 업데이트

**위험:** 🔴 **높음** — 테스트 인프라 재설계. 잘못하면 모든 테스트 깨짐. 2 세션 가능성.

---

### Phase DB5 — Catalog·Template DB read 전환

**목표:** YAML 파일 직접 read 코드 제거. seed_yaml_to_db는 boot 1회 시드로만 유지.

**작업:**
1. `croftos/layers/device/catalog.py` → `croftos/layers/db/catalog_repo.py`
   - `async def list_module_kinds(pool)`, `list_device_kinds(pool)`
   - `Catalog` 클래스는 pool 주입받는 thin wrapper
2. `croftos/layers/device/template_loader.py` → `croftos/layers/db/template_repo.py`
   - `async def list_templates(pool)`, `load_template(pool, template_id)`
3. `croftos/layers/api/devices.py`의 catalog/template 핸들러 수정
4. 기존 YAML 파일 (`module_kind_catalog.yaml`, `device_kind_catalog.yaml`, `*-template.yaml`)은 DB7에서 처리

**검증:**
- `grep -rn "yaml.safe_load" croftos/layers/device/` = 0건
- 운영 lifespan 시 카탈로그 API 정상 응답

**산출물:** repo 2, 모듈 2 삭제 (또는 DB read wrapper로 축소)

**위험:** 낮음

---

### Phase DB6 — DeviceRepoYaml 폐기

**목표:** YAML repo 모듈 자체 제거.

**작업:**
1. `croftos/layers/api/simulation.py:129` `DeviceRepoYaml()` → `DeviceRepoPostgres(sim_pool)` (DB4 fixture 응용)
2. `croftos/layers/device/device_repo_yaml.py` 삭제
3. `croftos/layers/db/yaml_to_db_seed.py`의 `from ... DeviceRepoYaml` 처리:
   - 옵션 A: yaml 직접 파싱으로 변경 (seed_yaml_to_db 함수 내부)
   - 옵션 B: DB7에서 함수 자체 폐기 → 옵션 A는 임시
4. `tests/test_device_repo_yaml.py` 삭제
5. `tests/test_repo_upsert.py` 삭제 (또는 `test_device_repo_postgres.py`로 흡수)
6. `tests/api/conftest.py`의 YAML import 제거

**검증:**
- `grep -rn "DeviceRepoYaml" .` = 0건
- 시뮬 API (`POST /api/simulations`) 정상 동작 확인

**산출물:** 모듈 1 삭제, 테스트 2 삭제

**위험:** 낮음 (DB4 끝나면 기계적)

---

### Phase DB7 — YAML 파일 정리

**목표:** `croftos/data/devices/*.yaml` 8개 파일 처리.

**의사결정 — Q-SEED**:

| 옵션 | 의미 |
|------|------|
| A. 모두 삭제 | 신규 운영 환경 부팅 시 빈 DB → UI/template으로 등록 |
| B. SQL seed로 변환 | `croftos/data/seeds/*.sql` — 부팅 시 1회 실행 |
| **C. 별도 디렉토리로 이전** ✅ | `croftos/data/seeds/devices/*.yaml` — `@db-seed-source` 마커로 의미 변경, 향후 옵셔널 시드 |

→ **C 권고** (default). 이전 `device_manager_concepts.md §16-7`이 같은 결정.

**작업:**
1. `croftos/data/devices/` → `croftos/data/seeds/devices/`로 이동 (`audit_log.yaml`은 DB2에서 이미 삭제됨)
2. 헤더 마커 변경: `@임시-yaml` → `@db-seed-source` (의미: 시드 source로 보존)
3. `croftos/layers/db/yaml_to_db_seed.py` 경로 업데이트, 함수명도 변경 검토 (`seed_to_db`)
4. `pyproject.toml` `pyyaml` 의존 — KMA parser 등 다른 사용처 검토 후 유지/제거 결정

**검증:**
- `grep -rn "@임시-yaml" .` = 0건
- 운영 lifespan 정상 부팅 (seed 동작 확인)

**산출물:** 디렉토리 이동, 헤더 갱신

**위험:** 낮음

---

### Phase DB8 — Schema migration 정식화 (선택)

**목표:** `CREATE TABLE IF NOT EXISTS` 패턴을 명시적 마이그레이션으로 전환.

**의사결정 — Q-META-1**:

| 옵션 | 의미 |
|------|------|
| A. alembic | Python 표준, autogenerate, downgrade 지원 |
| **B. 자체 schema_migrations** ✅ | 가벼움, 순서 보장, downgrade 없음 |

→ **B 권고** (default). 운영 schema 진화가 잦아지면 A로 전환.

**작업:**
1. `croftos/layers/db/migrations/` 디렉토리 신설
   - `0001_initial.sql` — 현재 모든 DDL
   - `0002_audit_log.sql` — DB1 신설
   - `0003_actuator_output_fk.sql` — DB3
2. `croftos/layers/db/migrator.py`
   - `async def run_migrations(pool)` — `schema_migrations` 테이블 조회 후 미적용 SQL 순차 실행 + checksum 기록
3. lifespan의 `init_*_schema()` 호출 → `run_migrations()`로 통합
4. `*_schema.py` 파일들의 DDL 상수는 `0001_initial.sql`로 dump 후 모듈은 폐기

**검증:**
- 부팅 시 `SELECT version FROM schema_migrations ORDER BY applied_at` = `['0001_initial', '0002_audit_log', '0003_actuator_output_fk']`
- DDL 변경 시 새 SQL 파일 추가하면 자동 적용

**산출물:** SQL 파일 3개, migrator 1, lifespan 단순화

**위험:** 중간 — 기존 운영 DB 마이그 검증 필요. 신규 환경에는 영향 없음.

**보류 가능:** DB1~DB7 완료 후 운영 schema 변경 필요성이 생기는 시점에. 현 단계에서는 *idempotent CREATE*로 충분.

---

## 종속성 그래프

```
DB1 (foundation: audit_log + schema_migrations + fail-fast)
 │
 ├─→ DB2 (audit_log DB write)         ─┐
 ├─→ DB3 (actuator_output INSERT)     ─┤
 │                                      │
 └─→ DB4 (pytest DB fixture) ⚠         ─┤
       │                                │
       ├─→ DB5 (catalog/template DB)    │
       └─→ DB6 (DeviceRepoYaml 폐기)    │
             └─→ DB7 (YAML 파일 정리) ←─┘
                   └─→ DB8 (schema migration 정식화) [선택]
```

**핵심 분기점:** DB4. 여기서 schema-per-test 패턴이 자리 잡으면 DB5~DB7은 빠르게 진행.

---

## 비용 추정

| Phase | 세션 | 위험 |
|-------|------|------|
| DB1 | 1 | 🟢 낮음 |
| DB2 | 1 | 🟢 낮음 |
| DB3 | 1 | 🟡 중간 |
| DB4 | **2** | 🔴 **높음** |
| DB5 | 1 | 🟢 낮음 |
| DB6 | 1 | 🟢 낮음 |
| DB7 | 0.5 | 🟢 낮음 |
| DB8 | 2 | 🟡 중간 (선택) |

**총 7~9 세션.** DB4가 타임sink.

---

## 의사결정 지점

진행 전 또는 해당 phase 시작 시 확정 필요.

| Q | 위치 | default 권고 | 결정 |
|---|------|-------------|------|
| Q-META-1 | DB8 | 자체 `schema_migrations` (alembic 보류) | ⏸ |
| Q-META-2 | DB1 | isolation level `READ COMMITTED` 명시 | ⏸ |
| Q-CTRL-1 | DB3 | `actuator_group_command` INSERT 동시 활성 | ⏸ |
| Q-TEST | DB4 | schema-per-test (옵션 C) | ⏸ |
| Q-SEED | DB7 | `data/seeds/devices/`로 이전 (옵션 C) | ⏸ |
| Q-PYYAML | DB7 | KMA parser 검토 후 결정 | ⏸ |

[erd_6layer.md](erd_6layer.md)의 추가 토론 지점 12개 (Q-UI/API/CTRL/DEV/COMM/CORE/MODEL) 는 본 plan 범위 밖. 별도 plan에서 다룸.

---

## 검증·회귀 정책

각 Phase 완료 시 자동 검증:

```bash
# 1. 테스트 회귀
docker compose run --rm croftos-test
# 467 passed (DB6 이후 감소 가능 — YAML 테스트 삭제분)

# 2. YAML 마커 카운트 감소
grep -rn "@임시-yaml" croftos/ tests/ | wc -l
# DB1: 11 → DB2: 8 → DB3: 6 → ... → DB7: 0

# 3. YAML 도태 카운트
grep -rn "DeviceRepoYaml\|yaml.safe_load" croftos/ | wc -l
# DB6 후: 0 (yaml_to_db_seed.py만 일시 잔존, DB7에서 제거)

# 4. 운영 lifespan 정상 부팅
.\croft-up.bat
# 부팅 로그에 DDL 적용 + 'application startup complete'
```

---

## 다른 plan과의 관계

본 plan 완료 후 진행할 별도 plan들:

| plan | 책임 | 의존 |
|------|------|------|
| `sensor_timeseries.md` | `sensor_value_l0~l3` 4단계 + CAGG | DB4 (테스트 인프라) |
| `models_db.md` | `greenhouse_params`, `crop_params`, `calibration_run` | DB1 (migration) |
| `problem_log.md` | `problem_log` 영속화 | DB1 |
| `auth.md` | `api_user`, `api_session`, `api_key` | deferred |
| `comm_layer.md` | `comm_adapter_config`, `comm_session_log` | Modbus 도입 시 |

본 plan은 *YAML 도태 + DB SSOT 토대*에 집중. 위 plan들은 본 plan 위에서 데이터 모델 확장.

---

## 진행 방식

1. 의사결정 6개 (Q-META-1, Q-META-2, Q-CTRL-1, Q-TEST, Q-SEED, Q-PYYAML) 확정
2. 본 문서를 정본으로 commit
3. **세션당 1개 Phase** 진행 — 시작 시 본 plan + 해당 Phase 섹션 + Q결정 로드
4. 각 Phase 완료 시:
   - 검증 4단계 실행
   - 본 문서 §Phase 섹션에 ✅ 표기
   - `db_layer_erd.md` 동기화 (스키마 변경 시)
   - `erd_6layer.md` ❌ → ✅ 갱신
5. 모든 Phase 완료 시 본 문서 archive (또는 *완료* 마킹)
