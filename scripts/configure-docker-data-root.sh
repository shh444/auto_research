#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-/mnt/ssd2tb_20251211/docker-data}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash scripts/configure-docker-data-root.sh $TARGET" >&2
  exit 1
fi

mkdir -p "$TARGET"

if [ -f /etc/docker/daemon.json ]; then
  cp /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%Y%m%d-%H%M%S)"
fi

python3 - "$TARGET" <<'PY'
import json
import pathlib
import sys

target = sys.argv[1]
path = pathlib.Path("/etc/docker/daemon.json")
data = {}
if path.exists() and path.read_text().strip():
    data = json.loads(path.read_text())
data["data-root"] = target
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY

systemctl restart docker

echo "Docker data-root is now:"
docker info --format '{{.DockerRootDir}}'
echo ""
echo "If this prints $TARGET, future images, layers, and BuildKit cache will use the SSD path."
