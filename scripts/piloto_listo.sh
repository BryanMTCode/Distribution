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
# Y para el día de operación, todos de una vez (ADR 0002 §171):
#
#   bash scripts/en_el_servidor.sh piloto_listo.sh todos
#
set -uo pipefail

VENDEDOR="${1:-${DSD_PILOTO_VENDEDOR:-}}"
URL="${DSD_DATABASE_URL:-postgresql://postgres:dsd@127.0.0.1:5432/dsd}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Lo que ningún script puede revisar. Una sola vez, también con `todos`.
recordatorio() {
cat <<'TXT'

Y lo que ningún script puede revisar, en la bodega antes de salir:

  · La impresora: prendida, con papel, y en la app ⋮ → Impresora →
    «Imprimir una prueba». Si no imprime ahí, no imprimirá la venta.
  · El teléfono con batería y datos. La app trabaja sin señal y se sincroniza
    sola cada dos minutos cuando la hay; al final del día, «Sincronizar ahora».
  · Que entre a la app ANTES de salir, con señal: así baja su carga y sus
    tiendas del día.
  · El precio NO se negocia en la calle, y todo se cobra en efectivo.
TXT

}

# Las tiendas que le salen en el teléfono, ruta por ruta (ADR 0002 §174): las
# de sus rutas que van en la lista —activas o prospecto, como las enseña la app—
# y cuántas le tocan hoy, por su plan de visita o porque se le reagendaron. Con
# el día de Mazatlán: la base cuenta en UTC y a las seis de la tarde ya sería
# mañana. Un renglón por ruta: «R1107|273|47».
tiendas_por_ruta() {
    psql "$URL" -Atq -c "
        WITH hoy AS (SELECT (now() AT TIME ZONE 'America/Mazatlan')::date AS d)
        SELECT r.codigo, count(c.id),
               count(c.id) FILTER (
                   WHERE c.visita_reprogramada = hoy.d
                      OR EXISTS (SELECT 1 FROM clientes_frecuencia f
                                  WHERE f.cliente_id = c.id
                                    AND toca_visita(hoy.d, f.dia_semana,
                                                    f.semana_del_mes)
                                    AND frecuencia_vigente(f.cliente_id, f.desde,
                                                           f.hasta, hoy.d)))
          FROM usuarios u
          JOIN usuarios_rutas ur ON ur.usuario_id = u.id
          JOIN rutas r ON r.id = ur.ruta_id
          CROSS JOIN hoy
          LEFT JOIN clientes c ON c.ruta_id = r.id
                              AND c.estatus IN ('activo', 'prospecto')
         WHERE u.codigo = '$(echo "$1" | tr -cd 'A-Za-z0-9_-')'
         GROUP BY r.codigo ORDER BY r.codigo" 2>/dev/null
}

dia_de_hoy() {
    psql "$URL" -Atq -c "
        SELECT (ARRAY['domingo', 'lunes', 'martes', 'miércoles', 'jueves',
                      'viernes', 'sábado'])
               [extract(dow FROM now() AT TIME ZONE 'America/Mazatlan')::int + 1]" \
        2>/dev/null
}

