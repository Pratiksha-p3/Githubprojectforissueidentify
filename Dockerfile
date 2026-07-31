# Stage 14: containerizes what has, until now, only run from the venv
# directly (see docker-compose.yml's note on this). One image, four
# possible processes (webhook API, worker, dashboard, fix-actions API) --
# which one a given container runs is decided by CMD/the compose service
# definition, not by building four separate images for what is the same
# codebase and dependency set.
FROM python:3.11-slim AS base

WORKDIR /app

# System deps for psycopg (libpq) -- everything else is pure Python or
# ships its own wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Runs as a non-root user -- least-privilege default for a container that
# has no reason to run as root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

# No default CMD: docker-compose.yml's app services each set their own
# (uvicorn for the APIs, celery for the worker) -- there's no single
# sensible default for an image that serves four different roles.
