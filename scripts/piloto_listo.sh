#!/usr/bin/env bash
#
# ¿Está este vendedor listo para el piloto de campo? (Fase 3)
#
# ───────────────────────────────────────────────────────────────────────────
# POR QUÉ NO ES UNA SECCIÓN MÁS DE `doctor.sh`
# ───────────────────────────────────────────────────────────────────────────
# `doctor.sh` contesta "¿puedo trabajar en el entorno de desarrollo?". Esto
# contesta algo distinto: "¿puede este vendedor concreto salir el lunes a su
# ruta con el teléfono?". Son preguntas de dos mundos — una es del taller, la
# otra es de la operación — y mezclarlas haría que la segunda se perdiera entre
# las nueve secciones de la primera.
#
# Nada de lo que revisa es exótico. Todo es algo que, si falta, se descubre a
# las 6 de la mañana del lunes en la bodega, con el camión cargado, y cuesta el
# primer día del piloto. El primer día de un piloto de catorce es el 7%.
#
# ───────────────────────────────────────────────────────────────────────────
# LO QUE ESTE SCRIPT NO PUEDE REVISAR, Y ES LO MÁS IMPORTANTE
# ───────────────────────────────────────────────────────────────────────────
# Que el vendedor haya hecho una jornada completa de práctica. Ninguna consulta
# lo sabe, y es el punto que más seguido se salta. Lo dice al final, siempre.
#
#   make piloto-listo VENDEDOR=VEND01
#
set -uo pipefail

VENDEDOR="${1:-${DSD_PILOTO_VENDEDOR:-}}"
URL="${DSD_DATABASE_URL:-postgresql://postgres:dsd@127.0.0.1:5432/dsd}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -t 1 ]; then
    VERDE=$'\033[32m'; ROJO=$'\033[31m'; AMBAR=$'\033[33m'; GRIS=$'\033[90m'; FIN=$'\033[0m'
else
    VERDE=''; ROJO=''; AMBAR=''; GRIS=''; FIN=''
fi

