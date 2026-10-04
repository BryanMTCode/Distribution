#!/usr/bin/env bash
# =============================================================================
# ¿Está el servidor listo para tener la operación encima?
# =============================================================================
# Se corre EN EL SERVIDOR, desde la carpeta del despliegue.
#
# Nació como `revisar_cifrado.sh`, para una mini PC en la oficina. El servidor se
# mudó a un VPS y eso cambió qué hay que revisar, no cuánto:
#
#   · El disco cifrado dejó de ser lo principal. En la oficina protegía de que se
#     llevaran el equipo; en un VPS nadie se lleva tu disco, y sin TPM el cifrado
#     cuesta teclear la frase en cada reinicio del proveedor. Aquí se REPORTA, no
#     se exige — la decisión está en SEGURIDAD-OPERATIVA §5.1.
#   · Apareció algo que la oficina no tenía: UNA IP PÚBLICA. El servidor está en
#     internet abierto, escaneado todo el día. Eso es lo que esto revisa ahora.
#
# Lo que NO puede comprobar, y hay que probar a mano: que el servidor vuelva solo
# después de un reinicio.
set -uo pipefail

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
    [ $# -gt 1 ] && printf '        %s%s%s\n' "$GRIS" "$2" "$FIN"
    return 0
}
pista()  { printf '        %s%s%s\n' "$GRIS" "$1" "$FIN"; }
titulo() { printf '\n%s\n' "$1"; }

# -----------------------------------------------------------------------------
# ¿De qué dispositivo cuelga una ruta, y hay un `crypt` en su cadena?
# -----------------------------------------------------------------------------
# Con LVM sobre LUKS la cadena de `/` es:
#
#     /dev/mapper/vg-root        (lvm)
#       └ /dev/mapper/dm_crypt-0 (crypt)   <- esto es lo que se busca
#           └ /dev/nvme0n1p3     (part)
#               └ /dev/nvme0n1   (disk)
#
# Se pide con `-P` —pares `NAME="x" TYPE="y"`— y no en columnas: en columnas
# lsblk DIBUJA el árbol, y el nombre viene con glifos `└─` pegados delante. Con
# `-P` no hay glifos que recortar, que es de donde salen los parseos que fallan
# solo en la máquina de alguien más.
cadena_de() {
    lsblk -nsPo NAME,TYPE "$1" 2>/dev/null
}

esta_cifrado() {
    cadena_de "$1" | grep -q 'TYPE="crypt"'
}

# La partición LUKS que sostiene un dispositivo, para preguntarle por sus llaves.
# En la cadena invertida, el renglón SIGUIENTE al `crypt` es su respaldo físico.
particion_luks() {
    cadena_de "$1" | awk '
        encontrado {
            if (match($0, /NAME="[^"]*"/)) {
                nombre = substr($0, RSTART + 6, RLENGTH - 7)
                print "/dev/" nombre
            }
            exit
        }
        /TYPE="crypt"/ { encontrado = 1 }'
}

echo "Revisión del servidor"
printf '%s%s%s\n' "$GRIS" "(se corre EN EL SERVIDOR, desde la carpeta del despliegue)" "$FIN"

# -----------------------------------------------------------------------------
titulo "La puerta a internet"
# -----------------------------------------------------------------------------
# Lo que la oficina no tenía. Detrás del túnel de Cloudflare el servidor no tenía
# puertos abiertos a nadie; un VPS tiene IP pública y lo escanean todo el día.
if ! command -v ufw >/dev/null 2>&1; then
    avisa "no encontré ufw: no pude revisar el cortafuegos"
    pista "sudo apt install -y ufw   (ver docs/DESPLIEGUE.md)"
elif ! ESTADO_UFW="$(ufw status 2>/dev/null)"; then
    avisa "no pude leer el estado de ufw (¿hace falta sudo?)"
elif ! printf '%s' "$ESTADO_UFW" | grep -qi "^Status: active"; then
    falla "el cortafuegos está APAGADO y el servidor tiene IP pública" \
          "ver docs/DESPLIEGUE.md: ufw allow 22,80,443 y ufw enable"
else
    bien "cortafuegos activo"
    # Lo que no debería estar abierto. 5432 es el que importa: una base expuesta
    # a internet se encuentra con un escaneo, no con un ataque dirigido.
    for puerto in 5432 8000 8501; do
        if printf '%s' "$ESTADO_UFW" | grep -qE "(^|[^0-9])$puerto(/| |$)"; then
            falla "ufw tiene una regla para el puerto $puerto" \
                  "ni la base ni la API ni el laboratorio se exponen directo: todo entra por Caddy"
        fi
    done
