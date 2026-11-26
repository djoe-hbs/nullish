# Smish

A self-hosted chatbot that answers only from websites you configure, always checks content against the live site before using it, and runs on open-source models.

- Design: [Smish-Plan.md](Smish-Plan.md)
- Deployment review: [docs/analysis.md](docs/analysis.md)
- GPU setup (local development): [gpu/README.md](gpu/README.md)
- **Production deployment** (written for AI agents to follow):
  - [deploy/VPS_DEPLOY.md](deploy/VPS_DEPLOY.md): the app, database and HTTPS on a VPS
  - [deploy/GPU_SERVER.md](deploy/GPU_SERVER.md): the three model servers on the GPU machine

**Login:** the first admin account comes from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env`. It's
created on the first start only; change the password afterwards from the account menu
(top right).

## Layout

| Where | What |
|---|---|
| This PC (Docker Desktop) | frontend (:3000), api (:8000), worker, Postgres + pgvector, Redis |
| Rented Vast.ai GPU | vLLM: chat/router, embeddings, reranker, reached via `gpu/tunnel.ps1` |

The GPU can be off. Crawling and indexing keep running locally, while chat and embedding wait for it.

## Run locally

```powershell
copy .env.example .env      # then fill in SECRET_KEY, ENCRYPTION_KEY, GPU_API_KEY
docker compose up -d --build
start http://localhost:3000  # status page
curl.exe http://localhost:8000/api/v1/health
```

When the GPU is up (see `gpu/README.md`):
```powershell
powershell -ExecutionPolicy Bypass -File gpu\tunnel.ps1 -HostIp <ip> -SshPort <port>
curl.exe "http://localhost:8000/api/v1/health?deep=true"
```

## Use it (http://localhost:3000)

- **Sites** tab: add a site URL. It's crawled right away: robots.txt, then the sitemap, then links
  within the site. JavaScript-only sites are rendered in a headless browser automatically.
  The page shows crawl progress, pages, errors and how much of the site is embedded.
- **Chat** tab, **Your sources** mode: answers only from your sites, with a clickable `[n]` citation
  for each claim and a Sources list that shows when each page was fetched. Use the site chips to
  limit a question to some sites. When the sites don't have the answer, it says so in its own
  words.
- **Sources → Databases:** connect a PostgreSQL or MySQL/MariaDB database and choose what Smish
  may do with it:
  - **Never edit:** read only (also enforced with read-only transactions).
  - **Ask before editing:** each change is tried out (rolled back) and shown as an approval card
    with a before/after preview; nothing changes until you click Approve.
  - **Edit automatically:** data changes run right away; schema changes still need approval.
  Ask questions in chat ("how many orders are pending?"); Smish writes the SQL, runs it, and
  cites each query as Q1, Q2 (the SQL and results are under the answer). Hide tables or columns
  Smish must never see, and add notes that explain what the data means. Every statement is
  logged (card → Query log). Details: [docs/db-connections-plan.md](docs/db-connections-plan.md).
  Sample databases for trying it out: `docker compose --profile testdbs up -d testdb-postgres
  testdb-mysql` (host `testdb-postgres` / `testdb-mysql`, database/user/password `shop`; needs
  `ALLOW_PRIVATE_DB_HOSTS=true`, already set in the dev `.env`).
- **Chat** tab, **Model only** mode: a plain chat with the model, not grounded in your sites.

Conversations are saved in this browser with their sources and site selection. Choose a
conversation to continue it, or start a new one. Use the header control to switch between
light and dark themes. Live sites checks pages and reviews the draft against its citations
before displaying the answer. Model chat streams directly without website verification.

Enabled sites are rediscovered from the root and sitemap an hour after each crawl completes.
The scheduler checks every minute. A live check that retires an old URL brings the next
crawl forward. Live checks also trigger rediscovery when at least half of four or more
previously indexed outgoing links disappear from a page. Each full crawl also checks up to 25 previously indexed URLs missing from
its discovery pass. A transient outage or absence from a sitemap alone does not delete a page.

How it works ("index to find, live to verify", the same pattern as ChatGPT / Perplexity search):
1. **Background index:** the worker crawls each site and extracts each page's main content
   (Trafilatura). Pages are chunked (~500 tokens, along headings) and versioned: changed text
   replaces old chunks. Embedding runs whenever the GPU is up.
2. **Search:** vector + keyword search (pgvector + Postgres full-text), merged by rank and
   reranked, finds the most relevant pages.
3. **Live check, on every question:** the top ~6 pages are re-fetched from the live site in
   parallel.
   - Unchanged (HTTP 304 or the same text): used as is.
   - Changed: the new text is extracted, embedded and used immediately.
   - Deleted (404): dropped.
   - Too slow or the site is down: used, but labelled "couldn't verify live".
   - JavaScript sites are re-rendered in a warm headless browser.
4. **Live discovery:** each site's sitemap (cached 5 min) and optional site-search page find
   pages the index doesn't have yet. They're fetched and used right away.
5. The chat model answers only from these verified sources. Each citation shows when its page
   was verified.

Live-check timing (tunable in `backend/app/config.py`):
- About 1–4 s on normal sites and about 8–10 s on JavaScript sites.
- A page confirmed live in the last 60 s is not fetched again (`live_reverify_after_sec`, 0 = always).
- Any page not checked within 15 s is labelled unverified (`live_total_budget_sec`).

Not built yet:
- Adding more users from the UI (one admin account for now).

## Develop

```powershell
docker compose exec api pytest       # unit + integration (uses a separate nullish_test database)
docker compose exec api ruff check .
docker compose exec api alembic revision --autogenerate -m "..."
docker compose restart worker        # the worker doesn't auto-reload code changes
cd frontend; npm run build
```

The host ports for Postgres and Redis are 55432 and 56379, because the defaults are already used on this PC.
