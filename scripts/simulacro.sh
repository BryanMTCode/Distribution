#!/usr/bin/env bash
#
# Simulacro de restauración.
#
# ───────────────────────────────────────────────────────────────────────────
# POR QUÉ ESTE SCRIPT ES MÁS IMPORTANTE QUE EL DE RESPALDO
# ───────────────────────────────────────────────────────────────────────────
# Un respaldo que nunca restauraste no es un respaldo: es un archivo del que no
# sabes nada. Las tres formas en que un respaldo resulta inútil justo el día que
# se necesita son éstas, y ninguna se nota sin restaurar:
#
#   · El archivo está truncado —el disco se llenó— y `pg_dump` terminó con 0.
#   · Falta una extensión. `postgis` no viene en el dump: se crea en la base
#     destino ANTES de restaurar, y si nadie lo hizo nunca, la restauración
#     falla con un error que no dice eso.
#   · El dump es bueno pero el esquema cambió, y las migraciones que vinieron
#     después no están. Se restaura y la API no arranca.
#
# Esto los encuentra. Restaura en una base DESECHABLE, corre las invariantes
# (`db/tests/smoke_invariantes.sql`) y cuenta los renglones de las tablas que
# importan. Después borra la base del simulacro.
#
# NO TOCA LA BASE DE PRODUCCIÓN. Es la única propiedad no negociable de este
# archivo: un simulacro que pueda pisar lo que protege es peor que no tenerlo.
# Por eso el nombre de la base destino se construye aquí y se verifica que
# termine en `_simulacro` antes de cualquier `DROP`.
set -euo pipefail

DESTINO="${DSD_RESPALDOS:-$HOME/respaldos-dsd}"
ADMIN_URL="${DSD_SIMULACRO_ADMIN_URL:-postgresql://postgres:dsd@127.0.0.1:5432/postgres}"
BASE_SIMULACRO="dsd_simulacro"

if [ -t 1 ]; then
    VERDE=$'\033[32m'; ROJO=$'\033[31m'; AMBAR=$'\033[33m'; GRIS=$'\033[90m'; FIN=$'\033[0m'
else
    VERDE=''; ROJO=''; AMBAR=''; GRIS=''; FIN=''
fi

bien()  { printf '  %sOK%s    %s\n' "$VERDE" "$FIN" "$1"; }
avisa() { printf '  %sNOTA%s  %s\n' "$AMBAR" "$FIN" "$1"; }
falla() { printf '  %sFALLA%s %s\n' "$ROJO" "$FIN" "$1"; FALLAS=$((FALLAS + 1)); }
FALLAS=0

# El cinturón de seguridad. Si alguien cambia la variable de arriba a `dsd`,
# esto detiene el script antes del primer DROP.
case "$BASE_SIMULACRO" in
    *_simulacro) ;;
    *)
        printf '%sLa base del simulacro tiene que terminar en _simulacro.%s\n' "$ROJO" "$FIN"
        exit 2
        ;;
esac

# ---------------------------------------------------------------------------
printf '\nEl archivo\n'
# ---------------------------------------------------------------------------
ARCHIVO="${1:-}"
if [ -z "$ARCHIVO" ]; then
    ARCHIVO="$(ls -1t "$DESTINO"/dsd-*.dump 2>/dev/null | head -1 || true)"
fi
if [ -z "$ARCHIVO" ] || [ ! -f "$ARCHIVO" ]; then
    falla "no hay ningún respaldo en $DESTINO"
    printf '        %screa uno con:%s make respaldo\n' "$GRIS" "$FIN"
    exit 1
fi
bien "$(basename "$ARCHIVO") ($(du -h "$ARCHIVO" | cut -f1))"

EDAD_HORAS=$(( ( $(date +%s) - $(stat -c %Y "$ARCHIVO") ) / 3600 ))
if [ "$EDAD_HORAS" -gt 48 ]; then
    # Aviso y no falla: un simulacro sobre un respaldo viejo sigue probando que
    # la restauración funciona. Lo que avisa es otra cosa — que el respaldo
    # automático dejó de correr.
    avisa "el respaldo más reciente tiene $EDAD_HORAS h. ¿Está corriendo el cron?"
else
    bien "tiene $EDAD_HORAS h"
fi

if [ -f "$ARCHIVO.sha256" ]; then
    if ( cd "$(dirname "$ARCHIVO")" && sha256sum -c "$(basename "$ARCHIVO").sha256" >/dev/null 2>&1 ); then
        bien "el checksum cuadra: el archivo no está corrupto"
    else
        falla "EL CHECKSUM NO CUADRA: el archivo está corrupto o incompleto"
        exit 1
    fi
else
    avisa "sin .sha256 al lado; no se puede comprobar la integridad"
