# GPU server (Vast.ai)

The rented GPU runs **all models**: three vLLM servers for chat/router, embeddings and reranker.

The local PC (frontend, API, worker, Postgres, Redis) keeps working when the GPU is off:
- Sites can still be added, crawled, extracted and chunked. New chunks wait in the embedding queue
  and are embedded automatically once the GPU is back.
- Chat shows "models offline" until then. Chat needs the GPU anyway, so moving embeddings there
  costs nothing at question time.

Why embeddings aren't local: on this PC's CPU (Ryzen 5 3500U), Qwen3-Embedding-0.6B took about 31 s
per 600-token chunk. On the GPU it takes milliseconds.

## Two profiles: test first, then scale up

`start_vllm.sh` has two presets. Only the **chat model** differs. The embedding model and reranker are
identical in both, so pages indexed during testing stay valid after you scale up.

| Profile | Command | Chat model | GPU to rent | Use for |
|---|---|---|---|---|
| **test** (default) | `bash start_vllm.sh test` | `Qwen/Qwen3-4B-Instruct-2507-FP8` (~4.5 GB), 16k context | **16–24 GB**, cheapest available (RTX 3090 / 4060 Ti 16 GB / A4000 / A5000) | Building and checking every flow: crawl, index, retrieval, citations, streaming |
| **prod** | `bash start_vllm.sh prod` | `Qwen/Qwen3-30B-A3B-Instruct-2507-FP8`, 32k context | **48 GB** (RTX A6000 / L40S / A40) | Real answer quality, once everything works |

The models are served under fixed names (`nullish-chat`, `nullish-embed`, `nullish-rerank`), so
**scaling up needs no change on the Smish PC**:
1. `bash start_vllm.sh stop`
2. Rent or switch to the 48 GB instance.
3. Run `bash start_vllm.sh prod` and reconnect the tunnel.

The status page shows the real model behind each name, so you can always see which profile is running.

Notes:
- On a 24 GB card in **test** you can raise `CHAT_MAX_LEN=32768`.
- To try a different small chat model, set `CHAT_MODEL=...` before the command.
- Check the exact model IDs and licenses on Hugging Face before the first pull.
- FP8 runs natively on RTX 40xx / L4 / L40S / H100. On older cards (3090, A-series), vLLM falls back to
  a slower weight-only FP8 path. That's fine for testing, or use the non-FP8 `Qwen/Qwen3-4B-Instruct-2507`
  on a 24 GB card.
- **Never change `EMBED_MODEL`** between runs. The health check marks the embedding server red
  ("serves X, index expects Y") if the GPU serves a different embedding model than the one the index
  was built with.

## Instance setup

- Template: the **vLLM** template (or any PyTorch / CUDA 12.x image; the script installs vLLM if it's missing).
- Disk: **≥ 60 GB** for test, **≥ 100 GB** for prod. `HF_HOME=/workspace/hf` stays on the instance
  disk, so a *stopped* (not destroyed) instance restarts without re-downloading weights.
- Add your SSH public key in the Vast account settings.
- **Do not** expose ports 8000/8010/8020. Access is only through the SSH tunnel.

## Start

1. On the instance:
   ```bash
   export VLLM_API_KEY=<same as GPU_API_KEY in Smish .env>
   bash start_vllm.sh test   # copy it over with scp, or paste it. Starts embed, then rerank, then chat
   ```
2. On this PC, in a new terminal, leave this running:
   ```powershell
   powershell -ExecutionPolicy Bypass -File gpu\tunnel.ps1 -HostIp <ip> -SshPort <port>
   ```
3. Check that the models are reachable:
   ```powershell
   curl.exe -H "Authorization: Bearer <key>" http://localhost:8001/v1/models
   curl.exe "http://localhost:8000/api/v1/health?deep=true"
   ```
   The deep check should report `embedding_dim: 1024` and rerank scores. The status page at
   http://localhost:3000 turns all three GPU rows green within ~30 s.

## Stop
`bash start_vllm.sh stop`, then stop the instance in the Vast console. A stopped instance keeps
its disk and is billed for storage only. Nothing on the local side needs to change.

## If a vLLM flag is rejected
vLLM's CLI changes between releases. If `--runner pooling` isn't recognised by the installed
version, replace it with `--task embed` (embedding) or `--task score` (reranker) in `start_vllm.sh`.
