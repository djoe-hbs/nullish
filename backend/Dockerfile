FROM python:3.12-slim AS py
# PYTHONPATH=/app so the bind-mounted source (docker-compose) wins over the installed copy.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app
WORKDIR /app

# Headless Chromium for JavaScript-rendered sites. Needed by BOTH the worker (crawls) and the
# api (live verification at question time). Installed before the Python deps so editing
# pyproject.toml doesn't re-download the browser (slow layer stays cached).
FROM py AS browser
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install "playwright>=1.47" && playwright install --with-deps chromium

FROM browser AS app
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install ".[dev]" && rm -rf app build
COPY . .

FROM app AS api
EXPOSE 8000

FROM app AS worker
