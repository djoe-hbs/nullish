# Nullish GPU server: setup guide for an AI agent

You are setting up the **model server** for Nullish on a rented GPU machine (usually a Vast.ai
instance). When you are done, three model servers run on this machine, reachable only from
localhost. The Nullish VPS reaches them through an SSH tunnel it opens itself.

| Port (127.0.0.1 only) | Served name | Model | Used for |
|---|---|---|---|
| 8000 | `nullish-chat` | `test`: Qwen/Qwen3-4B-Instruct-2507-FP8 · `prod`: Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 | answers, routing, titles |
| 8010 | `nullish-embed` | Qwen/Qwen3-Embedding-0.6B (1024-dim) | search index |
| 8020 | `nullish-rerank` | BAAI/bge-reranker-v2-m3 | ranking search results |

The VPS only ever uses the **served names**, so switching between `test` and `prod` needs no change
on the VPS.

Some providers put their own operating guide on the machine (for example
`/etc/vast-agents-guide.md`). Follow it for platform matters such as storage and networking. This
document defines **what** to run for Nullish.

---

## 0. Inputs: get these from the operator before starting

| Name | What it is | If missing |
|---|---|---|
| `VLLM_API_KEY` | Shared secret. Must be **identical** to `GPU_API_KEY` in the VPS `.env`. | Ask. Never invent one: the VPS would be locked out. |
| `PROFILE` | `test` (small chat model) or `prod` (large chat model) | Default to `test`. |
| `VPS_PUBLIC_KEY` | One line starting `ssh-ed25519 …`: the VPS tunnel key (`deploy/secrets/gpu_ssh_key.pub` on the VPS) | Ask. Without it the VPS can't connect. Everything else can be done first. |

## 1. Hard rules

1. **Never bind a model server to a public address.** They listen on `127.0.0.1` only. Don't open,
   map or expose ports 8000, 8010 or 8020 to the internet.
2. **Never print, log or echo `VLLM_API_KEY`** in your output or reports, and don't put it on
   a command line that ends up in shell history. Keep it in the file from step 4 (mode `600`).
3. Don't change the served names, ports, or the embedding model. The VPS index was built with
   `Qwen/Qwen3-Embedding-0.6B`; another embedding model silently breaks search.
4. Stop and report instead of improvising when a step's check fails twice.

## 2. Check the machine

```bash
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
nvidia-smi | grep -o "CUDA Version: [0-9.]*"
df -h /workspace 2>/dev/null || df -h ~
python3 -c "import torch;print('torch CUDA', torch.version.cuda)" 2>/dev/null || echo "no torch yet"
```

Decide:

| VRAM | Allowed profiles |
|---|---|
| < 16 GB | **Stop.** Too small; report to the operator. |
| 16–40 GB | `test` only. On 16–24 GB set `CHAT_GPU_UTIL=0.60 EMBED_GPU_UTIL=0.12 RERANK_GPU_UTIL=0.12`. |
| ≥ 48 GB | `test` or `prod` |

- **Disk:** at least 60 GB free for `test`, 100 GB for `prod` (the models are downloaded once into
  `/workspace/hf`).
- **NVIDIA Blackwell GPUs** (RTX 50xx, RTX PRO 6000, B100/B200) need CUDA ≥ 12.8 and a recent vLLM.
  On older software they fail with `no kernel image is available`. If the image's CUDA is older,
  report it: the instance must be re-created from a newer image.

## 3. Make sure the ports are free

```bash
ss -ltnp | grep -E ':(8000|8010|8020)\b' || echo "ports free"
```

If something listens there:
- if it is a vLLM server started by the provider's template (the process command contains `vllm`),
  stop it, for example with `pkill -f "vllm serve"`, and re-check;
- if it's anything else, stop and report. Don't kill unknown services.

## 4. Install the start script and the secret

```bash
mkdir -p /workspace/nullish /workspace/logs
```