fi

# -----------------------------------------------------------------------------
titulo "Docker y el cortafuegos (la trampa)"
# -----------------------------------------------------------------------------
# Docker escribe sus PROPIAS reglas de iptables, por delante de las de ufw. Un
# `ports:` en compose queda abierto a internet AUNQUE ufw diga que ese puerto
# está cerrado, y `ufw status` no lo menciona. Es la trampa más conocida de este
# combo y la que deja bases de datos públicas sin que nadie lo note.
#
# Se revisa la fuente de la verdad —qué publica compose— y no solo lo que está
# corriendo, para que la respuesta sea la misma con el stack arriba o abajo.
PUBLICADOS="$(awk '
    /^  [a-z][a-z0-9_-]*:[[:space:]]*$/ { servicio = $1; sub(/:$/, "", servicio) }
    /^    ports:/ { en_puertos = 1; next }
    en_puertos && /^      - / {
        linea = $0
        gsub(/[^0-9:.]/, "", linea)
        split(linea, partes, ":")
        print servicio, partes[1]
        next
    }
    en_puertos && !/^      / { en_puertos = 0 }
' docker-compose.yml 2>/dev/null)"

if [ -z "$PUBLICADOS" ]; then
    avisa "no encontré puertos publicados en docker-compose.yml"
    pista "¿estás en la carpeta del despliegue?"
else
    INESPERADOS=0
    while read -r servicio puerto; do
        [ -z "$puerto" ] && continue
        case "$puerto" in
        80|443) ;;
        *)
            INESPERADOS=$((INESPERADOS + 1))
            falla "«$servicio» publica el puerto $puerto a internet" \
                  "ufw NO lo tapa: Docker escribe sus reglas por delante. Quítale el ports: y entra por Caddy"
            ;;
        esac
    done <<< "$PUBLICADOS"
    [ "$INESPERADOS" -eq 0 ] && bien "solo 80 y 443 publicados; el resto vive en la red interna"
fi

