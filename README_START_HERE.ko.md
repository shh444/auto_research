# Paperclip + 프론트엔드 자가수정 에이전트 시작 가이드

이 폴더는 내가 만든 Paperclip 프로세스 어댑터 기반 프론트엔드 에이전트를 **Docker 기준**으로 켜기 쉽게 정리한 스타터입니다.

핵심 아이디어는 단순합니다.

- **Paperclip**는 일감, 이슈, 하트비트, 상태 업데이트를 맡습니다.
- **process adapter**는 Paperclip가 `python3 /opt/agent/paperclip_frontend_agent.py ...`를 실행하게 만듭니다.
- 에이전트는 로컬 프론트엔드 repo를 보고, 계획을 세우고, 코드를 수정하고, 브라우저로 화면을 리뷰하고, `verify` 명령이 통과할 때까지 다시 시도합니다.
- 기본값에서는 원본 repo를 직접 수정하지 않고 `final.patch`를 산출합니다. 검증 성공 후 원본에 바로 적용하려면 `apply_patch_on_success = true` 또는 `--apply-on-success`를 명시합니다.
- 유출된 디자인 프롬프트 자체를 복제하지 않고, 공개적으로 안전한 UI 검증 원칙(계층, 간격, 대비, 반응형, 접근성, 상태 표현)을 브라우저 하네스에 넣었습니다.

---

## 0. 먼저 운영 모드를 고르세요

### A. macOS / Apple Silicon
이 경우 **Paperclip와 에이전트는 Docker로**, **vLLM은 호스트(macOS)나 원격 Linux GPU 서버로** 두는 편이 가장 현실적입니다.

이유:
- Docker Desktop의 GPU 지원 문서는 현재 **Windows + WSL2**만 공식적으로 다룹니다.
- vLLM은 macOS Apple Silicon에 대해 **실험적 지원**이 있고, GPU 가속은 **vllm-metal**이 네이티브 경로입니다.

이 스타터에서는 이 모드일 때 `agent/paperclip_agent.frontend.mac.toml`을 사용하세요.

### B. Linux + NVIDIA GPU
이 경우 **Paperclip + 에이전트 + vLLM**을 모두 Docker Compose로 묶을 수 있습니다.

이 스타터에서는 이 모드일 때 `agent/paperclip_agent.frontend.linux-gpu.toml`과 `compose.vllm.linux-gpu.yml`을 같이 사용하세요.

---

## 1. 디렉터리 배치

가장 쉬운 방식은 **공식 Paperclip repo 루트**에 이 폴더의 파일들을 두는 것입니다.

예시:

```text
~/work/
  paperclip/                  # 공식 paperclip repo
    Dockerfile                # 공식 repo 파일
    docker/
    compose.paperclip-agent.yml
    compose.vllm.linux-gpu.yml
    Dockerfile.paperclip-agent
    .env
    agent/
      paperclip_frontend_agent.py
      vllm_frontend_loop.py
      vllm_coding_loop.py
      paperclip_agent.frontend.mac.toml
      paperclip_agent.frontend.linux-gpu.toml
      paperclip_process_adapter.container.json
  my-frontend-app/            # 실제 수정할 프론트 repo
```

즉:
1. 공식 Paperclip repo를 클론합니다.
2. 이 스타터 파일을 그 repo 루트에 넣습니다.
3. 실제 프론트 repo는 따로 두고 bind mount로 연결합니다.

---

## 2. 공식 Paperclip repo 받기

```bash
git clone https://github.com/paperclipai/paperclip.git
cd paperclip
```

공식 Paperclip README의 가장 빠른 시작은 현재 `npx paperclipai onboard --yes`입니다. 다만 이 스타터는 **Docker overlay** 방식이므로, 공식 repo 루트의 `Dockerfile`로 `paperclip-local` 이미지를 빌드한 뒤 이 폴더의 파일들을 공식 repo 루트에 두는 흐름을 사용합니다.

