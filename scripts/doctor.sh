#!/usr/bin/env bash
#
# Diagnóstico del entorno de desarrollo. Un comando en lugar de seis.
#
# ───────────────────────────────────────────────────────────────────────────
# PARA QUÉ EXISTE
# ───────────────────────────────────────────────────────────────────────────
# Levantar el entorno tras reiniciar la PC falla siempre por las mismas cinco
# cosas, y cada una se ve igual desde afuera: "no conecta". El síntoma no dice
# si falta Docker, si el contenedor quedó parado, si otro proceso tomó el 5432,
# si faltan las migraciones o si no hay usuario para entrar al panel.
#
# Esto las separa y, para cada una, imprime EL COMANDO que la arregla. No
# arregla nada por su cuenta: un script que "arregla" la base de datos sin
# preguntar es justo el que un día borra lo que estabas probando.
#
# No falla por lo opcional. Flutter y adb no hacen falta para trabajar en el
# servidor, así que su ausencia se informa y no cuenta como error.
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTENEDOR=dsd-postgres
VOLUMEN=dsd_pgdata
PUERTO_DB=5432
PUERTO_API=8000

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

# ---------------------------------------------------------------------------
titulo "Docker"
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    falla "no hay comando 'docker' en esta terminal" \
          "Docker Desktop → Settings → Resources → WSL Integration → activa tu distro"
elif ! docker info >/dev/null 2>&1; then
    falla "el comando existe pero el motor no responde" \
          "abre Docker Desktop en Windows y espera a que diga 'Engine running'"
else
    bien "el motor responde"

    ESTADO="$(docker inspect -f '{{.State.Status}}' "$CONTENEDOR" 2>/dev/null || echo ausente)"
    case "$ESTADO" in
        running) bien "el contenedor $CONTENEDOR está arriba" ;;
        ausente) falla "el contenedor $CONTENEDOR no existe" "make db" ;;
        *)       falla "el contenedor $CONTENEDOR está '$ESTADO'" "make db" ;;
    esac

    if docker volume inspect "$VOLUMEN" >/dev/null 2>&1; then
        bien "el volumen $VOLUMEN existe (tus datos sobreviven al reinicio)"
    else
        avisa "no hay volumen $VOLUMEN: este contenedor es de los viejos, sin datos persistentes."
        avisa "Cuando puedas perder la base: make db-borrar && make db && make migrar && make usuario"
    fi
fi

# ---------------------------------------------------------------------------
titulo "La base de datos en el puerto $PUERTO_DB"
# ---------------------------------------------------------------------------
# ¿Hay ALGO escuchando ahí? Se prueba abriendo el puerto, no preguntándole a
# `ss`: en una WSL mínima `ss` puede no estar instalado, y entonces la respuesta
# sería "nadie escucha" cuando en realidad hay otro PostgreSQL ocupándolo. Un
# diagnóstico que miente es peor que ninguno — manda a buscar al lugar equivocado.
puerto_ocupado() {
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3<&- && return 0
    return 1
}

QUIEN_DB=""
if command -v ss >/dev/null 2>&1; then
    QUIEN_DB="$(ss -ltnp 2>/dev/null | grep -E ":$PUERTO_DB\b" || true)"
fi

if docker exec "$CONTENEDOR" pg_isready -U postgres -q >/dev/null 2>&1; then
    bien "acepta conexiones"

    PSQL=(docker exec "$CONTENEDOR" psql -U postgres -d dsd -tAc)

    if "${PSQL[@]}" "SELECT 1 FROM pg_extension WHERE extname='postgis'" 2>/dev/null | grep -q 1; then
        bien "PostGIS instalado"
    else
        falla "falta la extensión PostGIS" "make db"
    fi

    VERSION="$("${PSQL[@]}" "SELECT version_num FROM alembic_version" 2>/dev/null || true)"
    if [ -z "$VERSION" ]; then
        falla "la base no tiene migraciones aplicadas" "make migrar"
    else
        bien "migraciones aplicadas (última: $VERSION)"
    fi

    USUARIOS="$("${PSQL[@]}" \
        "SELECT count(*) FROM usuarios WHERE rol_codigo IN ('admin','gerente','supervisor') AND activo" \
        2>/dev/null || echo 0)"
    if [ "${USUARIOS:-0}" -gt 0 ]; then
        bien "hay $USUARIOS usuario(s) de oficina: puedes entrar al panel"
    else
        falla "no hay ningún usuario de oficina; el panel no te dejará entrar" "make usuario"
    fi