fi

# ---------------------------------------------------------------------------
printf '\nRestauración en %s\n' "$BASE_SIMULACRO"
# ---------------------------------------------------------------------------
BASE_URL="${ADMIN_URL%/*}/$BASE_SIMULACRO"

# `client_min_messages=warning` calla el NOTICE de «no existe, se salta»,
# que en la salida de un simulacro se lee como un problema y no lo es.
PSQL_ADMIN=(psql "$ADMIN_URL" -q --set=client_min_messages=warning)
"${PSQL_ADMIN[@]}" -c "DROP DATABASE IF EXISTS $BASE_SIMULACRO" >/dev/null
"${PSQL_ADMIN[@]}" -c "CREATE DATABASE $BASE_SIMULACRO" >/dev/null

# PostGIS ANTES de restaurar. El dump trae tablas con columnas `geography`, y
# sin la extensión creada el restore falla en la primera — con un error sobre un
# tipo desconocido que no menciona PostGIS.
psql "$BASE_URL" -q -c 'CREATE EXTENSION IF NOT EXISTS postgis' >/dev/null
bien "base creada con PostGIS"

BITACORA="$(mktemp)"
# `--exit-on-error` NO se usa a propósito: con él, el primer aviso sobre un rol
# inexistente abortaría la restauración y no se vería nada más. Se restaura
# completo y después se cuenta: lo que importa es si los DATOS llegaron.
if pg_restore --dbname="$BASE_URL" --no-owner --no-privileges --jobs=2 \
        "$ARCHIVO" >"$BITACORA" 2>&1; then
    bien "pg_restore terminó sin errores"
else
    ERRORES="$(grep -c 'error:' "$BITACORA" || true)"
    if [ "$ERRORES" -gt 0 ]; then
        avisa "pg_restore reportó $ERRORES aviso(s); se revisa por los datos:"
        grep 'error:' "$BITACORA" | head -5 | sed 's/^/        /'
    fi
fi
rm -f "$BITACORA"

# ---------------------------------------------------------------------------
printf '\nQué quedó dentro\n'
# ---------------------------------------------------------------------------
contar() {
    psql "$BASE_URL" -tAc "SELECT count(*) FROM $1" 2>/dev/null || echo "ERROR"
}

VERSION="$(psql "$BASE_URL" -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null || echo '')"
if [ -n "$VERSION" ]; then
    bien "migraciones: $VERSION"
    ESPERADA="$(ls -1 "$(dirname "$0")/../server/db/alembic/versions"/*.py \
        | sed 's#.*/##; s/\.py$//' | sort | tail -1)"
    if [ "$VERSION" = "$ESPERADA" ]; then
        bien "coincide con la última migración del repositorio"
    else
        # Es el fallo que no se ve: el respaldo es bueno y el esquema es viejo.
        # Restaurarlo y arrancar la API daría errores de columnas faltantes.
        avisa "el repositorio está en $ESPERADA; este respaldo es de antes"
    fi
else
    falla "no hay tabla alembic_version: la restauración no trajo el esquema"
fi

TOTAL=0
for tabla in usuarios clientes productos ventas venta_partidas cobros \
             cuentas_por_cobrar movimientos_inventario; do
    CUANTOS="$(contar "$tabla")"
    if [ "$CUANTOS" = "ERROR" ]; then
        falla "la tabla $tabla no existe en la restauración"
    else
        printf '        %-24s %s\n' "$tabla" "$CUANTOS"
        TOTAL=$((TOTAL + CUANTOS))
    fi
done

if [ "$TOTAL" -eq 0 ]; then
    # Un dump de una base vacía restaura perfecto y no prueba nada. Decirlo
    # evita el peor resultado posible de un simulacro: salir tranquilo.
    avisa "la restauración no trae NI UN renglón. El simulacro probó el camino,"
    avisa "no los datos: hazlo otra vez sobre un respaldo con operación real."
fi

