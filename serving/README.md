# Model serving

Both models run on the cluster control node at the same time, each on its own GPU (`harness/llm_containers.sh`). The commands below are the single-model launch used for the checks; the study uses the same flags.

## Host constraints

- GPUs: two NVIDIA L40S, 46 GB each. Driver 555.42.06 (CUDA 12.5 max). The driver cannot be upgraded without sudo and a reboot that would disturb microk8s.
- Image: `vllm/vllm-openai:v0.29.0-cu129`. `latest` needs CUDA 13. `v0.30.0-cu129` ships a cu130 torch build.
- The image's CUDA compat library fails on this driver (CUDA error 803). `LD_PRELOAD` of the host `libcuda.so.555.42.06` fixes it.
- Models live in a Hugging Face cache on the host, referred to as `$HF_CACHE` below (mounted into the container, `HF_HUB_OFFLINE=1`).

## Models

| Container | Model | Port | Size | Notes |
|---|---|---|---|---|
| `telecomgpt-r1` | `ukkathva/TelecomGPT-R1-27B-FP8-Dynamic` | 8001 | 28 GB | FP8 build of `KU-DFI/TelecomGPT-R1` (base Qwen3.5-27B). Per-channel FP8 weights, per-token dynamic activations. |
| `qwen35-27b` | `Qwen/Qwen3.5-27B-FP8` | 8002 | 29 GB | Official Qwen FP8. 128x128 block weights, dynamic activations. |

All three share one chat template. They therefore share the tool-call parser (`qwen3_coder`) and the reasoning parser (`qwen3`).

## Launch command (qwen35-27b, verified 2026-09-24)

```bash
docker run -d --name qwen35-27b --gpus all --ipc host \
  -p 127.0.0.1:8002:8000 \
  -v "$HF_CACHE":/root/.cache/huggingface \
  -e NVIDIA_DISABLE_REQUIRE=1 \
  -e HF_HUB_OFFLINE=1 \
  -e VLLM_ENABLE_CUDA_COMPATIBILITY=0 \
  -e LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libcuda.so.555.42.06 \
  vllm/vllm-openai:v0.29.0-cu129 \
  --model Qwen/Qwen3.5-27B-FP8 --max-model-len 8192 --language-model-only --max-num-seqs 64 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3
```

Checks passed: plain chat; a tool call with `tool_choice: auto`; thinking on with a JSON schema (reasoning in its own field, answer schema-valid); port bound to 127.0.0.1 only.

## Launch command (telecomgpt-r1, verified 2026-09-24)

Identical flags. Only the model and the host port differ.

```bash
docker run -d --name telecomgpt-r1 --gpus all --ipc host \
  -p 127.0.0.1:8001:8000 \
  -v "$HF_CACHE":/root/.cache/huggingface \
  -e NVIDIA_DISABLE_REQUIRE=1 \
  -e HF_HUB_OFFLINE=1 \
  -e VLLM_ENABLE_CUDA_COMPATIBILITY=0 \
  -e LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libcuda.so.555.42.06 \
  vllm/vllm-openai:v0.29.0-cu129 \
  --model ukkathva/TelecomGPT-R1-27B-FP8-Dynamic --max-model-len 8192 --language-model-only --max-num-seqs 64 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3
```

Same checks passed. Every model must be served with identical flags.

## Thinking mode

Both Qwen3.5-family models think by default. Control it per request with `"chat_template_kwargs": {"enable_thinking": false}`. Use the same setting for every model in a cell.
