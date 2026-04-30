#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
fi

HF_TOKEN_VALUE="${HF_TOKEN:-}"
if [ -z "$HF_TOKEN_VALUE" ]; then
  printf "Paste Hugging Face read token, or press Enter to leave blank: "
  IFS= read -r HF_TOKEN_VALUE
fi

HF_TOKEN_VALUE="$HF_TOKEN_VALUE" python3 - <<'PY'
import os
from pathlib import Path

root = Path.cwd()
env = Path(".env")
text = env.read_text() if env.exists() else ""
lines = text.splitlines()

values = {
    "HF_TOKEN": os.environ.get("HF_TOKEN_VALUE", ""),
    "HF_CACHE_DIR": str(root / ".hf-cache"),
    "VLLM_MODEL": "Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8",
    "VLLM_API_KEY": "dev",
    "VLLM_TENSOR_PARALLEL_SIZE": "2",
    "VLLM_GPU_MEMORY_UTILIZATION": "0.90",
    "VLLM_MAX_MODEL_LEN": "32768",
    "VLLM_MAX_NUM_SEQS": "1",
    "VLLM_TOOL_CALL_PARSER": "qwen3_coder",
}

seen = set()
for index, line in enumerate(lines):
    if "=" not in line or line.lstrip().startswith("#"):
        continue
    key = line.split("=", 1)[0]
    if key in values:
        lines[index] = f"{key}={values[key]}"
        seen.add(key)

for key, value in values.items():
    if key not in seen:
        lines.append(f"{key}={value}")

env.write_text("\n".join(lines).rstrip() + "\n")
(root / ".hf-cache").mkdir(exist_ok=True)
print("Configured .env for 2x NVIDIA RTX A5000 + Qwen3-Coder FP8.")
PY
