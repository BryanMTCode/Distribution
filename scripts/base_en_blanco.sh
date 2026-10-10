#!/usr/bin/env bash
#
# Pone la base en blanco EN EL SERVIDOR, de un jalón (ver
# `server/app/infra/base_en_blanco.py`): se quedan los usuarios y sus teléfonos
# vinculados; se borra todo lo demás —también rutas, bodegas y camiones— y entran
# los artículos de `server/db/semillas/articulos_distribuciones_se.csv` con su
# existencia en una Bodega principal nueva.
#
#   bash scripts/base_en_blanco.sh
#
# El orden importa:
#   1. Dice lo que va a borrar y pide escribir «EN BLANCO» con la API ARRIBA:
#      mientras se decide, la página sigue funcionando.
#   2. El respaldo. Si falla, se para aquí y NO se borra nada.
#   3. Detiene la API y el worker —que ningún teléfono escriba mientras se
#      vacía—, borra y carga.
#   4. Levanta la API y el worker otra vez, pase lo que pase en el paso 3.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

echo "== 1/4 · Lo que se va a borrar =="
docker compose run --rm --no-deps api python -m app.cli base-en-blanco --resumen
read -r -p "Para seguir escribe EN BLANCO: " RESPUESTA
if [ "$(echo "$RESPUESTA" | tr '[:lower:]' '[:upper:]' | xargs)" != "EN BLANCO" ]; then
    echo "No se borró nada."
    exit 1
fi

echo "== 2/4 · Respaldo de la base =="
bash scripts/en_el_servidor.sh respaldar.sh

echo "== 3/4 · Base en blanco =="
trap 'echo "== 4/4 · Levantando la API y el worker =="; docker compose up -d api worker' EXIT
docker compose stop api worker
docker compose run --rm --no-deps api python -m app.cli base-en-blanco --confirmado
