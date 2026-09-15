# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# uv: instalación determinista a partir del lockfile.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

COPY app ./app
COPY db ./db
COPY alembic.ini ./

# No corre como root: si algo se escapa, que no sea con todos los permisos.
RUN useradd --create-home --uid 10001 dsd && chown -R dsd /srv
USER dsd

EXPOSE 8000

# Los workers de uvicorn se dimensionan en compose: con 8-10 dispositivos,
# 2-4 bastan de sobra en el mini PC.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