# `todos`: la misma revisión para cada vendedor activo, y al final quién está
# listo y quién no, para no tener que leer veinte pantallas para saberlo.
if [ "$VENDEDOR" = "todos" ]; then
    CODIGOS="$(psql "$URL" -Atq -c "
        SELECT codigo FROM usuarios
         WHERE rol_codigo = 'vendedor' AND activo ORDER BY codigo" 2>/dev/null)"
    if [ -z "$CODIGOS" ]; then
        echo "No hay vendedores activos, o no se pudo leer la base."
        exit 1
    fi
    RESUMEN=""
    MAL=0
    for codigo in $CODIGOS; do
        # Junto a cada uno, sus tiendas: lo que debe decir su teléfono (§174).
        TIENDAS="$(tiendas_por_ruta "$codigo" \
            | awk -F'|' '{ printf "%s%s: %s tiendas, hoy %s", (NR > 1 ? " · " : ""), $1, $2, $3 }')"
        if DSD_SIN_RECORDATORIO=1 bash "${BASH_SOURCE[0]}" "$codigo"; then
            RESUMEN="${RESUMEN}$(printf '  %-12s %-10s %s' listo "$codigo" "$TIENDAS")"$'\n'
        else
            RESUMEN="${RESUMEN}$(printf '  %-12s %-10s %s' 'POR ARREGLAR' "$codigo" "$TIENDAS")"$'\n'
            MAL=$((MAL + 1))
        fi
        printf '\n\n'
    done
    printf '═══════════════════════════════════════════════════════════════\n'
    printf 'Todos los vendedores (hoy es %s)\n\n%s' "$(dia_de_hoy)" "$RESUMEN"
    recordatorio
    exit $(( MAL > 0 ? 1 : 0 ))
fi

if [ -t 1 ]; then
    VERDE=$'\033[32m'; ROJO=$'\033[31m'; AMBAR=$'\033[33m'; GRIS=$'\033[90m'; FIN=$'\033[0m'
else
    VERDE=''; ROJO=''; AMBAR=''; GRIS=''; FIN=''
fi