---

## 3. 환경 파일 만들기

```bash
cp .env.example .env
```

반드시 수정할 것:

- `BETTER_AUTH_SECRET`
- `FRONTEND_REPO_DIR`

예시:

```env
BETTER_AUTH_SECRET=super-long-random-secret
PAPERCLIP_PORT=3100
PAPERCLIP_PUBLIC_URL=http://localhost:3100
PAPERCLIP_DATA_DIR=./data/docker-paperclip
FRONTEND_REPO_DIR=/Users/you/work/my-frontend-app
```

Linux GPU로 vLLM까지 Docker로 띄울 거면 아래도 채우세요.

```env
HF_TOKEN=...
HF_CACHE_DIR=/absolute/path/to/.cache/huggingface
VLLM_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
VLLM_API_KEY=dev
```

---

## 4. Paperclip 기본 이미지 빌드

이 단계는 공식 수동 빌드 흐름입니다.

```bash
docker build -t paperclip-local .
```

---

## 5. Python + Playwright + 에이전트 스크립트가 들어간 확장 이미지 빌드

```bash
docker build -t paperclip-local-agent -f Dockerfile.paperclip-agent .
```

이 이미지가 하는 일:
- `paperclip-local`을 베이스로 사용
- Python / pip / git 설치
- `playwright`와 Chromium 설치
- `agent/` 폴더를 `/opt/agent`로 복사

---

## 6. vLLM 준비

### macOS / Apple Silicon
호스트에 vLLM을 직접 두세요. 가장 현실적인 선택지는 다음 둘 중 하나입니다.

1. **vllm-metal** 설치 후 호스트에서 `vllm serve` 실행
2. 원격 Linux GPU 서버에 vLLM 올리고 그 URL을 `agent/paperclip_agent.frontend.mac.toml`의 `base_url`에 넣기

호스트 macOS에서 8000 포트로 열었다면, 컨테이너에서는 `host.docker.internal:8000`으로 접근합니다.

### Linux + NVIDIA GPU
아래처럼 compose로 같이 올리면 됩니다.

```bash
docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml up -d
```

### Paperclip만 먼저 올리고 싶은 경우

```bash
docker compose -f compose.paperclip-agent.yml up -d
```

---

## 7. Paperclip 열기

브라우저에서 다음으로 접속합니다.

```text
http://localhost:3100
```

처음엔 회사와 에이전트를 직접 만들어야 합니다.

---

## 8. 회사와 에이전트 만들기

### 회사 만들기
UI에서 **New Company**를 눌러 회사를 만듭니다.

간단한 예시:
- Name: `Frontend Lab`
- Description: `Autonomous frontend improvement team`
- Goal: `Improve conversion and polish for our product UI without breaking builds`

### 에이전트 만들기
Agents 페이지에서 새 에이전트를 만듭니다.

권장 예시:
- Name: `frontend-engineer`
- Role: `engineer`
- Adapter type: `process`
- Capabilities: `Frontend implementation, browser review, iterative polish`

중요한 건 **Adapter config**입니다.

#### Linux GPU용 예시

```json
{
  "adapterType": "process",
  "adapterConfig": {
    "command": "python3 /opt/agent/paperclip_frontend_agent.py --config /opt/agent/paperclip_agent.frontend.linux-gpu.toml",
    "cwd": "/workspace/repo",
    "timeoutSec": 7200,
    "env": {
      "PYTHONUNBUFFERED": "1"
    }
  }
}
```

#### macOS용 예시
위 JSON에서 config 파일만 아래로 바꾸세요.

```text
/opt/agent/paperclip_agent.frontend.mac.toml
```

그리고 UI의 **Test Environment** 버튼으로 먼저 검증하세요.

---

## 9. TOML 설정에서 반드시 바꿔야 하는 것

가장 중요한 파일은 아래 둘입니다.

