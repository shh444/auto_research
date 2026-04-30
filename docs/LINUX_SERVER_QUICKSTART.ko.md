# Linux 서버 빠른 시작

전제:

- 서버 OS: Linux
- NVIDIA GPU가 있다면 vLLM까지 Docker Compose로 같이 실행
- 이 repo(`auto_research`)가 control repo
- Paperclip 소스는 `.external/paperclip`에 자동 clone
- 실제 수정 대상 앱은 일단 `examples/demo-frontend`로 시작

## 1. 서버 준비

Ubuntu 계열 예시:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"
newgrp docker
```

NVIDIA GPU를 Docker에서 쓰려면 NVIDIA driver와 NVIDIA Container Toolkit이 필요합니다.

확인:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

두 명령이 모두 보여야 vLLM GPU 컨테이너가 정상적으로 뜹니다.

## 2. repo clone

```bash
git clone https://github.com/shh444/auto_research.git
cd auto_research
```

## 3. bootstrap

```bash
bash scripts/bootstrap.sh
```

이 스크립트가 하는 일:

1. `.env` 생성
2. 기본 `FRONTEND_REPO_DIR`를 `examples/demo-frontend`로 설정
3. `.external/paperclip`에 공식 Paperclip clone
4. `paperclip-local` Docker image build
5. `paperclip-local-agent` Docker image build

## 4. `.env` 확인

처음에는 데모 앱으로 되어 있을 겁니다.

```bash
cat .env
```

중요한 값:

```env
FRONTEND_REPO_DIR=/.../auto_research/examples/demo-frontend
HF_TOKEN=...
HF_CACHE_DIR=/.../.cache/huggingface
VLLM_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
VLLM_API_KEY=dev
```

Hugging Face 모델이 공개 모델이면 `HF_TOKEN` 없이도 되는 경우가 있지만, gated 모델이나 rate limit 회피를 위해 넣는 편이 낫습니다.

## 5. Paperclip만 먼저 실행

```bash
docker compose -f compose.paperclip-agent.yml up -d
```

접속:

```text
http://SERVER_IP:3100
```

서버 방화벽이나 클라우드 보안그룹에서 TCP 3100을 열어야 외부에서 접속됩니다.

## 6. vLLM까지 같이 실행

GPU 준비가 끝났다면:

```bash
docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml up -d
```

로그 확인:

```bash
docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml logs -f vllm
```

vLLM API 확인:

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer dev"
```

## 7. Paperclip 전에 단독 루프 테스트

```bash
docker compose exec paperclip python3 /opt/agent/vllm_frontend_loop.py \
  --repo /workspace/repo \
  --config /opt/agent/paperclip_agent.frontend.linux-gpu.toml \
  --task-file /opt/agent/manual_task.example.md
```

성공해도 기본값은 원본 repo를 바꾸지 않고 patch/report만 남깁니다.

검증 성공 후 원본 repo에 바로 적용하려면:

```bash
docker compose exec paperclip python3 /opt/agent/vllm_frontend_loop.py \
  --repo /workspace/repo \
  --config /opt/agent/paperclip_agent.frontend.linux-gpu.toml \
  --task-file /opt/agent/manual_task.example.md \
  --apply-on-success
```

## 8. 실제 앱으로 바꾸기

실제 프론트 repo를 서버에 clone합니다.

```bash
cd ..
git clone https://github.com/YOUR_ORG/YOUR_FRONTEND.git my-frontend
cd auto_research
```

`.env` 수정:

```env
FRONTEND_REPO_DIR=/home/you/my-frontend
```

그리고 TOML에서 repo에 맞게 바꿉니다.

```toml
[commands]
setup = ["npm ci"]
verify = [
  "npm run lint",
  "npm run build",
]

[frontend]
start_command = "npm run dev -- --host 127.0.0.1 --port 3000"
default_paths = ["/", "/login", "/dashboard"]
```

컨테이너 재시작:

```bash
docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml up -d --force-recreate paperclip
```

## 9. 운영 권장값

처음 며칠:

```toml
[agent]
apply_patch_on_success = false
max_cycles = 3
```

안정화 후:

```toml
[agent]
apply_patch_on_success = true
max_cycles = 6
```

큰 변경은 자동 적용하지 말고 patch/report 리뷰 후 적용하는 편이 좋습니다.