FALLAS=0
bien()  { printf '  %sOK%s    %s\n' "$VERDE" "$FIN" "$1"; }
avisa() {
    printf '  %sNOTA%s  %s\n' "$AMBAR" "$FIN" "$1"
    [ $# -gt 1 ] && printf '        %s%s%s\n' "$GRIS" "$2" "$FIN"
    return 0
}
falla() {
    FALLAS=$((FALLAS + 1))
    printf '  %sFALLA%s %s\n' "$ROJO" "$FIN" "$1"
    [ $# -gt 1 ] && printf '        %sarréglalo con:%s %s\n' "$GRIS" "$FIN" "$2"
    return 0
}
titulo() { printf '\n%s\n' "$1"; }

if [ -z "$VENDEDOR" ]; then
    echo "Falta el código del vendedor."
    echo "  make piloto-listo VENDEDOR=VEND01     (o VENDEDOR=todos)"
    exit 2
fi

# `psql` con `-A -t`: sin alineación ni encabezados, para leer el valor directo.
# `ON_ERROR_STOP` no se usa a propósito: una consulta que falle debe dejar el
# valor vacío y seguir revisando lo demás, no abortar el diagnóstico completo.
consultar() { psql "$URL" -Atq -c "$1" 2>/dev/null; }

printf 'Revisión antes de salir a ruta — %s\n' "$VENDEDOR"
# Sin usuario ni contraseña: en el servidor la URL trae la del `.env`.
BASE_SIN_CLAVE="${URL%%\?*}"
printf '%s%s%s\n' "$GRIS" "base: ${BASE_SIN_CLAVE##*@}" "$FIN"

# ---------------------------------------------------------------------------
titulo "Base de datos"
# ---------------------------------------------------------------------------
if ! consultar "SELECT 1" | grep -q 1; then
    falla "no se puede conectar a PostgreSQL" "make db && make doctor"
    printf '\n%sSin base no hay nada más que revisar.%s\n' "$ROJO" "$FIN"
    exit 1
fi
bien "conecta"

# El id de la revisión que trae el archivo, no su nombre: no siempre son iguales.
# `alembic_version` guarda 32 caracteres, así que la 0060 se llama
# «…_la_tienda_que_el_telefono_tiene» y su revisión es «…_la_tienda_del_telefono».
# Comparar nombres daba una FALLA de migraciones con la base al día.
ULTIMA_MIGRACION="$(ls "$RAIZ"/server/db/alembic/versions/*.py 2>/dev/null \
    | grep -v __ | sort | tail -1)"
VERSION_REPO="$(grep -m1 '^revision = "' "$ULTIMA_MIGRACION" 2>/dev/null \
    | sed 's/^revision = "\([^"]*\)".*/\1/')"
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
    || falla "su rol es '$ROL' y esta revisión es de un vendedor de ruta" \
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
titulo "Para vender hoy"
# ---------------------------------------------------------------------------
# Lo que el día de operación necesita y la revisión del piloto no veía (ADR 0002
# §171): sin folios el teléfono no cierra la venta, con el corte de ayer abierto
# la carga de hoy no sale, y con una versión vieja la app no hace lo que la
# oficina espera.
if [ "${EQUIPOS:-0}" -ge 1 ] 2>/dev/null; then
    for par in venta:ventas:venta no_drop:no_drops:visita merma:mermas:merma; do
        IFS=':' read -r tipo tabla nombre <<<"$par"
        QUEDAN="$(consultar "
            SELECT r.hasta - GREATEST(r.consumido_hasta, COALESCE((
                     SELECT max(x.folio_consecutivo) FROM $tabla x
                      WHERE x.dispositivo_id = r.dispositivo_id
                        AND x.folio_consecutivo BETWEEN r.desde AND r.hasta), 0))
              FROM folios_rangos r
              JOIN dispositivos d ON d.id = r.dispositivo_id
             WHERE d.usuario_id = '$ID' AND d.estado = 'activo'
               AND r.documento_tipo = '$tipo' AND NOT r.agotado
             ORDER BY r.hasta DESC LIMIT 1")"
        if [ -z "$QUEDAN" ]; then
            falla "su teléfono no tiene folios de $nombre: no podrá cerrar una" \
                  "que entre a la app con señal y sincronice; si sigue, vuelve a vincularlo"
        elif [ "$QUEDAN" -le 0 ] 2>/dev/null; then
            falla "se le acabaron los folios de $nombre" \
                  "que sincronice con la app +58 o más nueva, o vuelve a vincular el teléfono"
        elif [ "$QUEDAN" -le 300 ] 2>/dev/null; then
            avisa "le quedan $QUEDAN folios de $nombre: al sincronizar se le alargan solos"
        else
            bien "folios de $nombre: le quedan $QUEDAN"
        fi
    done

    IFS='|' read -r APP ULTIMA <<<"$(consultar "
        SELECT COALESCE(app_version, ''),
               COALESCE(to_char(ultima_sync_push_en AT TIME ZONE current_setting('TimeZone'),
                                'DD/MM HH24:MI'), '')
          FROM dispositivos
         WHERE usuario_id = '$ID' AND estado = 'activo'
         ORDER BY ultima_sync_push_en DESC NULLS LAST LIMIT 1")"
    ULTIMA_APP="$(grep -m1 '^version:' "$RAIZ/mobile/app/pubspec.yaml" 2>/dev/null \
                  | sed 's/^version:[[:space:]]*//')"
    if [ -z "$APP" ]; then
        avisa "su teléfono no ha dicho qué versión de la app tiene: que sincronice"
    elif [ -n "$ULTIMA_APP" ] && [ "$APP" != "$ULTIMA_APP" ]; then
        avisa "tiene la app $APP y la última es la $ULTIMA_APP: instálale la nueva encima"
    else
        bien "app $APP"
    fi
    if [ -z "$ULTIMA" ]; then
        avisa "su teléfono no ha sincronizado nunca"
    else
        bien "última sincronización: $ULTIMA"
    fi
fi

CORTES_VIEJOS="$(consultar "
    SELECT count(*) FROM cortes_vendedor
     WHERE vendedor_id = '$ID' AND estado = 'pendiente'
       AND fecha_operativa < CURRENT_DATE")"
