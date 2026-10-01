#!/usr/bin/env bash
#
# Respaldo de la base de datos.
#
# ───────────────────────────────────────────────────────────────────────────
# LAS CUATRO DECISIONES DE ESTE ARCHIVO
# ───────────────────────────────────────────────────────────────────────────
# 1. **Formato `custom` (-Fc), no SQL plano.** Comprime, y sobre todo permite
#    restaurar en paralelo y restaurar SOLO UNA TABLA. El día que haga falta
#    recuperar `cuentas_por_cobrar` sin pisar el resto, un .sql de 400 MB no
#    sirve para eso.
#
# 2. **Checksum al lado.** Un respaldo corrupto es indistinguible de uno bueno
#    hasta que se intenta restaurar, y eso pasa el peor día. El `.sha256` se
#    verifica en el simulacro y tarda un segundo.
#
# 3. **El respaldo del día se escribe a un temporal y se renombra al final.**
#    Si el disco se llena o se corta la luz a media escritura, el archivo
#    incompleto NO reemplaza al de ayer. `mv` dentro del mismo sistema de
#    archivos es atómico; copiar encima no lo es.
#
# 4. **Retención por cantidad, no por fecha.** `find -mtime` borra por fecha de
#    modificación, y si el respaldo falla una semana se queda sin nada que
#    borrar y luego borra todo de golpe. Contar archivos y quedarse con los N
#    más recientes es la regla que no se puede volver en contra.
#
# ───────────────────────────────────────────────────────────────────────────
# LO QUE ESTE SCRIPT NO HACE, Y HAY QUE HACER APARTE
# ───────────────────────────────────────────────────────────────────────────
# **Sacar el respaldo del edificio.** Un respaldo en el mismo disco que la base
# no protege del disco; en el mismo cuarto, no protege del cuarto. Lo cubre
# `docs/RESPALDOS.md` §3, y es la parte que de verdad hay que verificar.
set -euo pipefail

DESTINO="${DSD_RESPALDOS:-$HOME/respaldos-dsd}"
RETENER="${DSD_RESPALDOS_RETENER:-14}"
URL="${DSD_RESPALDO_URL:-postgresql://postgres:dsd@127.0.0.1:5432/dsd}"

if [ -t 1 ]; then
    VERDE=$'\033[32m'; ROJO=$'\033[31m'; GRIS=$'\033[90m'; FIN=$'\033[0m'
else
    VERDE=''; ROJO=''; GRIS=''; FIN=''
fi

mkdir -p "$DESTINO"

SELLO="$(date +%Y%m%d-%H%M%S)"
FINAL="$DESTINO/dsd-$SELLO.dump"
PARCIAL="$FINAL.parcial"

limpiar() { rm -f "$PARCIAL"; }
trap limpiar EXIT

printf 'Respaldando a %s\n' "$FINAL"

# --no-owner y --no-privileges: el respaldo se restaura en una base de
# simulacro donde los roles (dsd_api, dsd_analitica) pueden no existir. Sin
# esto, `pg_restore` escupe decenas de errores de "role does not exist" que
# esconden los errores de verdad.
if ! pg_dump "$URL" \
        --format=custom \
        --compress=6 \
        --no-owner \
        --no-privileges \
        --file="$PARCIAL" 2>"$PARCIAL.log"; then
    printf '%sFALLÓ el pg_dump%s\n' "$ROJO" "$FIN"
    sed 's/^/    /' "$PARCIAL.log" >&2
    rm -f "$PARCIAL.log"
    exit 1
fi
rm -f "$PARCIAL.log"

# El renombrado es el commit: hasta aquí, el respaldo de ayer sigue siendo el
# bueno.
mv "$PARCIAL" "$FINAL"
trap - EXIT

( cd "$DESTINO" && sha256sum "$(basename "$FINAL")" > "$(basename "$FINAL").sha256" )

TAMANO="$(du -h "$FINAL" | cut -f1)"
printf '%sOK%s  %s (%s)\n' "$VERDE" "$FIN" "$(basename "$FINAL")" "$TAMANO"

# ---------------------------------------------------------------------------
# Retención
# ---------------------------------------------------------------------------
mapfile -t TODOS < <(ls -1t "$DESTINO"/dsd-*.dump 2>/dev/null || true)
if [ "${#TODOS[@]}" -gt "$RETENER" ]; then
    for viejo in "${TODOS[@]:$RETENER}"; do
        rm -f "$viejo" "$viejo.sha256"
        printf '%sborrado por retención: %s%s\n' "$GRIS" "$(basename "$viejo")" "$FIN"
    done
fi

printf '\nQuedan %s respaldo(s) en %s.\n' \
    "$(ls -1 "$DESTINO"/dsd-*.dump 2>/dev/null | wc -l)" "$DESTINO"
printf '%sUn respaldo que nunca restauraste no es un respaldo: corre "make simulacro".%s\n' \
    "$GRIS" "$FIN"
