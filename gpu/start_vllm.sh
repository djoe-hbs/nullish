#!/usr/bin/env bash
# Run ON the Vast.ai instance. Starts three OpenAI-compatible vLLM servers, all bound to 127.0.0.1
# (reached from the Smish PC only through the SSH tunnel, gpu/tunnel.ps1):
#
#   :8000  chat / router   /v1/chat/completions   served as "nullish-chat"
#   :8010  embeddings      /v1/embeddings         served as "nullish-embed"
#   :8020  reranker        /v1/rerank             served as "nullish-rerank"
#
# The served names never change, so switching test <-> prod needs NO change on the Smish PC.
#
# Usage:
#   export VLLM_API_KEY=...        # same value as GPU_API_KEY in Smish .env
#   bash start_vllm.sh test        # small chat model, cheap 16-24 GB GPU (flow testing)
#   bash start_vllm.sh prod        # full-size chat model, 48 GB GPU
#   bash start_vllm.sh stop
# Any variable below can still be overridden from the environment.
set -euo pipefail

PROFILE="${1:-test}"
if [[ "$PROFILE" == "stop" ]]; then pkill -f "vllm serve" || true; exit 0; fi

case "$PROFILE" in
  test)
    # ~4.5 GB of weights. Non-thinking instruct model: fine for router JSON + grounded answers.
    : "${CHAT_MODEL:=Qwen/Qwen3-4B-Instruct-2507-FP8}"
    : "${CHAT_MAX_LEN:=16384}"
    : "${CHAT_GPU_UTIL:=0.55}"
    : "${EMBED_GPU_UTIL:=0.15}"
    : "${RERANK_GPU_UTIL:=0.15}"
    ;;
  prod)
    : "${CHAT_MODEL:=Qwen/Qwen3-30B-A3B-Instruct-2507-FP8}"
    : "${CHAT_MAX_LEN:=32768}"
    : "${CHAT_GPU_UTIL:=0.72}"
    : "${EMBED_GPU_UTIL:=0.10}"
    : "${RERANK_GPU_UTIL:=0.10}"
    ;;
  *) echo "usage: $0 [test|prod|stop]"; exit 1 ;;
esac

: "${VLLM_API_KEY:?set VLLM_API_KEY}"
# Embedding + reranker are IDENTICAL in both profiles: the index built while testing stays valid
# after scaling up. Changing EMBED_MODEL means re-embedding every page.
EMBED_MODEL="${EMBED_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
RERANK_MODEL="${RERANK_MODEL:-BAAI/bge-reranker-v2-m3}"
LOG_DIR="${LOG_DIR:-/workspace/logs}"

# Keep weights on the instance disk so restarts don't re-download.
export HF_HOME="${HF_HOME:-/workspace/hf}"
mkdir -p "$HF_HOME" "$LOG_DIR"

if ! command -v vllm >/dev/null 2>&1; then
  pip install --upgrade vllm
fi

echo "Profile: $PROFILE | chat: $CHAT_MODEL | embed: $EMBED_MODEL | rerank: $RERANK_MODEL"

wait_ready() {  # $1 port, $2 name
  echo -n "waiting for $2 on :$1 "
  for _ in $(seq 1 360); do
    if curl -sf "http://127.0.0.1:$1/health" >/dev/null; then echo " ready"; return 0; fi
    echo -n "."; sleep 5
  done
  echo " TIMEOUT - see $LOG_DIR/$2.log"; return 1
}

trap 'pkill -P $$ || true' INT TERM

# Small pooling models first: vLLM sizes its memory from what is free at start-up,
# so starting the processes one after another avoids them fighting over VRAM.
vllm serve "$EMBED_MODEL" --served-model-name nullish-embed \
  --host 127.0.0.1 --port 8010 --api-key "$VLLM_API_KEY" \
  --runner pooling --gpu-memory-utilization "$EMBED_GPU_UTIL" --max-model-len 8192 \
  > "$LOG_DIR/embed.log" 2>&1 &
wait_ready 8010 embed

vllm serve "$RERANK_MODEL" --served-model-name nullish-rerank \
  --host 127.0.0.1 --port 8020 --api-key "$VLLM_API_KEY" \
  --runner pooling --gpu-memory-utilization "$RERANK_GPU_UTIL" --max-model-len 8192 \
  > "$LOG_DIR/rerank.log" 2>&1 &
wait_ready 8020 rerank

CHAT_EXTRA=()
if [[ -n "${CHAT_QUANT:-}" ]]; then CHAT_EXTRA+=(--quantization "$CHAT_QUANT"); fi
vllm serve "$CHAT_MODEL" --served-model-name nullish-chat \
  --host 127.0.0.1 --port 8000 --api-key "$VLLM_API_KEY" \
  --max-model-len "$CHAT_MAX_LEN" --gpu-memory-utilization "$CHAT_GPU_UTIL" \
  --enable-prefix-caching "${CHAT_EXTRA[@]}" \
  > "$LOG_DIR/chat.log" 2>&1 &
wait_ready 8000 chat

echo "All three servers up ($PROFILE). Logs: $LOG_DIR/{chat,embed,rerank}.log"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv
wait
