# auto_research

로컬/자가호스팅 LLM으로 코딩 에이전트를 밤새 돌리기 위한 스타터입니다.

구성은 세 층입니다.

- `agent/vllm_coding_loop.py`: repo를 읽고 계획, 파일 수정, 명령 실행, 검증을 반복하는 기본 루프
- `agent/vllm_frontend_loop.py`: Playwright로 실제 화면을 열고 콘솔 에러, 네트워크 실패, 스크린샷, 모바일/데스크톱 레이아웃 문제를 관찰하는 프론트엔드 루프
- `agent/paperclip_frontend_agent.py`: Paperclip process adapter로 붙여 이슈, 하트비트, 산출물 업로드를 연결하는 브리지

네가 `git clone https://github.com/shh444/auto_research.git`로 받은 바로 그 폴더가 루트입니다. 공식 Paperclip repo 안으로 파일을 옮길 필요가 없습니다.

처음 시작은 [docs/START_FROM_THIS_REPO.ko.md](docs/START_FROM_THIS_REPO.ko.md)를 먼저 보세요. 더 긴 배경 설명은 [README_START_HERE.ko.md](README_START_HERE.ko.md)에 있습니다.

## 빠른 판단

밤새 자동 코딩을 안정적으로 하려면 “모델이 알아서 잘하겠지”가 아니라 아래 구조가 중요합니다.

1. 목표를 이슈/태스크에 명확히 둡니다.
2. 에이전트는 임시 git worktree에서만 수정합니다.
3. `npm run lint`, `npm run build`, `pytest`, Playwright 같은 외부 검증이 통과해야 성공입니다.
4. 성공하면 `final.patch`를 남깁니다.
5. 원본 repo에 바로 반영하고 싶을 때만 `apply_patch_on_success = true` 또는 `--apply-on-success`를 씁니다.

기본값은 안전하게 원본 repo를 직접 바꾸지 않습니다. 검증된 변경을 바로 적용하고 싶으면 TOML의 `[agent]` 섹션에서 켜세요.

```toml
[agent]
apply_patch_on_success = true
```

## 단독 실행

Paperclip 없이 에이전트만 먼저 시험할 수 있습니다.

```bash
python agent/vllm_frontend_loop.py \
  --repo /path/to/frontend-repo \
  --config agent/paperclip_agent.frontend.linux-gpu.toml \
  --task-file agent/manual_task.example.md
```

## Bootstrap

Windows PowerShell:

```powershell
.\scripts\bootstrap.ps1
```

macOS/Linux/WSL:

```bash
bash scripts/bootstrap.sh
```

이 스크립트는 `.external/paperclip`에 공식 Paperclip을 clone하고, `paperclip-local`과 `paperclip-local-agent` Docker 이미지를 빌드합니다.

검증 성공 시 원본 repo에 패치를 적용하려면:

```bash
python agent/vllm_frontend_loop.py \
  --repo /path/to/frontend-repo \
  --config agent/paperclip_agent.frontend.linux-gpu.toml \
  --task-file agent/manual_task.example.md \
  --apply-on-success
```
