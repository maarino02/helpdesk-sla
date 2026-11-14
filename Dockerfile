# syntax=docker/dockerfile:1

# Both stages must use the same image: the virtualenv points to its Python.
ARG PYTHON_IMAGE=python:3.13-slim-trixie

# --- Build stage: resolve and install dependencies with uv ----------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv

# Precompile .pyc files for faster startup; copy instead of hard-linking from the
# cache (which is a separate mount); use the image's Python, never download one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Only the dependency files: this layer is reused until uv.lock changes.
# --locked fails the build if uv.lock is out of date with pyproject.toml.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project

# --- Runtime stage: virtualenv and code, no build tools -------------------------
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    WEB_CONCURRENCY=2

RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=builder /app/.venv /app/.venv
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/entrypoint
COPY backend/ /app/backend/

WORKDIR /app/backend

# Static files are part of the image. Settings only need placeholder values to
# load: collectstatic opens no database or Redis connection.
RUN DJANGO_SECRET_KEY=build-only \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=postgres://build@localhost/build \
    REDIS_URL=redis://localhost:6379/0 \
    python manage.py collectstatic --noinput

# Files stay owned by root: the app user can read the code but not modify it.
USER app

EXPOSE 8000

ENTRYPOINT ["entrypoint"]
CMD ["web"]