- `agent/paperclip_agent.frontend.mac.toml`
- `agent/paperclip_agent.frontend.linux-gpu.toml`

꼭 손봐야 할 항목:

### 1) `agent.apply_patch_on_success`

```toml
[agent]
apply_patch_on_success = false
```

`false`이면 에이전트가 임시 worktree에서만 작업하고 `final.patch`를 남깁니다.

`true`이면 verifier가 모두 통과한 뒤에만 원본 Git repo에 patch를 적용합니다. 밤새 자동 반영을 원하면 이 값을 켜되, 처음 며칠은 작은 이슈와 짧은 verify로 동작을 확인하는 편이 좋습니다.

### 2) `frontend.viewports`

자동 UI 리뷰가 열어볼 화면 크기입니다.

```toml
viewports = [
  { name = "desktop", width = 1440, height = 1024 },
  { name = "mobile", width = 390, height = 844 },
]
```

브라우저 하네스는 각 viewport에서 콘솔 에러, 네트워크 실패, HTTP 400 이상, 비어 있는 body, 주요 CTA 대비, 수평 overflow, 중요한 텍스트/버튼 겹침, 텍스트 잘림, 작은 터치 타깃, 접근 가능한 이름 없는 버튼/링크를 봅니다.

### 3) `commands.setup`
repo 의존성 설치 명령입니다.

예시:

```toml
setup = ["npm ci"]
```

pnpm이면:

```toml
setup = ["corepack enable && pnpm install --frozen-lockfile"]
```

### 4) `commands.verify`
성공 판정의 핵심입니다.

최소 예시:

```toml
verify = [
  "npm run lint",
  "npm run build",
]
```

Playwright 테스트가 이미 repo에 있으면 이렇게 강하게 거는 게 좋습니다.

```toml
verify = [
  "npm run lint",
  "npm run build",
  "npx playwright test --reporter=line",
]
```

### 5) `frontend.start_command`
프론트 dev 서버를 띄우는 명령입니다.

Next.js 예시:

```toml
start_command = "npm run dev -- --host 127.0.0.1 --port 3000"
```

Vite 예시:

```toml
start_command = "npm run dev -- --host 127.0.0.1 --port 3000"
```

### 6) `frontend.pages`
에이전트가 실제로 열어볼 경로입니다.

예시:

```toml
[[frontend.pages]]
path = "/"
name = "home"

[[frontend.pages]]
path = "/pricing"
name = "pricing"
```

### 7) `vllm.base_url`
- Linux Docker vLLM: `http://vllm:8000/v1`
- macOS host vLLM: `http://host.docker.internal:8000/v1`
- 원격 서버: `https://your-server.example.com/v1`

### 8) `vision`

진짜 스크린샷 기반 비전 리뷰까지 하고 싶으면 별도 VLM 엔드포인트를 켜고 `[vision] enabled = true`로 둡니다.

```toml
[vision]
enabled = true
base_url = "http://localhost:8001/v1"
api_key = "dev"
model = "Qwen/Qwen2.5-VL-7B-Instruct"
```

텍스트 코딩 모델만 써도 DOM/스타일/레이아웃 휴리스틱은 동작하지만, 화면의 미묘한 미감 문제까지 잡으려면 비전 모델이 훨씬 낫습니다.

---

## 10. Paperclip 연결 전에 단독 수동 테스트 먼저 해보기

이 단계가 매우 중요합니다.

Paperclip까지 걸기 전에, 에이전트 자체가 네 repo에서 한 번 도는지 먼저 확인하세요.

### Linux GPU 예시

```bash
docker compose exec paperclip \
  python3 /opt/agent/vllm_frontend_loop.py \
  --repo /workspace/repo \
  --config /opt/agent/paperclip_agent.frontend.linux-gpu.toml \
  --task-file /opt/agent/manual_task.example.md
```

검증 성공 시 원본 bind mount repo에 바로 적용하려면 마지막에 `--apply-on-success`를 붙입니다.

