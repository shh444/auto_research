#!/usr/bin/env bash
set -euo pipefail

PAPERCLIP_REPO="${PAPERCLIP_REPO:-https://github.com/paperclipai/paperclip.git}"
PAPERCLIP_REF="${PAPERCLIP_REF:-master}"
PAPERCLIP_DIR="${PAPERCLIP_DIR:-.external/paperclip}"

cd "$(dirname "$0")/.."

command -v git >/dev/null || { echo "Required command not found: git" >&2; exit 1; }
command -v docker >/dev/null || { echo "Required command not found: docker" >&2; exit 1; }
command -v python3 >/dev/null || { echo "Required command not found: python3" >&2; exit 1; }

if [ ! -f .env ]; then
  cp .env.example .env
  demo_frontend="$(pwd)/examples/demo-frontend"
  if command -v openssl >/dev/null; then
    secret="$(openssl rand -base64 48)"
    sed -i.bak "s#BETTER_AUTH_SECRET=replace-this-with-a-long-random-string#BETTER_AUTH_SECRET=$secret#" .env
    rm -f .env.bak
  fi
  python3 - <<PY
from pathlib import Path
path = Path(".env")
text = path.read_text()
text = text.replace("FRONTEND_REPO_DIR=/absolute/path/to/your-frontend-repo", "FRONTEND_REPO_DIR=$demo_frontend")
path.write_text(text)
PY
  echo "Created .env from .env.example and pointed FRONTEND_REPO_DIR at examples/demo-frontend."
fi

if [ ! -d "$PAPERCLIP_DIR" ]; then
  mkdir -p "$(dirname "$PAPERCLIP_DIR")"
  git clone "$PAPERCLIP_REPO" "$PAPERCLIP_DIR"
fi

git -C "$PAPERCLIP_DIR" fetch --tags --prune
git -C "$PAPERCLIP_DIR" checkout "$PAPERCLIP_REF"

echo "Building paperclip-local from $PAPERCLIP_DIR ..."
docker build -t paperclip-local "$PAPERCLIP_DIR"

echo "Building paperclip-local-agent from this auto_research repo ..."
docker build -t paperclip-local-agent -f Dockerfile.paperclip-agent .

cat <<'MSG'

Bootstrap complete.
Next:
  1. Optional: edit .env and set FRONTEND_REPO_DIR to a real app repo.
  2. Start Paperclip:
     docker compose -f compose.paperclip-agent.yml up -d
  3. Linux NVIDIA GPU stack:
     docker compose -f compose.paperclip-agent.yml -f compose.vllm.linux-gpu.yml up -d
MSG
