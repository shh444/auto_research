# croft-engine

LLM이 시간이 지나며 잊어도 되는 / 잊으면 안 되는 **확정된 아키텍처**를 보관하는 곳.

작업을 시작할 때 아래 라우팅 테이블에서 필요한 1~3개 파일만 읽고 들어간다.
이 폴더의 자산은 신중하게 늘린다. 새 파일을 추가하기 전에 합칠 수 있는 기존 파일이 있는지 먼저 본다.

---

## 라우팅 테이블 (작업 → 읽을 파일)

| 작업 | 파일 |
|---|---|
| 시스템 전체 구조 / 새 레이어·모듈 추가 / 레이어 간 통신 / 하드웨어 연결 | [architecture/croft-os.md](architecture/croft-os.md) |
| 코드 레이아웃 / 새 파일·폴더 위치 결정 | [architecture/croft-os.md](architecture/croft-os.md) §8 |
| 센서 데이터 흐름 / Hot·Cold Path / LogicalVariable / Quality / 메트릭 L0~L3 | [architecture/sensor.md](architecture/sensor.md) |
| 새 센서 추가 / 매핑 변경 / Continuous Aggregate | [architecture/sensor.md](architecture/sensor.md) |
| Setpoint 저장 위치 / DB·Cache·PLC 동기화 / 4단계 값 위계 | [architecture/setpoint.md](architecture/setpoint.md) |
| Setpoint API 구현 / 변경 전파 / UI 조회 흐름 | [architecture/setpoint.md](architecture/setpoint.md) + [architecture/croft-os.md](architecture/croft-os.md) |
| Compartment 추가 / 시간대별 schedule 추가 | [architecture/croft-os.md](architecture/croft-os.md) §7 + [architecture/setpoint.md](architecture/setpoint.md) 정리 |
| 스케줄 평가 ([1] User Intent 생성) / 환기온도 6단계 / 일출일몰 기반 시작시각 / 환경 보정·ramp | [architecture/recipe.md](architecture/recipe.md) |
| StateCache 전체 필드 (센서 + setpoint) | [architecture/sensor.md](architecture/sensor.md) §9-2 + [architecture/setpoint.md](architecture/setpoint.md) §하위 6 |
| 에러·경고·알람 정립 / Problem 발행·라이프사이클 / Quality·장치상태 → Problem 매핑 / NATS subject 규약 | [architecture/error.md](architecture/error.md) (+ [sensor.md](architecture/sensor.md) §7, [croft-os.md](architecture/croft-os.md) §2) |

---

## 이 폴더에 무엇이 들어가는가

들어감:
- **고수준 아키텍처** — 레이어 구조, 책임 경계, 통신 방식
- **도메인 계약** — 값의 위계, 상태 모델, 권한 규칙 (수동 선언)
- **안전 계약** — E-Stop, 인터록, 페일세이프 (가장 강한 코드 게이트)

들어가지 않음:
- 코드에서 자동 추출 가능한 것 (OpenAPI 스키마, DTO, 이벤트 페이로드 정의)
- 코드에서 git history로 답할 수 있는 것 (최근 변경, 누가 무엇을 바꿨는지)
- 추측 — 첫 모듈 코드 한 줄 없이 "왜 이 아키텍처인가"를 미리 쓰지 않는다
- 일시적 작업 메모, 진행 중인 결정

---

## 운영 원칙 (요약)

CROFT-ENGINE 본문(§1, §2)을 따른다. 핵심만:

- **틀린 문서는 없는 문서보다 위험하다.** 낡은 내용은 즉시 갱신하거나 삭제.
- **자동 갱신되는 자산만 신뢰한다.** 수동 자산은 사람이 따라잡지 못하므로 수를 제한한다.
- **추측으로 결정 기록을 미리 쓰지 않는다.** 결정 회고(`decisions/`)는 첫 모듈 구현 후에만 추가.
- **자산이 늘면 라우팅 비용이 커진다.** 새 파일 추가 전 합칠 수 있는 기존 파일을 먼저 찾는다.

---

## 그래프 탐색 (옵시디언 + graphify)

라우팅 테이블은 *어떤 작업에 어떤 파일을 읽을지*를 손으로 정한 인덱스다. 도메인 문서가 늘어 손으로 따라잡기 어려워지면 두 도구로 보완한다.

| 도구 | 역할 | 신뢰 |
|---|---|---|
| 옵시디언 | 명시적 마크다운 링크 시각화. `croft-engine/`을 vault로 열기만 하면 작동 | 사람이 직접 쓴 링크 = 항상 신뢰 |
| graphify | 의미 연결 자동 추출 + community 클러스터링 | EXTRACTED는 신뢰, INFERRED·AMBIGUOUS는 사람 검토 후 신뢰 |

두 vault는 성격이 다르다 — `croft-engine/` 자체는 사람이 쓴 정본 문서, `croft-engine/graphify-out/obsidian/`은 graphify가 자동 생성한 community·노드 메타데이터.

**언제 돌리는가**

- 라우팅 테이블만으로 *어떤 파일을 읽을지* 판단이 흐려질 때 (현재 3개는 사람이 충분히 본다)
- 새 도메인 문서를 `architecture/`에 추가한 직후

**명령어** (`croft-engine/`에서 실행)

```
/graphify --obsidian --wiki        # 첫 실행: vault + LLM 친화 wiki 생성
/graphify --update                  # 새 문서 추가 후: 변경분만 재추출
```

출력은 `graphify-out/`에 떨어진다. 도구 출력이라 라우팅 자산 추가 정책과 무관하다.

**검증 의무 (§5)**

- 첫 실행 / `--update` 후 `GRAPH_REPORT.md`의 INFERRED·AMBIGUOUS 엣지를 한 번 본다.
- 잘못된 추론이 라우팅 결정에 영향을 주면 출처 문서에 반례를 한 줄 적는다 — 다음 `--update`에서 graphify가 다시 학습한다.
- post-commit hook 같은 자동 트리거는 도입하지 않는다. 사람이 검증 사이클을 안 보면 INFERRED 오염이 누적된다.

---

## 용어 통일 (cross-cutting)

이 폴더의 모든 문서가 따른다:
- **compartment** — 구획 단위 (Nexus와 통일). `zone`이라는 단어는 쓰지 않는다.
- **LogicalVariable** — 측정 대상의 의미 (`TEMP_AIR`, `CO2` 등). 카탈로그는 [sensor.md](architecture/sensor.md) §5.
- **State Cache** — Core 기반층의 in-memory dict. 모든 레이어가 import.

용어 정답은 [architecture/sensor.md](architecture/sensor.md) (Nexus 호환성 기준).

---

## 현재 자산

- `architecture/croft-os.md` — 6-Layer 시스템 아키텍처
- `architecture/sensor.md` — 센서 흐름 + LogicalVariable 카탈로그 + Quality + 메트릭 L0~L3
- `architecture/setpoint.md` — Setpoint 저장 + 4단계 위계
- `architecture/recipe.md` — 스케줄 평가 ([1] User Intent 생성, 환기온도가 첫 도메인)
- `architecture/error.md` — 에러·경고·알람 단일 정립 (Problem 6차원 / 분류 매트릭스 / 기존 enum 호환 매핑 / NATS `problem.>` / active list+timeline)
