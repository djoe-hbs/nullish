# Smish VPS: deployment guide for an AI agent

You are deploying **Smish** (web app + API + background worker + database) on a Linux VPS, with
HTTPS on the operator's domain. The AI models do **not** run here. They run on a separate GPU
server (set up with `deploy/GPU_SERVER.md`), which this VPS reaches through an SSH tunnel it opens
itself.

```
browser ──HTTPS──> web (Caddy: certificate, frontend, /api proxy)
                     └─> api ──> postgres (pgvector), redis
                         worker (crawls sites, embeds pages, scheduled re-crawls)
api / worker ──> gpu-tunnel ──SSH──> GPU server: 127.0.0.1:8000 / 8010 / 8020
```

Everything runs from `docker-compose.prod.yml` in the repository root. Only the `web` container
publishes ports (80, 443). The database, queue, API and tunnel are reachable only inside Docker's
private network.

---

## 0. Inputs: get these from the operator before starting

| Name | What it is | If missing |
|---|---|---|
| Repository | Git URL (with access) or an archive of the Smish code | Ask. |
| `DOMAIN` | e.g. `smish.example.com`. Its DNS **A record must point to this VPS's public IP** before step 7. | Ask. Without a domain, HTTPS and secure login cookies won't work. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | First admin account (created on the very first start only) | Ask. Never invent them. |
| `GPU_API_KEY` | Shared secret; must equal `VLLM_API_KEY` on the GPU server | Ask, or generate it in step 5 and give it to the operator for the GPU server. |
| `GPU_HOST`, `GPU_SSH_PORT` | Public SSH address of the GPU server (reported by its setup agent) | Ask. You can finish everything else first; `gpu_available` stays false until then. |

## 1. Hard rules

1. **Never publish ports other than 80/443.** Don't add `ports:` to postgres, redis, api, worker
   or gpu-tunnel, and don't open 5432, 6379, 8000 or 800x in the firewall.
2. **Never print, log or commit secrets:** `.env` contents, `ADMIN_PASSWORD`, `GPU_API_KEY`, the
   private key `deploy/secrets/gpu_ssh_key`. Don't paste them into reports.
3. **Keep `ALLOW_PRIVATE_URLS=false`.** Setting it to `true` lets anyone who can add a site
   make the server fetch internal addresses (an SSRF hole).
4. **Keep `COOKIE_SECURE=true`** in production.
5. **Don't edit application code to make a check pass.** If a verification fails twice, stop and
   report the failing step, the command and its output.