FALLAS=0
bien()  { printf '  %sOK%s    %s\n' "$VERDE" "$FIN" "$1"; }
avisa() { printf '  %sNOTA%s  %s\n' "$AMBAR" "$FIN" "$1"; }
falla() {
    FALLAS=$((FALLAS + 1))
    printf '  %sFALLA%s %s\n' "$ROJO" "$FIN" "$1"
    [ $# -gt 1 ] && printf '        %sarréglalo con:%s %s\n' "$GRIS" "$FIN" "$2"
    return 0
}
titulo() { printf '\n%s\n' "$1"; }

if [ -z "$VENDEDOR" ]; then
    echo "Falta el código del vendedor."
    echo "  make piloto-listo VENDEDOR=VEND01"
    exit 2
fi

# `psql` con `-A -t`: sin alineación ni encabezados, para leer el valor directo.
# `ON_ERROR_STOP` no se usa a propósito: una consulta que falle debe dejar el
# valor vacío y seguir revisando lo demás, no abortar el diagnóstico completo.
consultar() { psql "$URL" -Atq -c "$1" 2>/dev/null; }

printf 'Piloto de campo — revisión previa de %s\n' "$VENDEDOR"
printf '%s%s%s\n' "$GRIS" "base: ${URL%%\?*}" "$FIN"

# ---------------------------------------------------------------------------
titulo "Base de datos"
# ---------------------------------------------------------------------------
if ! consultar "SELECT 1" | grep -q 1; then
    falla "no se puede conectar a PostgreSQL" "make db && make doctor"
    printf '\n%sSin base no hay nada más que revisar.%s\n' "$ROJO" "$FIN"
    exit 1
fi
bien "conecta"

VERSION_REPO="$(ls "$RAIZ"/server/db/alembic/versions/*.py 2>/dev/null \
    | grep -v __ | sed 's#.*/##; s#\.py$##' | sort | tail -1)"
VERSION_BASE="$(consultar "SELECT version_num FROM alembic_version")"
if [ -z "$VERSION_BASE" ]; then
    falla "la base no tiene migraciones aplicadas" "make migrar"
elif [ "$VERSION_BASE" = "$VERSION_REPO" ]; then
    bien "migraciones al día ($VERSION_BASE)"
else
    falla "la base está en $VERSION_BASE y el repositorio en $VERSION_REPO" "make migrar"
fi

# La zona horaria decide qué día es "hoy", y de ahí salen la fecha operativa, el
# cuadre del piloto y el arqueo. Con el servidor en UTC y la operación en UTC−6,
# a partir de las 18:00 "hoy" es mañana: el cuadre compararía el papel de un día
# contra las ventas de otro. Es el defecto que encontró la Fase 7.
ZONA_BASE="$(consultar "SHOW timezone")"
ZONA_SISTEMA="$(date +%Z)"
if [ -n "$ZONA_BASE" ] && [ "$ZONA_BASE" = "UTC" ] && [ "$ZONA_SISTEMA" != "UTC" ]; then
    falla "PostgreSQL está en UTC y el sistema en $ZONA_SISTEMA" \
          "revisa TZ en docker-compose.yml y corre make doctor"
else
    bien "zona horaria de la base: $ZONA_BASE"
fi

# ---------------------------------------------------------------------------
titulo "El vendedor"
# ---------------------------------------------------------------------------
FILA="$(consultar "
    SELECT u.id, u.rol_codigo, u.activo,
           COALESCE(a.codigo, ''), COALESCE(a.tipo, ''),
           COALESCE(u.dias_max_offline::text, '')
      FROM usuarios u
      LEFT JOIN almacenes a ON a.id = u.almacen_id
     WHERE u.codigo = '$(echo "$VENDEDOR" | tr -cd 'A-Za-z0-9_-')'")"
if [ -z "$FILA" ]; then
    falla "no existe el usuario $VENDEDOR" "créalo en el panel → Usuarios y rutas"
    printf '\n%sSin el vendedor no se puede revisar su ruta ni su camión.%s\n' "$ROJO" "$FIN"
    exit 1
fi
IFS='|' read -r ID ROL ACTIVO CAMION TIPO_ALMACEN DIAS <<<"$FILA"
bien "existe ($VENDEDOR)"

[ "$ROL" = "vendedor" ] && bien "rol: vendedor" \
    || falla "su rol es '$ROL' y el piloto es de un vendedor de ruta" \
             "cámbialo en el panel → Usuarios y rutas"
[ "$ACTIVO" = "t" ] && bien "activo" \
    || falla "está inactivo: no podrá entrar a la app" "actívalo en el panel"

if [ -z "$CAMION" ]; then
    falla "no tiene almacén propio: sin camión no hay a dónde cargarle" \
          "asígnaselo en el panel → Usuarios y rutas"
elif [ "$TIPO_ALMACEN" != "camion" ]; then
    falla "su almacén $CAMION es de tipo '$TIPO_ALMACEN', no 'camion'" \
          "revisa el almacén en el panel → Inventario"
else
    bien "camión: $CAMION"
fi

# `dias_max_offline` es lo que decide cuándo la app se bloquea por no
# sincronizar (Fase 9). En un piloto importa el doble: un tope de 1 día en una
# ruta sin señal bloquea al vendedor el martes y el piloto mide el bloqueo en
# vez de medir la app.
if [ -z "$DIAS" ]; then
    avisa "sin tope de días sin sincronizar: usa el valor por omisión"
elif [ "$DIAS" -le 1 ] 2>/dev/null; then
    falla "dias_max_offline = $DIAS: en una ruta con mala señal lo bloquea al segundo día" \
          "súbelo a 3 o más en el panel → Usuarios y rutas"
else
    bien "tope de días sin sincronizar: $DIAS"
fi

RUTAS="$(consultar "
    SELECT count(*) FROM usuarios_rutas ur
      JOIN rutas r ON r.id = ur.ruta_id
     WHERE ur.usuario_id = '$ID' AND r.activo")"
[ "${RUTAS:-0}" -ge 1 ] 2>/dev/null && bien "tiene $RUTAS ruta(s) activa(s)" \
    || falla "no tiene ninguna ruta activa asignada" \
             "asígnasela en el panel → Usuarios y rutas"

# ---------------------------------------------------------------------------
titulo "Su teléfono"
# ---------------------------------------------------------------------------
EQUIPOS="$(consultar "
    SELECT count(*) FROM dispositivos
     WHERE usuario_id = '$ID' AND estado = 'activo'")"
if [ "${EQUIPOS:-0}" -ge 1 ] 2>/dev/null; then
    bien "$EQUIPOS teléfono(s) activo(s) registrado(s)"
else
    falla "no tiene teléfono activo registrado" \
          "regístralo desde la app y actívalo en el panel → Teléfonos"
fi

BORRADO="$(consultar "
    SELECT count(*) FROM dispositivos
     WHERE usuario_id = '$ID' AND borrado_ordenado_en IS NOT NULL
       AND borrado_confirmado_en IS NULL")"
[ "${BORRADO:-0}" -eq 0 ] 2>/dev/null && bien "sin órdenes de borrado pendientes" \
    || falla "tiene $BORRADO equipo(s) con borrado ordenado sin confirmar" \
             "cancélalo en el panel → Teléfonos si el equipo sigue en uso"

# ---------------------------------------------------------------------------
titulo "Sus clientes y su catálogo"
# ---------------------------------------------------------------------------
CLIENTES="$(consultar "
    SELECT count(*) FROM clientes c
      JOIN usuarios_rutas ur ON ur.ruta_id = c.ruta_id
     WHERE ur.usuario_id = '$ID' AND c.activo")"
if [ "${CLIENTES:-0}" -ge 5 ] 2>/dev/null; then
    bien "$CLIENTES clientes activos en sus rutas"
elif [ "${CLIENTES:-0}" -ge 1 ] 2>/dev/null; then
    avisa "solo $CLIENTES cliente(s) en sus rutas: ¿está completa la cartera?"
else
    falla "sus rutas no tienen clientes: no hay a quién venderle" \
          "cárgalos en el panel → Clientes"
fi

# Sin coordenadas no hay mapa del día ni distancia al cliente, y el alta en
# calle del piloto no se puede comparar contra nada.
SIN_GPS="$(consultar "
    SELECT count(*) FROM clientes c
      JOIN usuarios_rutas ur ON ur.ruta_id = c.ruta_id
     WHERE ur.usuario_id = '$ID' AND c.activo AND c.ubicacion IS NULL")"
# El `-ge 1` de los clientes no es una redundancia: con cero clientes, "todos
# tienen coordenadas" es cierto y se lee como un OK justo debajo de la falla que
# dice que no hay ninguno. Dos renglones que se contradicen hacen que nadie le
# crea al resto del reporte.
if [ "${CLIENTES:-0}" -lt 1 ] 2>/dev/null; then
    :
elif [ "${SIN_GPS:-0}" -eq 0 ] 2>/dev/null; then
    bien "todos sus clientes tienen coordenadas"
else
    avisa "$SIN_GPS cliente(s) sin coordenadas: el mapa del día saldrá incompleto"
fi

# Un producto sin precio en la lista del cliente no se puede vender, y el
# vendedor lo descubre frente al cliente. Es la falla de catálogo más cara.
PRODUCTOS="$(consultar "SELECT count(*) FROM productos WHERE activo")"
SIN_PRECIO="$(consultar "
    SELECT count(*) FROM productos p
     WHERE p.activo
       AND NOT EXISTS (
             SELECT 1 FROM precios pr
              WHERE pr.producto_id = p.id AND pr.activo)")"
if [ "${PRODUCTOS:-0}" -lt 1 ] 2>/dev/null; then
    falla "no hay productos activos: no hay nada que cargarle al camión" \
          "cárgalos en el panel → Productos"
elif [ "${SIN_PRECIO:-0}" -eq 0 ] 2>/dev/null; then
    bien "los $PRODUCTOS productos activos tienen precio"
else
    falla "$SIN_PRECIO producto(s) activo(s) sin precio en ninguna lista" \
          "ponles precio en el panel → Productos, o desactívalos"
fi

# ---------------------------------------------------------------------------
titulo "El respaldo"
# ---------------------------------------------------------------------------
# Va aquí y no en `doctor.sh` por una razón de calendario: durante el piloto, la
# base tiene los únicos datos de ventas que existen y no se pueden recapturar —
# el ticket se lo llevó el cliente.
CARPETA="${DSD_RESPALDOS:-$HOME/respaldos-dsd}"
ULTIMO="$(ls -t "$CARPETA"/dsd-*.dump 2>/dev/null | head -1)"
if [ -z "$ULTIMO" ]; then
    falla "no hay ningún respaldo en $CARPETA" "make respaldo"
else
    EDAD=$(( ($(date +%s) - $(date -r "$ULTIMO" +%s)) / 3600 ))
    if [ "$EDAD" -le 48 ]; then
        bien "respaldo de hace ${EDAD}h: $(basename "$ULTIMO")"
    else
        falla "el respaldo más reciente tiene ${EDAD}h" "make respaldo"
    fi
fi

# ---------------------------------------------------------------------------
titulo "El piloto"
# ---------------------------------------------------------------------------
if ! consultar "SELECT 1 FROM piloto_criterios LIMIT 1" | grep -q 1; then
    falla "no están sembrados los criterios de salida" "make migrar"
else
    CUANTOS="$(consultar "SELECT count(*) FROM piloto_criterios")"
    bien "$CUANTOS criterios de salida sembrados"
fi

ACTIVO_PILOTO="$(consultar "
    SELECT p.codigo || ' · ' || u.codigo || ' · ' || p.inicio || ' a ' || p.fin
      FROM pilotos p JOIN usuarios u ON u.id = p.vendedor_id
     WHERE p.activo")"
if [ -z "$ACTIVO_PILOTO" ]; then
    avisa "no hay piloto definido todavía: defínelo en el panel → Piloto"
else
    bien "piloto activo: $ACTIVO_PILOTO"
fi

# ---------------------------------------------------------------------------
printf '\n'
printf '%s───────────────────────────────────────────────────────────────%s\n' "$GRIS" "$FIN"
if [ "$FALLAS" -eq 0 ]; then
    printf '%sLo que una consulta puede revisar, está.%s\n' "$VERDE" "$FIN"
else
    printf '%s%s cosa(s) que hay que arreglar antes del lunes.%s\n' "$ROJO" "$FALLAS" "$FIN"
fi
cat <<'TXT'

Y lo que ningún script puede revisar, que es el punto más importante de la
lista del día −1:

  · ¿El vendedor hizo una JORNADA COMPLETA de práctica, haciendo y no mirando?
    Diez ventas, un cobro, un no-drop, una merma, una cancelación y la
    liquidación. Con la base de pruebas.
  · ¿Sabe que el precio NO se negocia en la calle? Si lo descubre frente al
    cliente, es la peor forma de enterarse.
  · ¿Sabe que la app no es la libreta de la noche?

  docs/PILOTO.md §1
TXT
exit $(( FALLAS > 0 ? 1 : 0 ))
