# 이 repo 하나에서 시작하기

이제 `auto_research`가 루트입니다. 공식 Paperclip repo 루트로 이동하거나 파일을 복사할 필요가 없습니다.

역할은 이렇게 나눕니다.

- `auto_research`: 네 연구/운영 control repo. 에이전트 코드, Docker compose, 설정, 실험 문서가 들어갑니다.
- `.external/paperclip`: bootstrap 스크립트가 자동으로 clone하는 Paperclip 소스. git에는 올리지 않습니다.
- `FRONTEND_REPO_DIR`: 에이전트가 실제로 수정할 앱 repo. 예를 들어 `C:\croft\programs\my_frontend_app`.
- `examples/demo-frontend`: 실제 앱 repo가 아직 없을 때 쓰는 테스트용 프론트엔드 앱.

## 1. 처음 한 번만 bootstrap

Windows PowerShell:

```powershell
cd C:\croft\programs\auto_research
.\scripts\bootstrap.ps1
```

macOS/Linux/WSL:

```bash
cd ~/croft/programs/auto_research
bash scripts/bootstrap.sh
```

이 스크립트가 하는 일:

1. `.env`가 없으면 `.env.example`에서 만듭니다.
2. 기본 `FRONTEND_REPO_DIR`를 `examples/demo-frontend`로 잡습니다.
3. `.external/paperclip`에 공식 Paperclip을 clone합니다.
4. `paperclip-local` Docker 이미지를 빌드합니다.
5. 이 repo의 `agent/`가 들어간 `paperclip-local-agent` 이미지를 빌드합니다.

## 2. `.env` 수정

처음에는 bootstrap이 `examples/demo-frontend`를 자동으로 넣어둡니다. 진짜 앱에 붙일 때만 `FRONTEND_REPO_DIR`를 실제 수정할 프론트엔드 repo 절대 경로로 바꿉니다.

```env
FRONTEND_REPO_DIR=C:\croft\programs\my_frontend_app
```

Linux/WSL이면 예를 들어:

```env
FRONTEND_REPO_DIR=/home/you/work/my_frontend_app
```

## 3. Paperclip만 먼저 실행

```powershell
docker compose -f compose.paperclip-agent.yml up -d
```

브라우저:

```text
http://localhost:3100
```

## 4. vLLM까지 같이 실행할 때

Linux + NVIDIA GPU 환경이면:

```powershell
docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml up -d
```

macOS나 Windows에서 GPU Docker가 애매하면 vLLM은 별도 Linux GPU 서버나 호스트에서 띄우고, TOML의 `vllm.base_url`만 바꿉니다.

## 5. Paperclip 붙이기 전에 단독 테스트

먼저 이걸 통과시키는 게 중요합니다.

```powershell
docker compose exec paperclip python3 /opt/agent/vllm_frontend_loop.py `
  --repo /workspace/repo `
  --config /opt/agent/paperclip_agent.frontend.linux-gpu.toml `
  --task-file /opt/agent/manual_task.example.md
```

검증 성공 후 원본 repo에 바로 patch 적용까지 하려면:

```powershell
docker compose exec paperclip python3 /opt/agent/vllm_frontend_loop.py `
  --repo /workspace/repo `
  --config /opt/agent/paperclip_agent.frontend.linux-gpu.toml `
  --task-file /opt/agent/manual_task.example.md `
  --apply-on-success
```

기본값은 원본 repo를 건드리지 않고 `final.patch`만 남깁니다.

## 6. 연구 순서

처음부터 완전 자동 overnight로 가지 말고 이렇게 키우세요.

1. 작은 프론트 task 하나로 단독 루프 실행
2. `final_report.md`, `final.patch`, 스크린샷 artifact 확인
3. `commands.verify`를 repo에 맞게 정교화
4. `frontend.pages`를 실제 주요 화면으로 수정
5. Paperclip issue/heartbeat 연결
6. 며칠간 `apply_patch_on_success = false`로 관찰
7. 안정화되면 작은 범위 task에 한해 `apply_patch_on_success = true`

핵심은 모델 성능보다 검증 구조입니다. verifier가 약하면 좋은 모델도 엉뚱하게 갑니다.