### macOS 예시

```bash
docker compose exec paperclip \
  python3 /opt/agent/vllm_frontend_loop.py \
  --repo /workspace/repo \
  --config /opt/agent/paperclip_agent.frontend.mac.toml \
  --task-file /opt/agent/manual_task.example.md
```

이 테스트가 통과하면 그다음에 Paperclip 이슈 기반 운용으로 넘어가면 됩니다.

---

## 11. 실제 운용 흐름

1. Paperclip UI에서 이슈 생성
2. 이슈에 목표, 범위, 금지사항, verify 기준 적기
3. 에이전트에게 이슈 할당 또는 invoke
4. 에이전트가 checkout -> 계획 -> 임시 worktree 수정 -> 브라우저 리뷰 -> verify -> patch/report 산출 -> 코멘트/아티팩트 업로드 수행
5. 성공이면 `done`, 실패나 막힘이면 `blocked`

이 에이전트는 모델의 자기판단을 믿지 않고, **`verify` 명령이 통과해야만 성공**으로 처리합니다.

---

## 12. 추천 이슈 템플릿

```md
# Goal
- 로그인 페이지를 더 신뢰감 있고 읽기 쉽게 개선한다.

# Non-goals
- 인증 API 변경 금지
- 백엔드 스키마 변경 금지

# Allowed files
- src/pages/login.tsx
- src/components/auth/**
- src/styles/**

# Acceptance tests
- npm run lint
- npm run build
- 로그인 페이지 콘솔 에러 0개
- 메인 CTA가 first fold에서 명확히 보임

# Stop if
- 새 환경변수 필요
- 새 패키지 의존성 필요
- API 스펙 변경 필요
```

---

## 13. 자주 막히는 지점

### A. `Test Environment`에서 실패
대부분 아래 셋입니다.
- config 파일 경로 오타
- `cwd` 경로 오타
- `FRONTEND_REPO_DIR`가 잘못되어 `/workspace/repo`에 repo가 안 보임

### B. vLLM 연결 실패
- macOS면 `host.docker.internal:8000`으로 열려 있는지 확인
- Linux면 `compose.vllm.linux-gpu.yml`을 같이 올렸는지 확인
- `api_key`가 TOML과 서버 옵션에서 같은지 확인

### C. 프론트 dev 서버가 준비되지 않음
- `start_command`가 실제 repo에서 먹는지 직접 먼저 확인
- monorepo면 repo 루트가 아니라 프론트 앱 디렉터리를 bind mount 하거나, start command를 workspace 구조에 맞게 바꾸기

### D. `npm run build`는 되는데 브라우저 리뷰가 이상함
- 실제 리뷰 대상 경로(`frontend.pages`)를 더 정확히 적기
- 로그인 필요하면 storage state 또는 테스트 계정 자동 로그인 루틴을 추가하기
- 디자인 브리프를 더 구체적으로 쓰기

---

## 14. 종료와 재시작

중지:

```bash
docker compose -f compose.paperclip-agent.yml down
```

Linux GPU 스택 전체 중지:

```bash
docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml down
```

데이터는 `PAPERCLIP_DATA_DIR` bind mount에 남습니다.

---

## 15. 내가 추천하는 첫 실행 순서

가장 덜 꼬이는 순서는 이겁니다.

1. 공식 Paperclip repo 클론
2. `.env` 작성
3. `paperclip-local` 빌드
4. `paperclip-local-agent` 빌드
5. Paperclip 컨테이너만 먼저 기동
6. 수동 테스트로 `vllm_frontend_loop.py` 1회 실행
7. 결과가 괜찮으면 UI에서 회사/에이전트 생성
8. process adapter 등록
9. 작은 이슈 하나로 시작
10. 통과하면 그 다음부터 범위를 조금씩 넓히기

작게 시작해야 덜 어긋납니다.