# ---------------------------------------------------------------------------
printf '\nLas invariantes del diseño\n'
# ---------------------------------------------------------------------------
# No basta con que las tablas existan: lo que sostiene el sistema offline son
# los constraints y los disparadores, y un dump puede traer los datos sin
# traerlos. `smoke_invariantes.sql` intenta violar cinco reglas y espera que
# PostgreSQL lo impida.
SMOKE="$(dirname "$0")/../server/db/tests/smoke_invariantes.sql"
if [ -f "$SMOKE" ]; then
    # La salida se GUARDA, no se tira. Antes iba a /dev/null y el diagnóstico
    # era una sola línea genérica: había que adivinar entre «el dump no trajo un
    # disparador» y «la semilla chocó con un dato real», que piden arreglos
    # opuestos. Sin el ERROR de psql a la vista, no se distinguen.
    SALIDA_SMOKE="$(mktemp)"
    if psql "$BASE_URL" -v ON_ERROR_STOP=1 -q -f "$SMOKE" >"$SALIDA_SMOKE" 2>&1 \
       && ! grep -q 'FALLA' "$SALIDA_SMOKE"; then
        # El `grep` NO es redundante, y su ausencia era un falso verde.
        #
        # De las seis invariantes, tres reportan su resultado con un SELECT que
        # devuelve el texto 'PASA'/'FALLA' y dos con RAISE WARNING. Ninguna de
        # esas cinco cambia el código de salida de psql: si mañana un disparador
        # dejara de bloquear el UPDATE al libro mayor, el archivo imprimiría
        # 'FALLA · se permitió editar el libro mayor' y psql saldría con CERO.
        # El simulacro habría dicho «las invariantes se cumplen» sobre una base
        # en la que el libro mayor es editable, que es exactamente el fallo que
        # esta prueba existe para encontrar.
        bien "las 6 invariantes se cumplen sobre la base restaurada"
        rm -f "$SALIDA_SMOKE"
    else
        PRIMER_ERROR="$(grep -m1 'ERROR:' "$SALIDA_SMOKE" || true)"
        if printf '%s' "$PRIMER_ERROR" | grep -q 'duplicate key'; then
            # Esto NO es un respaldo malo: la semilla de la prueba chocó con un
            # dato real de producción. Decir «esquema incompleto» aquí es
            # reportar como inservible un respaldo que sirve.
            falla "la semilla de la prueba chocó con un dato que ya existe"
            printf '        %s%s%s\n' "$GRIS" "$PRIMER_ERROR" "$FIN"
            printf '        %sel esquema está BIEN: lo que chocó es un código repetido.%s\n' \
                "$GRIS" "$FIN"
            printf '        %sla semilla usa el prefijo ZZ-HUMO- justo para evitarlo;%s\n' \
                "$GRIS" "$FIN"
            printf '        %ssi ves esto, alguien le devolvió un código verosímil.%s\n' \
                "$GRIS" "$FIN"
        elif grep -q 'FALLA' "$SALIDA_SMOKE"; then
            # psql salió con cero pero una invariante se reportó incumplida: el
            # esquema está completo y una REGLA no se está aplicando. Es el caso
            # más grave de los tres.
            falla "una invariante del diseño NO se cumple sobre la base restaurada"
            grep 'FALLA' "$SALIDA_SMOKE" | sed "s/^/        /" >&2
        else
            falla "las invariantes NO se cumplen: el esquema restaurado está incompleto"
            [ -n "$PRIMER_ERROR" ] && printf '        %s%s%s\n' "$GRIS" "$PRIMER_ERROR" "$FIN"
        fi
        printf '        %sreprodúcelo con:%s DSD_SIMULACRO_CONSERVAR=1 %s\n' \
            "$GRIS" "$FIN" "$0"
        printf '        %sy luego:%s psql <url>/%s -f %s\n' \
            "$GRIS" "$FIN" "$BASE_SIMULACRO" "$SMOKE"
        printf '        %ssalida completa en %s%s\n' "$GRIS" "$SALIDA_SMOKE" "$FIN"
    fi
else
    avisa "no se encontró $SMOKE"
fi

# ---------------------------------------------------------------------------
# Se conserva si se pide, porque el mensaje de fallo de arriba dice «reprodúcelo
# con psql sobre esta base» y hasta ahora la borrábamos en la línea siguiente:
# la instrucción era imposible de seguir.
if [ -n "${DSD_SIMULACRO_CONSERVAR:-}" ]; then
    printf '\n%sbase %s CONSERVADA (DSD_SIMULACRO_CONSERVAR)%s\n' \
        "$GRIS" "$BASE_SIMULACRO" "$FIN"
    printf '%sbórrala con: psql -c "DROP DATABASE %s"%s\n' \
        "$GRIS" "$BASE_SIMULACRO" "$FIN"
else
    "${PSQL_ADMIN[@]}" -c "DROP DATABASE IF EXISTS $BASE_SIMULACRO" >/dev/null
    printf '\n%sbase del simulacro borrada%s\n' "$GRIS" "$FIN"
fi

if [ "$FALLAS" -eq 0 ]; then
    printf '\n%sSimulacro superado.%s Este respaldo SÍ se puede restaurar.\n' "$VERDE" "$FIN"
    printf '%sApunta la fecha en docs/RESPALDOS.md §4.%s\n' "$GRIS" "$FIN"
else
    printf '\n%s%s problema(s).%s Este respaldo NO sirve para recuperarse.\n' \
        "$ROJO" "$FALLAS" "$FIN"
    exit 1
fi
