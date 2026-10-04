#!/usr/bin/env bash
#
# Corre un script de `scripts/` DENTRO de la red de Docker Compose.
#
# ───────────────────────────────────────────────────────────────────────────
# POR QUÉ EXISTE ESTE ENVOLTORIO
# ───────────────────────────────────────────────────────────────────────────
# `respaldar.sh`, `simulacro.sh` y `piloto_listo.sh` hablan con PostgreSQL por
# TCP y se escribieron para la máquina de desarrollo, donde la base escucha en
# `127.0.0.1:5432`. En el servidor NADA DE ESO SE CUMPLE:
#
#   1. PostgreSQL vive en un contenedor y **no publica puerto**: el compose solo
#      saca 80 y 443, a propósito (ver DESPLIEGUE.md §4). Así que en el host no
#      hay nada escuchando en 5432 y `make respaldo` fallaría con «connection
#      refused» — que suena a base caída y no lo es.
#   2. `pg_dump` **no está instalado en el host**, y aunque lo instalaras, su
#      versión tiene que coincidir con la del servidor. La imagen del contenedor
#      ya trae la correcta.
#   3. `make` tampoco está instalado en un Ubuntu de servidor recién hecho.
#   4. El usuario y la contraseña no son los de desarrollo: salen del `.env`.
#
# Este script resuelve las cuatro: levanta un contenedor de un solo uso con la
# imagen de PostgreSQL —que ya tiene `pg_dump` de la versión buena—, lo mete en
# la red de compose para que el nombre `postgres` resuelva, y le pasa las URLs
# armadas con lo que dice el `.env`.
#
# ───────────────────────────────────────────────────────────────────────────
# USO
# ───────────────────────────────────────────────────────────────────────────
#   bash scripts/en_el_servidor.sh respaldar.sh
#   bash scripts/en_el_servidor.sh simulacro.sh
#   bash scripts/en_el_servidor.sh piloto_listo.sh VEND01
#
# Los respaldos quedan en `~/respaldos-dsd` DEL HOST (o en `DSD_RESPALDOS`), no
# dentro del contenedor: un respaldo que vive en el contenedor desaparece con él.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

GUION="${1:-}"
if [ -z "$GUION" ]; then
    cat >&2 <<'USO'
Falta el nombre del script a correr.

  bash scripts/en_el_servidor.sh respaldar.sh
  bash scripts/en_el_servidor.sh simulacro.sh
  bash scripts/en_el_servidor.sh piloto_listo.sh VEND01
USO
    exit 2
fi
shift

if [ ! -f "scripts/$GUION" ]; then
    printf 'No existe scripts/%s. Los que se pueden correr así:\n' "$GUION" >&2
    ls -1 scripts/*.sh | sed 's|scripts/|  |' >&2
    exit 2
fi

if [ ! -f .env ]; then
    echo "No hay .env en $RAIZ. Esto se corre EN EL SERVIDOR, donde sí lo hay." >&2
    exit 1
fi

# `set -a` exporta lo que se lea; el `.env` es de la forma CLAVE=valor.
set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ -z "${DSD_DB_PASSWORD:-}" ]; then
    echo "DSD_DB_PASSWORD está vacía en .env. Sin ella no hay forma de conectar." >&2
    exit 1
fi

USUARIO="${DSD_DB_USUARIO:-dsd}"
DESTINO="${DSD_RESPALDOS:-$HOME/respaldos-dsd}"
mkdir -p "$DESTINO"

# Las tres URLs que esperan los scripts de dentro. `postgres` es el nombre del
# servicio en la red de compose, no un host de internet.
BASE="postgresql://$USUARIO:$DSD_DB_PASSWORD@postgres:5432/dsd"
ADMIN="postgresql://$USUARIO:$DSD_DB_PASSWORD@postgres:5432/postgres"

# --no-deps: no arranca nada más. --user: los archivos quedan a nombre de quien
# corre esto y no de root, para poder copiarlos y borrarlos sin sudo. El repo va
# de solo lectura: estos scripts leen, no escriben en él.
exec docker compose run --rm --no-deps \
    --user "$(id -u):$(id -g)" \
    --entrypoint bash \
    -v "$RAIZ:/repo:ro" \
    -v "$DESTINO:/respaldos" \
    -e HOME=/tmp \
    -e DSD_RESPALDOS=/respaldos \
    -e DSD_RESPALDOS_RETENER="${DSD_RESPALDOS_RETENER:-14}" \
    -e DSD_RESPALDO_URL="$BASE" \
    -e DSD_DATABASE_URL="$BASE" \
    -e DSD_SIMULACRO_ADMIN_URL="$ADMIN" \
    postgres "/repo/scripts/$GUION" "$@"
