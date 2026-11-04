#!/bin/bash
# (Re)create the two vLLM containers, one model per GPU, both served at the same time (2026-10-06).
# Identical settings for both models: vLLM v0.29.0, max length 8192, max 64 sequences, FP8 weights,
# offline model cache. Each container sees only its own GPU, chosen by the GPU's PCI address in the
# control VM (stable across reboots, unlike the index).
#   layout A: TelecomGPT on the GPU on the control VM's socket, Qwen3.5 on the other one
#   layout B: the reverse (GPU assignment counterbalanced across test days, docs/decisions.md)
# Usage: harness/llm_containers.sh A|B     (then harness/llm_serve.sh both)
set -euo pipefail
LAYOUT=${1:?layout A or B}
NEAR=00000000:08:00.0   # GPU on the same socket as the control VM (docs/cluster.md)
FAR=00000000:07:00.0    # GPU on the workers' socket, used only by the model it serves
gpu_index() { nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader | awk -F', ' -v b="$1" '$2==b {print $1}'; }
case $LAYOUT in
  A) TGPT=$NEAR; QWEN=$FAR ;;
  B) TGPT=$FAR; QWEN=$NEAR ;;
  *) echo "layout must be A or B" >&2; exit 1 ;;
esac
DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
create() {  # name, port, PCI address, model
  local idx; idx=$(gpu_index "$3")
  [ -n "$idx" ] || { echo "no GPU at $3" >&2; exit 1; }
  docker rm -f "$1" >/dev/null 2>&1 || true
  docker create --name "$1" -p "127.0.0.1:$2:8000" --ipc host --gpus "\"device=$idx\"" \
    -v "$HOME/hf-cache:/root/.cache/huggingface" \
    -e NVIDIA_DISABLE_REQUIRE=1 -e HF_HUB_OFFLINE=1 -e VLLM_ENABLE_CUDA_COMPATIBILITY=0 \
    -e "LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libcuda.so.$DRV" \
    vllm/vllm-openai:v0.29.0-cu129 \
    --model "$4" --max-model-len 8192 --language-model-only --max-num-seqs 64 \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 >/dev/null
  echo "$1: GPU $idx ($3), port $2"
}
create telecomgpt-r1 8001 "$TGPT" ukkathva/TelecomGPT-R1-27B-FP8-Dynamic
create qwen35-27b 8002 "$QWEN" Qwen/Qwen3.5-27B-FP8
mkdir -p "$(dirname "$0")/../runs"
echo "$LAYOUT $(date -u +%FT%TZ)" > "$(dirname "$0")/../runs/llm_layout.txt"