elif puerto_ocupado "$PUERTO_DB"; then
    # El caso que cuesta una hora si no se nombra: el puerto contesta, así que
    # "connection refused" no aparece, pero quien contesta es otro PostgreSQL —el
    # servicio de Windows, casi siempre— y ahí no están nuestras tablas.
    falla "algo escucha en el $PUERTO_DB pero NO es el contenedor del proyecto" \
          "detén el otro PostgreSQL (servicio de Windows) y luego: make db"
    [ -n "$QUIEN_DB" ] && printf '        %s%s%s\n' "$GRIS" "$QUIEN_DB" "$FIN"
else
    falla "nadie escucha en el $PUERTO_DB" "make db"
fi

# ---------------------------------------------------------------------------
titulo "Zona horaria"
# ---------------------------------------------------------------------------
# Esto no es cosmético y cuesta una tarde de confusión.
#
# "Hoy" lo deciden `date.today()` en Python y `CURRENT_DATE` en PostgreSQL, y
# los dos usan la zona del sistema. En el centro de México son UTC−6, así que
# con el reloj en UTC, a partir de las 18:00 locales "hoy" pasa a ser MAÑANA: el
# tablero de Gerencia muestra el día siguiente vacío, la cobranza del día sale
# sin cobros y el arqueo no cuadra con lo que la gente tiene en la mano.
#
# El síntoma es desconcertante porque a las 11 de la mañana todo funciona.
ZONA_ESPERADA="America/Mexico_City"
ZONA_SO="$( (timedatectl show -p Timezone --value 2>/dev/null) || cat /etc/timezone 2>/dev/null || echo '' )"
DESFASE="$(date +%z)"

if [ "$ZONA_SO" = "$ZONA_ESPERADA" ] || [ "$DESFASE" = "-0600" ] || [ "$DESFASE" = "-0500" ]; then
    bien "el sistema está en hora local (${ZONA_SO:-$DESFASE})"
else
    falla "el sistema NO está en hora de México (${ZONA_SO:-desconocida}, UTC$DESFASE)" \
          "sudo ln -sf /usr/share/zoneinfo/$ZONA_ESPERADA /etc/localtime"
    avisa "con el reloj en UTC, después de las 18:00 'hoy' ya es mañana para el"
    avisa "tablero, la cobranza del día y el arqueo."
fi

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTENEDOR"; then
    ZONA_DB="$(docker exec "$CONTENEDOR" date +%z 2>/dev/null || echo '')"
    if [ -z "$ZONA_DB" ]; then
        avisa "no se pudo leer la hora del contenedor"
    elif [ "$ZONA_DB" = "$DESFASE" ]; then
        bien "PostgreSQL va a la misma hora que el sistema (UTC$ZONA_DB)"
    else
        # Las dos tienen que coincidir: una mezcla hace que `CURRENT_DATE` y
        # `date.today()` discrepen, y entonces el renglón del tablero se
        # escribe con una fecha y se lee con otra.
        falla "PostgreSQL va en UTC$ZONA_DB y el sistema en UTC$DESFASE" \
              "make db-parar \&\& make db   (el contenedor toma TZ al crearse)"
    fi
fi

