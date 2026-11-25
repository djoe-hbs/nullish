# Persistent SSH tunnel from the VPS to the GPU server's three vLLM ports.
#   gpu-tunnel:8001 -> GPU 127.0.0.1:8000 (chat / router)
#   gpu-tunnel:8002 -> GPU 127.0.0.1:8010 (embeddings)
#   gpu-tunnel:8003 -> GPU 127.0.0.1:8020 (reranker)
# autossh restarts ssh whenever the connection drops (GPU rebooted, network blip, ...).
#
# The private key is mounted read-only at /keys/id_ed25519 and copied to a root-only file at
# start: ssh refuses keys with loose permissions ("bad permissions"), which bind mounts often have.

FROM alpine:3.20
RUN apk add --no-cache openssh-client autossh
ENV AUTOSSH_GATETIME=0
EXPOSE 8001 8002 8003
CMD ["sh", "-c", "\
  set -e; mkdir -p /root/.ssh; chmod 700 /root/.ssh; \
  install -m 600 /keys/id_ed25519 /root/.ssh/gpu_key; \
  exec autossh -M 0 -N \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/root/.ssh/known_hosts \
    -i /root/.ssh/gpu_key -p \"$GPU_SSH_PORT\" \
    -L 0.0.0.0:8001:127.0.0.1:8000 \
    -L 0.0.0.0:8002:127.0.0.1:8010 \
    -L 0.0.0.0:8003:127.0.0.1:8020 \
    \"$GPU_SSH_USER@$GPU_HOST\""]