## 2. Server requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04 / 24.04 LTS (or Debian 12), x86-64 | same |
| CPU / RAM | 2 vCPU / 4 GB + 4 GB swap | **4 vCPU / 8 GB** (a headless browser renders JavaScript sites) |
| Disk | 40 GB | 80 GB SSD |
| Open inbound | 22 (SSH), 80, 443 (TCP), 443 (UDP, for HTTP/3) | same |
| Outbound | unrestricted (crawling websites, SSH to the GPU server, Let's Encrypt) | same |

Check:

```bash
lsb_release -ds; nproc; free -h; df -h /
```

If RAM is below 8 GB and there is no swap, add 4 GB of swap:

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 3. Install Docker and the firewall

Use Docker's official packages:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git ufw
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
docker --version && docker compose version
```

Firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw --force enable
sudo ufw status
```

Expected: Docker ≥ 24, Compose ≥ v2.20, and ufw active with 22/80/443 allowed.

## 4. Get the code

```bash
sudo mkdir -p /opt/smish && sudo chown "$USER" /opt/smish
git clone <repository URL> /opt/smish     # or unpack the archive into /opt/smish
cd /opt/smish
ls docker-compose.prod.yml deploy/Caddyfile deploy/.env.production.example
```

All three files must exist. **All following commands run in `/opt/smish`.**

## 5. Create the GPU tunnel key and the secrets

**SSH key for the tunnel.** Generate it with no passphrase: it's used unattended, and it's
restricted to port forwarding on the GPU side.

```bash
mkdir -p deploy/secrets
ssh-keygen -t ed25519 -N "" -C smish-vps -f deploy/secrets/gpu_ssh_key
chmod 600 deploy/secrets/gpu_ssh_key
cat deploy/secrets/gpu_ssh_key.pub
```

Give the **public** key (the `.pub` line printed above) to the operator for the GPU server setup
(`deploy/GPU_SERVER.md`, step 5). Never share the private key file.

**Secrets.** Generate them; never type invented values:

```bash
openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-48; echo   # SECRET_KEY
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # ENCRYPTION_KEY
openssl rand -hex 24                                          # POSTGRES_PASSWORD
openssl rand -hex 32                                          # GPU_API_KEY (only if the operator didn't give one)
```

## 6. Write `.env`

```bash
cp deploy/.env.production.example .env
chmod 600 .env
```

Edit `.env` and replace **every** `<…>` placeholder:

| Variable | Value |
|---|---|
| `SITE_ADDRESS` | `DOMAIN` (no `https://`) |
| `FRONTEND_ORIGIN` | `https://DOMAIN` exactly, no trailing slash |
| `COOKIE_SECURE` | `true` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | from the operator |
| `SECRET_KEY`, `ENCRYPTION_KEY`, `POSTGRES_PASSWORD` | generated in step 5 |
| `DATABASE_URL` | `postgresql+asyncpg://nullish:<POSTGRES_PASSWORD>@postgres:5432/nullish`, with the same password |
| `GPU_HOST`, `GPU_SSH_PORT` | from the GPU server report |
| `GPU_API_KEY` | the shared secret |
| `CRAWLER_USER_AGENT` | `SmishBot/1.0 (+https://DOMAIN)` |

Leave the model settings (`CHAT_BASE_URL=http://gpu-tunnel:8001/v1`, …) as they are.

Check that nothing is left unfilled; this must print nothing:

```bash
grep -n '<' .env
```

## 7. Build and start

DNS first: `DOMAIN` must resolve to this server, otherwise Caddy can't get a certificate.

```bash
getent hosts DOMAIN            # must print this VPS's public IP
curl -s https://api.ipify.org; echo
```

Then build and start (5–15 minutes the first time; the backend image includes a headless
browser):

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

Expected `ps` output:
- `postgres`, `redis` and `api` are `Up (healthy)`;
- `worker`, `gpu-tunnel` and `web` are `Up`;
- `migrate` is `Exited (0)`. It applies database migrations and then stops on purpose.

## 8. Verify (all must pass)

```bash
D=https://DOMAIN

# 1) HTTPS works and serves the app
curl -sI $D/ | head -1                                  # HTTP/2 200

# 2) security headers present
curl -sI $D/ | grep -iE 'strict-transport-security|x-frame-options|x-content-type-options'

# 3) API health (public endpoint, no secrets)
curl -s $D/api/v1/health | python3 -m json.tool | head -40
#    "status": "ok"; database ok (pgvector); redis ok.
#    "gpu_available": true once the GPU server is running and reachable.

# 4) everything else requires login
curl -s -o /dev/null -w "%{http_code}\n" $D/api/v1/sites            # 401

# 5) the admin can sign in (read the password from .env, don't print it)
set -a; . ./.env; set +a
curl -s -c /tmp/smish.cookies -H 'content-type: application/json' -H "origin: $FRONTEND_ORIGIN" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" $D/api/v1/auth/login
#    {"id":1,"email":"…","role":"admin"}
curl -s -b /tmp/smish.cookies -o /dev/null -w "%{http_code}\n" $D/api/v1/sites   # 200

# 6) answers stream (needs the GPU): token events arrive one by one
curl -sN -b /tmp/smish.cookies -H 'content-type: application/json' -H "origin: $FRONTEND_ORIGIN" \
  -d '{"messages":[{"role":"user","content":"Hi"}]}' $D/api/v1/ask | head -12
rm -f /tmp/smish.cookies

# 7) only 22, 80, 443 are listening publicly
sudo ss -ltnp | grep -vE '127\.0\.0\.|::1' | grep -E ':(22|80|443|5432|6379|8000|800[1-3])\b'
```

**If check 3 shows `gpu_available: false`**, check the tunnel:

```bash
docker compose -f docker-compose.prod.yml logs --tail 20 gpu-tunnel
```

| Log line | Meaning / fix |
|---|---|
| `Permission denied (publickey)` | The GPU server hasn't authorised `deploy/secrets/gpu_ssh_key.pub` yet. |
| `Connection refused` / `timed out` | Wrong `GPU_HOST` or `GPU_SSH_PORT`, or the GPU machine is stopped. |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | The GPU machine was re-created at the same address. Run `docker compose -f docker-compose.prod.yml stop gpu-tunnel && docker volume rm smish_tunnel_known_hosts && docker compose -f docker-compose.prod.yml up -d gpu-tunnel`. |
| No errors, but health shows `chat … HTTP 401` | `GPU_API_KEY` ≠ the GPU server's `VLLM_API_KEY`. |

## 9. Scheduled database backups

Daily dump, keeping 14 days:

```bash
sudo mkdir -p /var/backups/smish
sudo tee /etc/cron.daily/smish-backup >/dev/null <<'EOF'
#!/bin/sh
set -e
cd /opt/smish
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U nullish -Fc nullish > /var/backups/smish/smish-$(date +%F).dump
find /var/backups/smish -name 'nullish-*.dump' -mtime +14 -delete
EOF
sudo chmod 755 /etc/cron.daily/smish-backup
sudo /etc/cron.daily/smish-backup && ls -lh /var/backups/smish
```

To restore a dump (it replaces the current data, so only on the operator's request):

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U nullish -d nullish --clean --if-exists < /var/backups/smish/<file>.dump
```

Also keep `.env` and `deploy/secrets/` backed up somewhere safe and off the server: they aren't
in git.

## Report back to the operator

Report these; **never** include secrets:

- The URL: `https://DOMAIN`.
- The result of every check in step 8 (and `gpu_available`).
- The public key from step 5, if the GPU server still needs it.
- A reminder to sign in and change the admin password (account menu → Change password).

---

## 10. Operations

| Task | Command (in `/opt/smish`) |
|---|---|
| Status | `docker compose -f docker-compose.prod.yml ps` |
| Logs | `docker compose -f docker-compose.prod.yml logs -f --tail 100 api worker` |
| Update to new code | `git pull && docker compose -f docker-compose.prod.yml up -d --build` (migrations run automatically) |
| GPU server moved (new address) | Edit `GPU_HOST` / `GPU_SSH_PORT` in `.env`, then `docker compose -f docker-compose.prod.yml up -d gpu-tunnel` |
| Restart everything | `docker compose -f docker-compose.prod.yml restart` |
| Stop | `docker compose -f docker-compose.prod.yml down`. **Never add `-v`**: it deletes the database and certificates. |

**Admin account:**
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` are used **once**, when the database has no users. Changing
  them later has no effect.
- Change the password in the app (account menu → Change password), which signs out other
  sessions.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `web` restarts; log says `adapting config using caddyfile` | `SITE_ADDRESS` is empty or invalid in `.env`. |
| Caddy log: `obtaining certificate … NXDOMAIN` / `timeout` | DNS doesn't point here yet, or ports 80/443 are blocked (provider firewall too). Fix it, then `docker compose -f docker-compose.prod.yml restart web`. |
| Login says "Cross-site request refused." | `FRONTEND_ORIGIN` isn't exactly `https://DOMAIN` (scheme, no trailing slash, `www.` matters). Fix it, then `up -d api`. |
| Login works but you're signed out immediately | `COOKIE_SECURE=true` while visiting over plain `http://`. Use `https://`. |
| `migrate` exited non-zero | `docker compose -f docker-compose.prod.yml logs migrate`. Usually `DATABASE_URL`'s password ≠ `POSTGRES_PASSWORD`. |
| `api` unhealthy | `docker compose -f docker-compose.prod.yml logs --tail 50 api` |
| Out of memory during the build or while crawling | Add swap (step 2). |
| Sites stuck at "Waiting for GPU" | The GPU is off or the tunnel is down. See the tunnel table in step 8. Crawling continues meanwhile. |
