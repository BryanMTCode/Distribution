#!/usr/bin/env bash
#
# Pone la base en blanco EN EL SERVIDOR, de un jalón (ver
# `server/app/infra/base_en_blanco.py`): se quedan los usuarios, sus rutas,
# bodegas, camiones y teléfonos; se borra todo lo demás y entran los artículos
# de `server/db/semillas/articulos_distribuciones_se.csv` con su existencia.
#
#   bash scripts/base_en_blanco.sh
#
# Los cuatro pasos, en este orden y por esta razón:
#   1. El respaldo. Si falla, se para aquí y NO se borra nada.
#   2. Detiene la API y el worker: que ningún teléfono escriba mientras se vacía.
#   3. El borrado y la carga. Antes de borrar dice cuánto hay y pide escribir
#      «EN BLANCO»; con cualquier otra cosa no toca nada.
#   4. Arranca la API y el worker otra vez, pase lo que pase en el paso 3.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

echo "== 1/4 · Respaldo de la base =="
bash scripts/en_el_servidor.sh respaldar.sh

echo "== 2/4 · Deteniendo la API y el worker =="
docker compose stop api worker
trap 'echo "== 4/4 · Arrancando la API y el worker =="; docker compose start api worker' EXIT

echo "== 3/4 · Base en blanco =="
docker compose run --rm api python -m app.cli base-en-blanco
