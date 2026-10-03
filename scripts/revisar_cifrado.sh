#!/usr/bin/env bash
# =============================================================================
# ¿Está cifrado el disco de la mini PC, y se puede recuperar?
# =============================================================================
# `docs/SEGURIDAD-OPERATIVA.md` exigía cifrado en el TELÉFONO y en los RESPALDOS
# y no decía nada del disco del servidor, donde viven la base completa, el `.env`
# con cuatro secretos en claro y la copia local de los respaldos. Quien se lleve
# el equipo se lleva las tres cosas.
#
# Esto comprueba lo que se puede comprobar desde dentro de la máquina. Lo que NO
# puede comprobar —y hay que probar a mano— es que el servidor vuelva solo
# después de un apagón: ver §5.1 del documento.
#
# SE CORRE EN LA MINI PC DE LA OFICINA. En una máquina de desarrollo va a salir
# rojo, y ahí eso no significa nada.
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
titulo() { printf '\n%s\n' "$1"; }

# -----------------------------------------------------------------------------
# ¿De qué dispositivo cuelga una ruta, y hay un `crypt` en su cadena?
# -----------------------------------------------------------------------------
# Con LVM sobre LUKS —lo que deja el instalador de Ubuntu— la cadena de `/` es:
#
#     /dev/mapper/vg-root   (lvm)
#       └ /dev/mapper/dm_crypt-0  (crypt)   <- esto es lo que se busca
#           └ /dev/nvme0n1p3      (part)
#               └ /dev/nvme0n1    (disk)
#
# `lsblk -s` recorre esa cadena hacia ARRIBA desde el dispositivo dado, así que
# basta con ver si aparece un tipo `crypt`.
#
# Se pide con `-P` —pares `NAME="x" TYPE="y"`— y no en columnas: en columnas
# lsblk DIBUJA el árbol, y el nombre viene con glifos `└─` pegados delante. Con
# `-P` no hay glifos que recortar, que es de donde salen los parseos que fallan
# solo en la máquina de alguien más.
cadena_de() {
    local fuente="$1"
    lsblk -nsPo NAME,TYPE "$fuente" 2>/dev/null
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

echo "Cifrado del disco del servidor"
printf '%s%s%s\n' "$GRIS" "(se revisa en la mini PC; en desarrollo esto sale rojo y da igual)" "$FIN"

# -----------------------------------------------------------------------------
titulo "La raíz"
# -----------------------------------------------------------------------------
RAIZ_DEV="$(findmnt -no SOURCE / 2>/dev/null)"
if [ -z "$RAIZ_DEV" ]; then
    falla "no pude averiguar de qué dispositivo cuelga /"
elif esta_cifrado "$RAIZ_DEV"; then
    bien "/ está sobre LUKS ($RAIZ_DEV)"
else
    falla "/ NO está cifrado ($RAIZ_DEV)" \
          "se hace al instalar Ubuntu; ver docs/SEGURIDAD-OPERATIVA.md §5.1"
fi

# -----------------------------------------------------------------------------
titulo "El intercambio (swap)"
# -----------------------------------------------------------------------------
# Importa más de lo que parece: PostgreSQL pagina, y el `.env` se lee a memoria.
# Un swap sin cifrar puede dejar en el disco, en claro, lo mismo que se cifró.
SWAPS="$(swapon --noheadings --show=NAME,TYPE 2>/dev/null)"
if [ -z "$SWAPS" ]; then
    avisa "no hay swap activo: nada que cifrar (y nada que pagine a disco)"
else
    while read -r nombre tipo; do
        [ -z "$nombre" ] && continue
        case "$tipo" in
        file)
            # Un archivo de swap hereda el cifrado del sistema donde vive.
            CONTENEDOR="$(findmnt -no SOURCE --target "$nombre" 2>/dev/null)"
            if [ -n "$CONTENEDOR" ] && esta_cifrado "$CONTENEDOR"; then
                bien "swap en archivo ($nombre), sobre un sistema cifrado"
            else
                falla "swap en archivo SIN cifrar: $nombre" \
                      "vive en $CONTENEDOR, que no está sobre LUKS"
            fi
            ;;
        *)
            if [ "${nombre#/dev/zram}" != "$nombre" ]; then
                bien "swap en zram ($nombre): es RAM comprimida, no toca el disco"
            elif esta_cifrado "$nombre"; then
                bien "swap en dispositivo cifrado ($nombre)"
            else
                falla "swap SIN cifrar: $nombre" \
                      "cífralo o quítalo; lo que pagine ahí queda en claro"
            fi
            ;;
        esac
    done <<< "$SWAPS"
fi

# -----------------------------------------------------------------------------
titulo "Las llaves, y el camino de vuelta"
# -----------------------------------------------------------------------------
if ! command -v cryptsetup >/dev/null 2>&1; then
    avisa "no hay cryptsetup: no pude revisar las llaves del volumen"
