# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: resuelve dependencias con uv (venv reproducible desde uv.lock)
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY app ./app
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------------------------------------------------------------------------
# Runtime: imagen final sin uv ni caché de build
# ---------------------------------------------------------------------------
FROM python:3.13-slim

RUN groupadd --system apiems && useradd --system --gid apiems --create-home apiems

WORKDIR /app

COPY --from=builder --chown=apiems:apiems /app/.venv /app/.venv
COPY --chown=apiems:apiems app ./app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER apiems

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