# -----------------------------------------------------------------------------
titulo "El acceso por SSH"
# -----------------------------------------------------------------------------
# El orden importa: Ubuntu pone `Include /etc/ssh/sshd_config.d/*.conf` ARRIBA de
# `sshd_config`, y en OpenSSH gana el PRIMER valor obtenido. Así que los archivos
# de `.d/` se leen primero y los suyos ganan.
CONFIG_SSH=""
for ruta in /etc/ssh/sshd_config.d/*.conf /etc/ssh/sshd_config; do
    [ -r "$ruta" ] && CONFIG_SSH="$CONFIG_SSH$(cat "$ruta" 2>/dev/null)
"
done

if [ -z "$CONFIG_SSH" ]; then
    avisa "no pude leer la configuración de sshd (¿hace falta sudo?)"
else
    # `head -1` y no `tail -1`: `sshd_config(5)` dice «the first obtained value
    # will be used». La primera versión de esto usaba `tail -1` y reportaba lo
    # CONTRARIO de la verdad cuando un drop-in endurecía sobre un `sshd_config`
    # permisivo — que es justo la forma en que se endurece un Ubuntu. Lo delató
    # probarlo con los dos archivos en desacuerdo.
    #
    # Supone el `Include` arriba, que es donde lo pone Ubuntu. Si alguien lo mueve
    # al final, el orden se invierte y esto habría que releerlo.
    valor_ssh() {
        printf '%s' "$CONFIG_SSH" \
            | grep -iE "^[[:space:]]*$1[[:space:]]" \
            | head -1 | awk '{print tolower($2)}'
    }

    CLAVES="$(valor_ssh PasswordAuthentication)"
    if [ "$CLAVES" = "no" ]; then
        bien "SSH sin contraseñas: solo llave"
    else
        falla "SSH acepta contraseñas (PasswordAuthentication ${CLAVES:-por omisión})" \
              "con IP pública eso es fuerza bruta todo el día: PasswordAuthentication no"
    fi

    ROOT="$(valor_ssh PermitRootLogin)"
    case "$ROOT" in
    no|prohibit-password|without-password)
        bien "root no entra con contraseña (PermitRootLogin $ROOT)" ;;
    *)
        falla "PermitRootLogin ${ROOT:-por omisión} permite entrar como root" \
              "PermitRootLogin no, y se trabaja con un usuario con sudo" ;;
    esac
fi

# -----------------------------------------------------------------------------
titulo "La hora, que decide qué día es «hoy»"
# -----------------------------------------------------------------------------
# Las imágenes de VPS vienen en UTC. En UTC−6, a partir de las 18:00 locales
# `CURRENT_DATE` ya dice mañana: el tablero del día sale vacío por la tarde y el
# arqueo compara el papel de un día contra las ventas de otro. Es el defecto que
# encontró la Fase 7, y en un VPS empieza activado por omisión.
ZONA_ESPERADA="$(grep -E '^DSD_ZONA=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
ZONA_ESPERADA="${ZONA_ESPERADA:-America/Mexico_City}"
if command -v timedatectl >/dev/null 2>&1 \
   && ZONA_HOST="$(timedatectl show -p Timezone --value 2>/dev/null)" \
   && [ -n "$ZONA_HOST" ]; then
    if [ "$ZONA_HOST" = "$ZONA_ESPERADA" ]; then
        bien "el servidor está en $ZONA_HOST"
    else
        falla "el servidor está en $ZONA_HOST y la operación en $ZONA_ESPERADA" \
              "sudo timedatectl set-timezone $ZONA_ESPERADA"
    fi
else
    avisa "no pude leer la zona horaria del servidor"
fi

# -----------------------------------------------------------------------------
titulo "El .env, que es texto plano mientras el servidor corre"
# -----------------------------------------------------------------------------
# Lo único que separa cuatro secretos de cualquier cuenta del sistema son sus
# permisos. El cifrado del disco no interviene aquí: con el servidor encendido
# —que es siempre— el disco está abierto.
ENV_SERVIDOR="${DSD_ENV:-.env}"
if [ ! -f "$ENV_SERVIDOR" ]; then
    avisa "no hay $ENV_SERVIDOR aquí: córrelo desde la carpeta del despliegue"
else
    MODO="$(stat -c '%a' "$ENV_SERVIDOR")"
    case "$MODO" in
    600|400) bien ".env solo lo lee su dueño ($MODO)" ;;
    *) falla ".env con permisos $MODO: lo puede leer más gente que su dueño" \
             "chmod 600 $ENV_SERVIDOR" ;;
    esac
fi

# -----------------------------------------------------------------------------
titulo "El disco"
# -----------------------------------------------------------------------------
# No es FALLA: en un VPS, cifrar el disco es una decisión con un costo —sin TPM,
# la frase se teclea por la consola del proveedor en CADA reinicio, incluidos los
# que el proveedor hace por mantenimiento— y §5.1 la toma explícitamente. Lo que
# se reporta es el hecho, para que nadie lo dé por supuesto en ningún sentido.
RAIZ_DEV="$(findmnt -no SOURCE / 2>/dev/null)"
if [ -z "$RAIZ_DEV" ]; then
    avisa "no pude averiguar de qué dispositivo cuelga /"
elif esta_cifrado "$RAIZ_DEV"; then
    bien "/ está sobre LUKS ($RAIZ_DEV)"
else
    avisa "/ no está cifrado ($RAIZ_DEV) — decisión de §5.1, no un olvido"
    pista "lo que protege los datos fuera del servidor son los respaldos cifrados"
fi

# El swap importa más de lo que parece cuando sí hay cifrado: PostgreSQL pagina y
# el `.env` se lee a memoria, así que un swap sin cifrar deja en disco, en claro,
# lo mismo que se cifró.
SWAPS="$(swapon --noheadings --show=NAME,TYPE 2>/dev/null)"
if [ -z "$SWAPS" ]; then
    avisa "no hay swap activo"
elif [ -n "${RAIZ_DEV:-}" ] && esta_cifrado "$RAIZ_DEV"; then
    while read -r nombre tipo; do
        [ -z "$nombre" ] && continue
        if [ "${nombre#/dev/zram}" != "$nombre" ]; then
            bien "swap en zram ($nombre): es RAM comprimida, no toca el disco"
        elif [ "$tipo" = "file" ]; then
            CONTENEDOR="$(findmnt -no SOURCE --target "$nombre" 2>/dev/null)"
            if [ -n "$CONTENEDOR" ] && esta_cifrado "$CONTENEDOR"; then
                bien "swap en archivo ($nombre), sobre un sistema cifrado"
            else
                falla "swap en archivo SIN cifrar: $nombre" \
                      "vive en $CONTENEDOR, que no está sobre LUKS"
            fi
        elif esta_cifrado "$nombre"; then
            bien "swap en dispositivo cifrado ($nombre)"
        else
            falla "swap SIN cifrar: $nombre" \
                  "con / cifrado, lo que pagine ahí sale en claro"
        fi
    done <<< "$SWAPS"
fi

# -----------------------------------------------------------------------------
# Las llaves del disco, solo si hay disco cifrado que revisar
# -----------------------------------------------------------------------------
if [ -n "${RAIZ_DEV:-}" ] && esta_cifrado "$RAIZ_DEV"; then
    if ! command -v cryptsetup >/dev/null 2>&1; then
        avisa "no hay cryptsetup: no pude revisar las llaves del volumen"
    else
        LUKS="$(particion_luks "$RAIZ_DEV")"
        if [ -z "$LUKS" ] || [ ! -e "$LUKS" ]; then
            avisa "no ubiqué la partición LUKS detrás de $RAIZ_DEV"
        elif ! VOLCADO="$(cryptsetup luksDump "$LUKS" 2>/dev/null)"; then
            avisa "no pude leer $LUKS (¿hace falta sudo?)"
        else
            RANURAS="$(printf '%s' "$VOLCADO" | grep -cE '^[[:space:]]*[0-9]+: luks2')"

            # LOS DOS caminos de sellado: `systemd-cryptenroll` deja un token
            # `systemd-tpm2`; `clevis luks bind`, uno `clevis`. Contar solo el
            # primero diría que el disco no abre solo cuando sí abre, y —lo
            # grave— contaría la ranura del TPM como si fuera una frase que
            # alguien puede teclear.
            AUTO="$(printf '%s' "$VOLCADO" \
                    | grep -ciE '^[[:space:]]*[0-9]+: (systemd-tpm2|clevis)')"
            COMO="$(printf '%s' "$VOLCADO" \
                    | grep -oiE '^[[:space:]]*[0-9]+: (systemd-tpm2|clevis)' \
                    | awk '{print $2}' | sort -u | tr '\n' ' ')"

            if [ "$AUTO" -gt 0 ]; then
                bien "llave automática enrolada (${COMO% }): abre solo al arrancar"
            else
                avisa "sin llave automática: pide la frase en CADA arranque"
                pista "en un VPS eso se teclea por la consola del proveedor"
            fi

            # Si alguien enroló una llave automática y BORRÓ la frase, el disco
            # queda ilegible para siempre el día que el TPM se resetee. No hay
            # otra llave. Se cuentan ranuras MENOS llaves automáticas.
            RECUPERABLES=$((RANURAS - AUTO))
            if [ "$RANURAS" -lt 1 ]; then
                falla "no encontré ranuras de llave en $LUKS" \
                      "revísalo a mano antes de confiar en esto"
            elif [ "$RECUPERABLES" -lt 1 ]; then
                falla "NO QUEDA NINGUNA FRASE: la única llave es la automática" \
                      "si se resetea, el disco no se vuelve a abrir. Agrega una frase ya: cryptsetup luksAddKey $LUKS"
            elif [ "$AUTO" -gt 0 ]; then
                bien "$RECUPERABLES frase(s) + $AUTO llave(s) automática(s)"
            else
                bien "$RECUPERABLES frase(s) de recuperación"
            fi
        fi
    fi
fi

# -----------------------------------------------------------------------------
printf '\n'
if [ "$FALLAS" -eq 0 ]; then
    printf '%sNada que arreglar aquí.%s\n' "$VERDE" "$FIN"
else
    printf '%s%s cosa(s) que arreglar.%s\n' "$ROJO" "$FALLAS" "$FIN"
fi
printf '%sLo que esto NO prueba: que el servidor vuelva SOLO después de un reinicio.%s\n' "$GRIS" "$FIN"
printf '%sEso se prueba reiniciándolo —el proveedor lo va a hacer sin avisar— y%s\n' "$GRIS" "$FIN"
printf '%scomprobando que /salud responda sin que nadie toque nada: docs/DESPLIEGUE.md.%s\n' "$GRIS" "$FIN"
[ "$FALLAS" -eq 0 ] || exit 1