- **The script:** if you have the Nullish repository, copy `gpu/start_vllm.sh` to
  `/workspace/nullish/start_vllm.sh`. Otherwise, write the script from the
  [Appendix](#appendix-start_vllmsh) to that path exactly.
- **The secret:** write the API key to a root-only file. Replace the placeholder; don't echo it
  back.

  ```bash
  umask 077
  printf 'VLLM_API_KEY=%s\n' '<VLLM_API_KEY from the operator>' > /workspace/nullish/vllm.env
  chmod 600 /workspace/nullish/vllm.env
  ```

- Optional: if model downloads are rate-limited, add `HF_TOKEN=<token>` to the same file (from the
  operator).

## 5. Allow the VPS to connect (tunnel only, no shell)

Append the VPS key **with restrictions**. It may only forward to the three model ports:
- `command="/bin/false"` makes any attempt to run a command or open a shell do nothing and exit;
- `restrict` turns off everything else (terminals, agent and X11 forwarding);
- `port-forwarding` plus the three `permitopen` entries re-allow exactly the model ports.

The tunnel opens no shell, so it works unchanged.

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
KEY='<VPS_PUBLIC_KEY: the whole ssh-ed25519 line>'
LINE="command=\"/bin/false\",restrict,port-forwarding,permitopen=\"127.0.0.1:8000\",permitopen=\"127.0.0.1:8010\",permitopen=\"127.0.0.1:8020\" $KEY"
grep -qF "$KEY" ~/.ssh/authorized_keys 2>/dev/null || echo "$LINE" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Don't remove other keys already in `authorized_keys`: the operator needs them.

## 6. Start the models

Run inside `tmux`, so the servers survive your SSH session ending:

```bash
tmux kill-session -t nullish 2>/dev/null || true
tmux new -d -s nullish "bash -c 'set -a; . /workspace/nullish/vllm.env; set +a; \
  exec bash /workspace/nullish/start_vllm.sh ${PROFILE:-test} 2>&1 | tee /workspace/logs/start.log'"
```

The script:
1. installs vLLM if it's missing;
2. downloads the models (first time only);
3. starts the embedding server, then the reranker, then chat, each one after the previous is
   ready.

The first start can take **5–30 minutes** (downloads). Follow progress with:

```bash
tail -n 5 /workspace/logs/start.log
```

Done means the log shows `All three servers up (<profile>)`. If it shows `TIMEOUT`, read the
named log (`/workspace/logs/embed.log`, `rerank.log` or `chat.log`) and go to
[Troubleshooting](#9-troubleshooting).

## 7. Verify (all must pass)

```bash
set -a; . /workspace/nullish/vllm.env; set +a
H="Authorization: Bearer $VLLM_API_KEY"

# 1) liveness: three times 200
for p in 8000 8010 8020; do curl -s -o /dev/null -w "$p %{http_code}\n" http://127.0.0.1:$p/health; done

# 2) served names and the real models behind them
for p in 8000 8010 8020; do curl -s -H "$H" http://127.0.0.1:$p/v1/models \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['data'][0];print(d['id'],'->',d['root'])"; done

# 3) chat answers
curl -s -H "$H" -H 'content-type: application/json' http://127.0.0.1:8000/v1/chat/completions \
  -d '{"model":"nullish-chat","messages":[{"role":"user","content":"Say OK"}],"max_tokens":5}' \
  | python3 -c "import sys,json;print('chat:',json.load(sys.stdin)['choices'][0]['message']['content'])"

# 4) embeddings are 1024-dimensional
curl -s -H "$H" -H 'content-type: application/json' http://127.0.0.1:8010/v1/embeddings \
  -d '{"model":"nullish-embed","input":["hello"]}' \
  | python3 -c "import sys,json;print('embed dim:',len(json.load(sys.stdin)['data'][0]['embedding']))"

# 5) reranker puts the relevant text first (expect index 1 first)
curl -s -H "$H" -H 'content-type: application/json' http://127.0.0.1:8020/v1/rerank \
  -d '{"model":"nullish-rerank","query":"return policy","documents":["Opening hours 9-5.","Returns accepted within 30 days."]}' \
  | python3 -c "import sys,json;print('rerank order:',[r['index'] for r in json.load(sys.stdin)['results']])"

# 6) not reachable from outside: every line must say 127.0.0.1
ss -ltn | grep -E ':(8000|8010|8020)\b'
```

Expected:
1. `8000 200`, `8010 200`, `8020 200`.
2. `nullish-chat -> <chat model of the profile>`, `nullish-embed -> Qwen/Qwen3-Embedding-0.6B`,
   `nullish-rerank -> BAAI/bge-reranker-v2-m3`.
3. Some short text after `chat:`.
4. `embed dim: 1024`.
5. `rerank order: [1, 0]`.
6. Only `127.0.0.1:` addresses.

A request **without** the key must be refused (401). Check this too:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/v1/models
```

## 8. Survive restarts

Stopping and starting a rented instance kills the servers. If the platform supports an on-start
script (Vast.ai: the template's **On-start Script** field), the operator should set it to:

```bash
tmux new -d -s nullish "bash -c 'set -a; . /workspace/nullish/vllm.env; set +a; \
  exec bash /workspace/nullish/start_vllm.sh test 2>&1 | tee /workspace/logs/start.log'"
```

Use `prod` instead of `test` if that's the chosen profile. You usually can't set this field from
inside the machine, so include it in your report.

## Report back to the operator

Report these, and **never** include the API key:

- GPU model and VRAM, driver CUDA version.
- Profile started, and the chat model behind `nullish-chat`.
- The result of each check in step 7.
- Whether the VPS key was added (step 5), or that it's still needed.
- **The public SSH address of this machine.** The VPS needs it as `GPU_HOST` / `GPU_SSH_PORT`
  (Vast.ai: Connect → "Direct ssh", for example `ssh -p 41234 root@1.2.3.4` means `GPU_HOST=1.2.3.4`,
  `GPU_SSH_PORT=41234`).
- The on-start script to configure (step 8), if not configured yet.

## 9. Troubleshooting

| Symptom (in the named log) | Fix |
|---|---|
| `unrecognized arguments: --runner` | Older vLLM. In the script, replace `--runner pooling` with `--task embed` (embedding line) and `--task score` (reranker line), then restart. |
| `no kernel image is available for execution on the device` | CUDA/vLLM too old for this GPU (typical on Blackwell). Needs an image with CUDA ≥ 12.8: report. |
| `CUDA out of memory` / `No available memory for the cache blocks` | Lower `CHAT_GPU_UTIL` by 0.05 or `CHAT_MAX_LEN` (e.g. 8192), keeping the three utilisations' sum < 0.9. Start the profile with those values set, e.g. `CHAT_MAX_LEN=8192 bash … start_vllm.sh test`. |
| `Address already in use` | Step 3. |
| Download errors / HTTP 429 from huggingface.co | Wait and retry; or add `HF_TOKEN` (step 4). |
| `Repository Not Found` for the chat model | The model id changed upstream. Report it; don't substitute another model on your own. |
| The VPS can't connect (it sees `Permission denied (publickey)`) | Step 5 wasn't done, or the key line was truncated. Check `grep nullish ~/.ssh/authorized_keys`. |

**Operations:**

| Task | Command |
|---|---|
| Stop | `bash /workspace/nullish/start_vllm.sh stop; tmux kill-session -t nullish` |
| Switch profile | Stop, then step 6 with the other profile. Nothing changes on the VPS. |
| Logs | `/workspace/logs/{start,chat,embed,rerank}.log` |

---

## Appendix: start_vllm.sh

Authoritative copy: `gpu/start_vllm.sh` in the Nullish repository. Use this only if you don't
have the repository.

```bash
#!/usr/bin/env bash
# Run ON the GPU machine. Starts three OpenAI-compatible vLLM servers, all bound to 127.0.0.1:
#   :8000  chat / router   /v1/chat/completions   served as "nullish-chat"
#   :8010  embeddings      /v1/embeddings         served as "nullish-embed"
#   :8020  reranker        /v1/rerank             served as "nullish-rerank"
# Usage:  VLLM_API_KEY=... bash start_vllm.sh test|prod|stop
set -euo pipefail

PROFILE="${1:-test}"
if [[ "$PROFILE" == "stop" ]]; then pkill -f "vllm serve" || true; exit 0; fi

case "$PROFILE" in
  test)
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
EMBED_MODEL="${EMBED_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
RERANK_MODEL="${RERANK_MODEL:-BAAI/bge-reranker-v2-m3}"
LOG_DIR="${LOG_DIR:-/workspace/logs}"
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