elif [ -z "${RAIZ_DEV:-}" ] || ! esta_cifrado "$RAIZ_DEV"; then
    avisa "sin volumen cifrado que revisar"
else
    LUKS="$(particion_luks "$RAIZ_DEV")"
    if [ -z "$LUKS" ] || [ ! -e "$LUKS" ]; then
        avisa "no ubiqué la partición LUKS detrás de $RAIZ_DEV"
    elif ! VOLCADO="$(cryptsetup luksDump "$LUKS" 2>/dev/null)"; then
        avisa "no pude leer $LUKS (¿hace falta sudo?)"
    else
        RANURAS="$(printf '%s' "$VOLCADO" | grep -cE '^[[:space:]]*[0-9]+: luks2')"

        # LOS DOS caminos del §5.1, no solo uno. `systemd-cryptenroll` deja un
        # token `systemd-tpm2`; `clevis luks bind`, uno `clevis`. Contar solo el
        # primero haría dos cosas mal en un equipo armado por el camino B: diría
        # que no abre solo —y sí abre—, y contaría la ranura del TPM como si fuera
        # una frase que alguien puede teclear. El segundo error es el grave: es el
        # que haría creer que hay camino de vuelta cuando no lo hay.
        AUTO="$(printf '%s' "$VOLCADO" \
                | grep -ciE '^[[:space:]]*[0-9]+: (systemd-tpm2|clevis)')"
        COMO="$(printf '%s' "$VOLCADO" \
                | grep -oiE '^[[:space:]]*[0-9]+: (systemd-tpm2|clevis)' \
                | awk '{print $2}' | sort -u | tr '\n' ' ')"

        if [ "$AUTO" -gt 0 ]; then
            bien "llave automática enrolada (${COMO% }): el servidor abre solo al arrancar"
        else
            avisa "sin llave automática: va a pedir la frase en CADA arranque"
            printf '        %s%s%s\n' "$GRIS" \
                "tras un apagón largo el servidor se queda abajo hasta que alguien vaya" "$FIN"
        fi

        # LA comprobación que importa de verdad, y la que se hace mal.
        #
        # `systemd-cryptenroll --tpm2-device` NO reemplaza la frase: AGREGA una
        # ranura con la llave sellada por el TPM. Así que un volumen bien armado
        # tiene al menos dos ranuras —la frase y el TPM— y la cuenta de ranuras
        # menos la de llaves del TPM dice cuántas quedan que una persona pueda
        # teclear.
        #
        # Si ese número es cero, alguien borró la frase después de enrolar el TPM.
        # Y entonces una actualización de BIOS que resetee el TPM —o cambiar la
        # tarjeta madre, o quitar la pila— deja el disco ILEGIBLE PARA SIEMPRE.
        # No hay otra llave. Es la forma más silenciosa de perder la operación
        # completa, y sale de seguir un tutorial hasta el paso que dice
        # «wipe-slot» creyendo que limpia algo.
        RECUPERABLES=$((RANURAS - AUTO))
        if [ "$RANURAS" -lt 1 ]; then
            falla "no encontré ranuras de llave en $LUKS" \
                  "revísalo a mano antes de confiar en esto"
        elif [ "$RECUPERABLES" -lt 1 ]; then
            falla "NO QUEDA NINGUNA FRASE: la única llave la tiene el TPM" \
                  "si el TPM se resetea, el disco no se vuelve a abrir. Agrega una frase ya: cryptsetup luksAddKey $LUKS"
        elif [ "$AUTO" -gt 0 ]; then
            bien "$RECUPERABLES frase(s) + $AUTO llave(s) automática(s): abre solo y hay camino de vuelta"
        else
            bien "$RECUPERABLES frase(s) de recuperación"
        fi
    fi
fi

# -----------------------------------------------------------------------------
titulo "Lo que el cifrado NO tapa: el .env con la máquina encendida"
# -----------------------------------------------------------------------------
# El disco cifrado protege el equipo APAGADO. Encendido —que es siempre— el
# `.env` es un archivo de texto con cuatro secretos, y lo único que lo separa de
# cualquier cuenta del sistema son sus permisos.
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
printf '\n'
if [ "$FALLAS" -eq 0 ]; then
    printf '%sNada que arreglar aquí.%s\n' "$VERDE" "$FIN"
else
    printf '%s%s cosa(s) que arreglar.%s\n' "$ROJO" "$FALLAS" "$FIN"
fi
printf '%sLo que esto NO prueba: que el servidor vuelva SOLO tras un apagón.%s\n' "$GRIS" "$FIN"
printf '%sEso se prueba desenchufándolo. Ver SEGURIDAD-OPERATIVA.md §5.1.%s\n' "$GRIS" "$FIN"
[ "$FALLAS" -eq 0 ] || exit 1