# ---------------------------------------------------------------------------
titulo "El puerto $PUERTO_API de la API"
# ---------------------------------------------------------------------------
FANTASMAS="$(pgrep -af "uvicorn app.main:app" 2>/dev/null || true)"
if [ -n "$FANTASMAS" ]; then
    avisa "ya hay un uvicorn corriendo. Si no fuiste tú, es un proceso fantasma:"
    printf '        %s%s%s\n' "$GRIS" "$FANTASMAS" "$FIN"
    avisa "para matarlo: pkill -f 'uvicorn app.main:app'"
elif puerto_ocupado "$PUERTO_API"; then
    avisa "algo ocupa el $PUERTO_API y no es uvicorn. Mira quién: ss -ltnp | grep $PUERTO_API"
else
    bien "libre: 'make api' puede arrancar"
fi

# ---------------------------------------------------------------------------
titulo "Variables de entorno"
# ---------------------------------------------------------------------------
# En desarrollo NO hace falta ninguna: `app/core/config.py` tiene valor por
# omisión para todas, y el Makefile pasa DSD_DATABASE_URL en cada objetivo. Lo
# que sí rompe es un .env a medias, porque se lee ANTES de los defaults.
if [ -f "$RAIZ/server/.env" ]; then
    avisa "existe server/.env y se lee al arrancar la API."
    if grep -qE '^\s*DSD_ENTORNO\s*=\s*produccion' "$RAIZ/server/.env" 2>/dev/null; then
        falla "ese .env dice DSD_ENTORNO=produccion: la API se niega a arrancar sin un secreto real" \
              "comenta esa línea, o: openssl rand -hex 32  y ponlo en DSD_JWT_SECRETO"
    fi
else
    bien "sin server/.env: la API usa los valores de desarrollo (es lo correcto aquí)"
fi

if [ -f "$RAIZ/.env" ]; then
    avisa ".env en la raíz: eso es para 'docker compose' (producción), no para el trabajo diario."
    avisa "make db / make api no lo leen, y no les hace falta."
fi

# ---------------------------------------------------------------------------
titulo "Entorno de Python"
# ---------------------------------------------------------------------------
if [ -x "$RAIZ/server/.venv/bin/python" ]; then
    bien "venv del servidor listo ($("$RAIZ/server/.venv/bin/python" --version 2>&1))"
else
    falla "falta el entorno virtual del servidor" "make instalar"
fi

# ---------------------------------------------------------------------------
titulo "Móvil (opcional: solo para probar en el teléfono)"
# ---------------------------------------------------------------------------
if command -v flutter >/dev/null 2>&1; then
    bien "flutter en el PATH"
else
    avisa "no hay flutter: solo hace falta para compilar la app. sudo snap install flutter --classic"
fi

if command -v adb >/dev/null 2>&1; then
    EQUIPOS="$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device" {print $1}')"
    if [ -n "$EQUIPOS" ]; then
        bien "teléfono conectado: $(echo "$EQUIPOS" | tr '\n' ' ')"
    elif adb devices 2>/dev/null | awk 'NR>1 && $2=="unauthorized"' | grep -q .; then
        avisa "el teléfono está conectado pero NO autorizado: desbloquéalo y acepta el diálogo de depuración USB"
    else
        avisa "no hay teléfono visible. Con el cable puesto, desde PowerShell como admin:"
        avisa "  usbipd list   y luego   usbipd attach --wsl --busid <BUSID>"
    fi
else
    avisa "no hay adb: solo hace falta para instalar en el teléfono. sudo apt install -y android-tools-adb"
fi

# ---------------------------------------------------------------------------
printf '\n'
if [ "$FALLAS" -eq 0 ]; then
    printf '%sTodo listo.%s  make api   →   http://127.0.0.1:8000/panel\n' "$VERDE" "$FIN"
    exit 0
fi
printf '%s%s cosa(s) que arreglar antes de probar.%s Corre el comando que dice cada FALLA y vuelve a pasar el doctor.\n' \
    "$ROJO" "$FALLAS" "$FIN"
exit 1
