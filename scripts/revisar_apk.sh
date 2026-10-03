#!/usr/bin/env bash
# =============================================================================
# Qué salió del `make apk`, dicho en voz alta
# =============================================================================
# Un APK de producción tiene tres cosas que no se ven mirando el archivo y que
# deciden si sirve o si va a causar un problema dentro de tres meses:
#
#   1. CON QUÉ LLAVE ESTÁ FIRMADO. Si es la de depuración, se instala igual y el
#      día que haya que actualizar no se podrá: Android exige la misma firma, y
#      el único camino sería desinstalar — lo que borra la base local del
#      vendedor con lo que no haya subido.
#   2. QUÉ versionCode TRAE. Android rechaza instalar encima uno MENOR que el
#      instalado, y con el mismo número sí reinstala — lo que deja dos APK
#      distintos indistinguibles con el teléfono en la mano.
#   3. A QUÉ SERVIDOR APUNTA. Se fijó al compilar y no hay forma de verlo desde
#      la app, así que se imprime aquí, que es el único momento en que se sabe.
#
# Esto no bloquea nada: el build ya terminó. Imprime lo que hace falta ver antes
# de repartir el archivo, y marca lo que está mal.
set -uo pipefail

BASE_URL="${1:-}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK="$RAIZ/mobile/app/build/app/outputs/flutter-apk/app-release.apk"

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
}

if [ ! -f "$APK" ]; then
    falla "no hay APK en $APK"
    exit 1
fi

echo "APK de producción"
echo
printf '  %s%s%s\n' "$GRIS" "$APK" "$FIN"
printf '  %s\n' "$(du -h "$APK" | cut -f1)"
echo

# -----------------------------------------------------------------------------
# La versión, leída del pubspec
# -----------------------------------------------------------------------------
VERSION="$(grep -m1 '^version:' "$RAIZ/mobile/app/pubspec.yaml" | sed 's/^version:[[:space:]]*//')"
CODIGO="${VERSION##*+}"
if [ "$CODIGO" = "$VERSION" ]; then
    falla "pubspec.yaml dice «version: $VERSION», sin «+N»"
    printf '        %sel versionCode no sube solo: ponle +1, +2, … y súbelo en cada APK%s\n' "$GRIS" "$FIN"
else
    bien "versión $VERSION  (versionCode $CODIGO)"
    printf '        %sAndroid rechaza instalar encima uno MENOR que el instalado.%s\n' "$GRIS" "$FIN"
    printf '        %sCon el mismo numero reinstala, pero entonces no hay forma de%s\n' "$GRIS" "$FIN"
    printf '        %ssaber que version trae cada equipo. Sube el +N en cada reparto.%s\n' "$GRIS" "$FIN"
fi

[ -n "$BASE_URL" ] && bien "servidor $BASE_URL"

# -----------------------------------------------------------------------------
# La firma
# -----------------------------------------------------------------------------
# `apksigner` viene en las build-tools del SDK de Android y no siempre está en
# el PATH. Se busca, y si no aparece se dice — en vez de callar y dejar creer
# que la firma se revisó.
APKSIGNER="$(command -v apksigner || true)"
if [ -z "$APKSIGNER" ] && [ -n "${ANDROID_HOME:-}" ]; then
    # `sort -V`: con `sort` a secas, 9.0.0 ganaría a 34.0.0.
    APKSIGNER="$(ls -1 "$ANDROID_HOME"/build-tools/*/apksigner 2>/dev/null | sort -V | tail -1)"
fi

if [ -z "$APKSIGNER" ]; then
    avisa "no encontré apksigner: no pude revisar con qué llave se firmó"
    printf '        %sestá en <SDK>/build-tools/<version>/apksigner, o exporta ANDROID_HOME%s\n' "$GRIS" "$FIN"
else
    CERTS="$("$APKSIGNER" verify --print-certs "$APK" 2>&1)"
    if printf '%s' "$CERTS" | grep -qi "CN=Android Debug"; then
        falla "está firmado con la LLAVE DE DEPURACIÓN — no lo reparta"
        printf '        %sesa llave es distinta en cada máquina: el día que no coincida%s\n' "$GRIS" "$FIN"
        printf '        %shabrá que desinstalar, y eso borra la base local del vendedor%s\n' "$GRIS" "$FIN"
        exit 1
    fi

    HUELLA="$(printf '%s' "$CERTS" | grep -i 'SHA-256 digest' | head -1 | sed 's/.*: *//')"
    SUJETO="$(printf '%s' "$CERTS" | grep -i 'Signer #1 certificate DN' | head -1 | sed 's/.*: *//')"
    bien "firmado con una llave de producción"
    [ -n "$SUJETO" ] && printf '        %s%s%s\n' "$GRIS" "$SUJETO" "$FIN"
    if [ -n "$HUELLA" ]; then
        printf '        %sSHA-256 del certificado: %s%s\n' "$GRIS" "$HUELLA" "$FIN"
        printf '        %sApúntala la primera vez: tiene que ser LA MISMA en cada APK.%s\n' "$GRIS" "$FIN"
    fi
fi

echo
echo "Instalar en un teléfono conectado:"
printf '  %sadb install -r %s%s\n' "$GRIS" "$APK" "$FIN"
echo
printf '%sAntes de repartirlo: pruébalo en un teléfono que YA tenga la versión%s\n' "$GRIS" "$FIN"
printf '%santerior instalada. Eso es lo único que comprueba de verdad que la firma%s\n' "$GRIS" "$FIN"
printf '%sy el versionCode permiten actualizar sin desinstalar.%s\n' "$GRIS" "$FIN"

# Sale distinto de cero si algo quedó mal: `make apk` lo propaga, y así un FALLA
# no se puede pasar por alto desde otro script.
[ "$FALLAS" -eq 0 ] || exit 1
