# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# uv, pinneado. `:latest` traería una versión distinta en cada build, y lo que
# esta imagen tiene que ser es REPRODUCIBLE: el instalador también es una
# dependencia.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

WORKDIR /srv

# ---------------------------------------------------------------------------
# Las dependencias salen de uv.lock, con sus hashes
# ---------------------------------------------------------------------------
# Antes esta línea era `uv pip install --system --no-cache .` con un comentario
# que decía «instalación determinista a partir del lockfile». No había lockfile:
# resolvía contra PyPI en cada build, así que dos imágenes construidas con el
# mismo commit podían traer versiones distintas. Un rango `>=` no es un pin.
#
# `--locked` falla si uv.lock no corresponde a pyproject.toml, en vez de
# resolver por su cuenta y seguir: un candado desactualizado tiene que detener el
# build, no arreglarse solo en silencio.
#
# SIN `--extra`, así que NO entran `dev` ni `analitica`: esta imagen no lleva
# pytest, ruff, streamlit ni pandas. El laboratorio corre en su propio contenedor.
#
# `--no-install-project` deja la capa de dependencias —la pesada— cacheada
# mientras el candado no cambie, y el código se copia después. El proyecto no se
# instala como paquete porque nada lo importa así: `uvicorn app.main:app` y
# `alembic` encuentran `app/` y `db/` desde WORKDIR.
#
# `--python python3.12` y `UV_PYTHON_DOWNLOADS=never`: que use el intérprete que
# la imagen base ya trae. Sin esto uv puede bajarse su propio CPython, y la
# imagen acabaría con dos Pythons y un paso de red más en cada build.
COPY pyproject.toml uv.lock ./
RUN UV_PYTHON_DOWNLOADS=never uv sync --locked --no-install-project \
        --no-cache --python python3.12

COPY app ./app
COPY db ./db
COPY alembic.ini ./

# El venv que creó `uv sync` vive en /srv/.venv. Sin esto, `uvicorn` y `alembic`
# del CMD y de docker-compose se buscarían en el Python del sistema, donde no
# están.
ENV PATH="/srv/.venv/bin:$PATH" \
    VIRTUAL_ENV="/srv/.venv"

# No corre como root: si algo se escapa, que no sea con todos los permisos.
RUN useradd --create-home --uid 10001 dsd && chown -R dsd /srv
USER dsd

EXPOSE 8000

# Los workers de uvicorn se dimensionan en compose: con 8-10 dispositivos,
# 2-4 bastan de sobra en el mini PC.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
