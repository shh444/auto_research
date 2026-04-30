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
sudo apt-get install -y git curl ca-certificates docker.io docker-compose-plugin docker-buildx
sudo usermod -aG docker "$USER"
newgrp docker
```

배포판에 `docker-buildx` 패키지가 없으면 Docker 공식 apt repo의 `docker-buildx-plugin`을 설치하거나, 최소한 BuildKit이 켜져 있어야 합니다. Paperclip Dockerfile은 `COPY --parents`를 쓰기 때문에 구형 legacy builder에서는 `Unknown flag: parents`로 실패합니다.

NVIDIA GPU를 Docker에서 쓰려면 NVIDIA driver와 NVIDIA Container Toolkit이 필요합니다.

확인:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

두 명령이 모두 보여야 vLLM GPU 컨테이너가 정상적으로 뜹니다.

## 2. Docker 저장소를 SSD로 옮기기

repo가 SSD에 있어도 Docker 기본 저장소가 `/var/lib/docker`이면 빌드 중 root disk가 찹니다. 먼저 Docker data-root를 SSD로 바꾸세요.

현재 Docker 저장소 확인:

```bash
docker info --format '{{.DockerRootDir}}'
```

이 값이 `/var/lib/docker`이고 root disk 용량이 부족하다면, 이 repo를 clone한 뒤 아래 스크립트를 실행합니다.

권장 경로:

```text
/mnt/ssd2tb_20251211/docker-data
```

## 3. repo clone

```bash
git clone https://github.com/shh444/auto_research.git
cd /mnt/ssd2tb_20251211/Project/henry_workspace/auto_research
```

이미 clone했다면 현재 위치에서 계속하면 됩니다.

```bash
pwd
# /mnt/ssd2tb_20251211/Project/henry_workspace/auto_research
```

Docker data-root 설정:

```bash
sudo bash scripts/configure-docker-data-root.sh /mnt/ssd2tb_20251211/docker-data
docker info --format '{{.DockerRootDir}}'
```

출력이 `/mnt/ssd2tb_20251211/docker-data`여야 합니다.

## 4. bootstrap

```bash
bash scripts/bootstrap.sh
```

만약 여기서 `Unknown flag: parents`가 나오면 Docker가 BuildKit/Buildx로 빌드하지 못한 것입니다. 우선 아래를 확인하세요.

```bash
docker buildx version
DOCKER_BUILDKIT=1 docker buildx build --load -t paperclip-local -f .external/paperclip/Dockerfile .external/paperclip
```

`docker buildx` 자체가 없으면:

```bash
sudo apt-get install -y docker-buildx-plugin
```

이 스크립트가 하는 일:

1. `.env` 생성
2. 기본 `FRONTEND_REPO_DIR`를 `examples/demo-frontend`로 설정
3. `PAPERCLIP_DATA_DIR`를 이 SSD workspace의 `.paperclip-data`로 설정
4. `HF_CACHE_DIR`를 이 SSD workspace의 `.hf-cache`로 설정
5. `.external/paperclip`에 공식 Paperclip clone
6. `paperclip-local` Docker image build
7. `paperclip-local-agent` Docker image build

## 5. `.env` 확인

처음에는 데모 앱으로 되어 있을 겁니다.

```bash
cat .env
```

중요한 값:

```env
FRONTEND_REPO_DIR=/.../auto_research/examples/demo-frontend
PAPERCLIP_DATA_DIR=/mnt/ssd2tb_20251211/Project/henry_workspace/auto_research/.paperclip-data
HF_TOKEN=...
HF_CACHE_DIR=/mnt/ssd2tb_20251211/Project/henry_workspace/auto_research/.hf-cache
VLLM_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
VLLM_API_KEY=dev
```

Hugging Face 모델이 공개 모델이면 `HF_TOKEN` 없이도 되는 경우가 많습니다. gated/private 모델을 쓰거나 다운로드 제한을 피하고 싶을 때만 넣으면 됩니다.

토큰이 필요하면 Hugging Face에서 `read` 권한 토큰을 만듭니다.

1. https://huggingface.co 에 로그인
2. https://huggingface.co/settings/tokens 열기
3. `New token` 클릭
4. 권한은 `read` 선택
5. 생성된 `hf_...` 값을 `.env`의 `HF_TOKEN=` 뒤에 붙여넣기

절대 `write` 권한 토큰을 쓸 필요가 없습니다.

A5000 2장 + Qwen3-Coder FP8 기본값으로 `.env`를 맞추려면:

```bash
bash scripts/configure-a5000-vllm-env.sh
```

토큰을 묻는 프롬프트가 나오면 `hf_...` 값을 붙여넣습니다. 이 값은 `.env`에만 저장되고 git에는 올라가지 않습니다.

## 6. Paperclip만 먼저 실행

```bash
docker compose -f compose.paperclip-agent.yml up -d
```

로그에서 `/workspace/repo/server/node_modules/...`를 찾는 에러가 나오면 Compose가 Paperclip 서버를 프론트 repo 안에서 띄운 것입니다. `compose.paperclip-agent.yml`에 `working_dir: /workspace/repo`가 있으면 삭제하고 컨테이너를 재생성하세요.

```bash
docker compose -f compose.paperclip-agent.yml down
docker compose -f compose.paperclip-agent.yml up -d --force-recreate
```

접속:

```text
http://SERVER_IP:3100
```

서버 방화벽이나 클라우드 보안그룹에서 TCP 3100을 열어야 외부에서 접속됩니다.

## 7. vLLM까지 같이 실행

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

## 8. Paperclip 전에 단독 루프 테스트

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

## 9. 실제 앱으로 바꾸기

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

## 10. 운영 권장값

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