[ "${CORTES_VIEJOS:-0}" -eq 0 ] 2>/dev/null && bien "sin cortes de días anteriores por cerrar" \
    || falla "tiene $CORTES_VIEJOS corte(s) de días anteriores sin cerrar: su carga no sale" \
             "panel → Cortes y cargas (si pidió carga, aceptarla cierra el corte)"

CARGA="$(consultar "
    SELECT folio || ' (' || CASE estado WHEN 'confirmada' THEN 'en el camión'
                                        ELSE 'en el corte' END || ')'
      FROM cargas
     WHERE vendedor_id = '$ID' AND estado IN ('confirmada', 'en_ruta')
     ORDER BY fecha_operativa DESC, creado_en DESC LIMIT 1")"
PIDIO="$(consultar "
    SELECT count(*) FROM solicitudes_carga
     WHERE vendedor_id = '$ID' AND estado = 'pendiente'")"
PIEZAS="$(consultar "
    SELECT COALESCE(sum(e.cantidad), 0)::int FROM existencias e
      JOIN usuarios u ON u.almacen_id = e.almacen_id
     WHERE u.id = '$ID' AND e.cantidad > 0")"
if [ -n "$CARGA" ]; then
    bien "carga $CARGA · ${PIEZAS:-0} pieza(s) en el camión"
elif [ "${PIDIO:-0}" -ge 1 ] 2>/dev/null; then
    avisa "pidió carga y espera que la aceptes" "panel → Cortes y cargas, o la app de Gerencia"
elif [ "${PIEZAS:-0}" -gt 0 ] 2>/dev/null; then
    avisa "sin carga nueva: sale con las ${PIEZAS} pieza(s) que tiene en el camión"
else
    falla "su camión está vacío y no tiene carga" \
          "que pida su carga desde la app, o créasela en el panel → Cargas"
fi

CUARENTENA="$(consultar "
    SELECT count(*) FROM sync_cuarentena
     WHERE usuario_id = '$ID' AND estado = 'pendiente'")"
[ "${CUARENTENA:-0}" -eq 0 ] 2>/dev/null && bien "nada suyo en cuarentena" \
    || avisa "$CUARENTENA operación(es) suya(s) en cuarentena: lo vendió y no ha entrado" \
             "panel → Cuarentena: Reprocesar o Descartar"

# ---------------------------------------------------------------------------
titulo "Sus clientes y su catálogo"
# ---------------------------------------------------------------------------
# Lo que debe enseñar su teléfono en «Todos» y en «Hoy» (ADR 0002 §174). Antes
# contaba `c.activo`, una columna que no existe: la consulta fallaba callada y
# la revisión decía siempre «sus rutas no tienen clientes».
POR_RUTA="$(tiendas_por_ruta "$VENDEDOR")"
CLIENTES="$(printf '%s\n' "$POR_RUTA" | awk -F'|' '{ s += $2 } END { print s + 0 }')"
DE_HOY="$(printf '%s\n' "$POR_RUTA" | awk -F'|' '{ s += $3 } END { print s + 0 }')"
DETALLE="$(printf '%s\n' "$POR_RUTA" \
    | awk -F'|' 'NF == 3 { printf "%s%s: %s, hoy %s", (NR > 1 ? " · " : ""), $1, $2, $3 }')"
if [ "${CLIENTES:-0}" -ge 5 ] 2>/dev/null; then
    bien "$CLIENTES tiendas en sus rutas; hoy ($(dia_de_hoy)) le tocan $DE_HOY"
    printf '        %s%s%s\n' "$GRIS" "$DETALLE" "$FIN"
elif [ "${CLIENTES:-0}" -ge 1 ] 2>/dev/null; then
    avisa "solo $CLIENTES cliente(s) en sus rutas: ¿está completa la cartera?"
else
    falla "sus rutas no tienen clientes: no hay a quién venderle" \
          "cárgalos en el panel → Clientes"
fi

# La tienda sin ruta le sale a TODOS los teléfonos, en «Todos». Si son muchas,
# cada vendedor ve más tiendas de las que tiene.
SIN_RUTA="$(consultar "
    SELECT count(*) FROM clientes
     WHERE ruta_id IS NULL AND estatus IN ('activo', 'prospecto') AND NOT mayoreo")"
if [ "${SIN_RUTA:-0}" -gt 0 ] 2>/dev/null; then
    avisa "$SIN_RUTA tienda(s) sin ruta: les salen a todos los vendedores" \
          "asígnales su ruta en el panel → Clientes"
fi

# Y lo que dijo su teléfono la última vez que preguntó cuáles son suyas: al
# sincronizar, una vez al día, suelta las demás (ADR 0002 §174, la +59).
REVISADAS="$(consultar "
    SELECT tiendas_en_telefono, tiendas_soltadas,
           to_char(tiendas_revisadas_en AT TIME ZONE 'America/Mazatlan', 'DD/MM HH24:MI')
      FROM dispositivos
     WHERE usuario_id = '$ID' AND estado = 'activo' AND tiendas_revisadas_en IS NOT NULL
     ORDER BY tiendas_revisadas_en DESC LIMIT 1")"
if [ -z "$REVISADAS" ]; then
    avisa "su teléfono todavía no revisa cuáles tiendas son suyas" \
          "lo hace solo al sincronizar, desde la app +59"
else
    IFS='|' read -r TENIA SOLTO CUANDO <<<"$REVISADAS"
    if [ "${SOLTO:-0}" -gt 0 ] 2>/dev/null; then
        bien "su teléfono enseñaba $TENIA tiendas y soltó $SOLTO que no eran suyas ($CUANDO)"
    else
        bien "su teléfono enseña $TENIA tiendas, todas suyas ($CUANDO)"
    fi
fi

# Sin coordenadas no hay mapa del día ni distancia al cliente, y el alta en
# calle del piloto no se puede comparar contra nada.
SIN_GPS="$(consultar "
    SELECT count(*) FROM clientes c
      JOIN usuarios_rutas ur ON ur.ruta_id = c.ruta_id
     WHERE ur.usuario_id = '$ID' AND c.estatus IN ('activo', 'prospecto')
       AND c.ubicacion IS NULL")"
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
       AND NOT EXISTS (SELECT 1 FROM precios pr WHERE pr.producto_id = p.id)")"
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
# El piloto, solo si hay uno corriendo: en la operación de todos los días no hay,
# y una NOTA que sale siempre enseña a no leer las notas.
# ---------------------------------------------------------------------------
ACTIVO_PILOTO="$(consultar "
    SELECT p.codigo || ' · ' || u.codigo || ' · ' || p.inicio || ' a ' || p.fin
      FROM pilotos p JOIN usuarios u ON u.id = p.vendedor_id
     WHERE p.activo")"
if [ -n "$ACTIVO_PILOTO" ]; then
    titulo "El piloto"
    if ! consultar "SELECT 1 FROM piloto_criterios LIMIT 1" | grep -q 1; then
        falla "no están sembrados los criterios de salida" "make migrar"
    else
        CUANTOS="$(consultar "SELECT count(*) FROM piloto_criterios")"
        bien "$CUANTOS criterios de salida sembrados"
    fi
    bien "piloto activo: $ACTIVO_PILOTO"
fi

# ---------------------------------------------------------------------------
printf '\n'
printf '%s───────────────────────────────────────────────────────────────%s\n' "$GRIS" "$FIN"
if [ "$FALLAS" -eq 0 ]; then
    printf '%sLo que una consulta puede revisar, está.%s\n' "$VERDE" "$FIN"
else
    printf '%s%s cosa(s) que hay que arreglar antes de que salga.%s\n' "$ROJO" "$FALLAS" "$FIN"
fi
[ -z "${DSD_SIN_RECORDATORIO:-}" ] && recordatorio
exit $(( FALLAS > 0 ? 1 : 0 ))
