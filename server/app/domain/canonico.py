"""Serialización canónica y hash de payloads.

Esta es la pieza más delicada de tener dos lenguajes: el `hash_payload` que
calcula Dart y el que verifica Python deben coincidir **byte a byte**. Si no,
la alarma contra manipulación (mismo `operacion_id` con payload distinto) se
convierte en un generador de falsos positivos y se acaba desactivando.

Los cuatro lugares donde Dart y Python divergen en silencio están cerrados
por reglas explícitas, no por convención:

1. **Orden de claves** — se ordenan por punto de código Unicode.
2. **`Decimal` vs `double`** — los flotantes están PROHIBIDOS en el payload.
   El dinero y las cantidades viajan como *string* con escala fija.
3. **Fechas** — RFC 3339 en UTC, con exactamente 3 decimales y sufijo `Z`.
4. **`null` vs clave ausente** — las claves con valor nulo se OMITEN.

Módulo de dominio puro: sin imports de FastAPI ni de SQLAlchemy.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

__all__ = [
    "ESCALA_CANTIDAD",
    "ESCALA_DINERO",
    "PayloadNoCanonico",
    "a_texto_canonico",
    "formatear_cantidad",
    "formatear_dinero",
    "formatear_instante",
    "hash_payload",
]

ESCALA_DINERO = 2
ESCALA_CANTIDAD = 3

# 'dinero' y 'cantidad' se validan con estas formas exactas: sin separador de
# miles, sin '+', sin notación exponencial, con la escala fija completa.
_RE_DINERO = re.compile(rf"^-?\d+\.\d{{{ESCALA_DINERO}}}$")
_RE_CANTIDAD = re.compile(rf"^-?\d+\.\d{{{ESCALA_CANTIDAD}}}$")
_RE_INSTANTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class PayloadNoCanonico(ValueError):
    """El payload contiene algo que no admite una representación canónica."""


def formatear_dinero(valor: Decimal | int | str) -> str:
    """Dinero como string con exactamente 2 decimales.

    Nunca aceptar `float` aquí: es justo el tipo que pierde centavos.
    """
    if isinstance(valor, float):
        raise PayloadNoCanonico("el dinero nunca se representa como float")
    d = Decimal(valor)
    return f"{d.quantize(Decimal('1.' + '0' * ESCALA_DINERO)):f}"


def formatear_cantidad(valor: Decimal | int | str) -> str:
    """Cantidad como string con exactamente 3 decimales (fracción de caja, granel)."""
    if isinstance(valor, float):
        raise PayloadNoCanonico("las cantidades nunca se representan como float")
    d = Decimal(valor)
    return f"{d.quantize(Decimal('1.' + '0' * ESCALA_CANTIDAD)):f}"


def formatear_instante(momento: datetime) -> str:
    """RFC 3339 en UTC con exactamente 3 decimales y sufijo 'Z'.

    Un datetime sin zona horaria es un error: el reloj del dispositivo y el del
    servidor están en juego, y una fecha ambigua entre ambos es indefendible.
    """
    if momento.tzinfo is None:
        raise PayloadNoCanonico("el instante debe traer zona horaria explícita")
    utc = momento.astimezone(UTC)
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{utc.microsecond // 1000:03d}Z"


def _escapar(texto: str) -> str:
    salida = ['"']
    for ch in texto:
        if ch in _ESCAPES:
            salida.append(_ESCAPES[ch])
        elif ch < "\x20":
            salida.append(f"\\u{ord(ch):04x}")
        else:
            # El resto se emite literal en UTF-8: nada de \uXXXX para acentos
            # ni emoji. Dart hace lo mismo por defecto.
            salida.append(ch)
    salida.append('"')
    return "".join(salida)


def _canonizar(valor: Any, ruta: str) -> str:
    if valor is True:
        return "true"
    if valor is False:
        return "false"
    if isinstance(valor, str):
        return _escapar(valor)
    if isinstance(valor, int):
        # bool ya quedó atrapado arriba. Los enteros van sin comillas.
        return str(valor)
    if isinstance(valor, float):
        raise PayloadNoCanonico(
            f"{ruta}: los float están prohibidos en el payload; "
            "usa string con escala fija (ver formatear_dinero/formatear_cantidad)"
        )
    if isinstance(valor, Decimal):
        raise PayloadNoCanonico(
            f"{ruta}: convierte el Decimal a string con escala fija antes de canonizar"
        )
    if isinstance(valor, datetime):
        raise PayloadNoCanonico(f"{ruta}: convierte el datetime con formatear_instante()")
    if isinstance(valor, Mapping):
        partes = []
        for clave in sorted(valor.keys()):
            if not isinstance(clave, str):
                raise PayloadNoCanonico(f"{ruta}: las claves deben ser string")
            interno = valor[clave]
            if interno is None:
                continue  # regla 4: las claves nulas se omiten
            partes.append(f"{_escapar(clave)}:{_canonizar(interno, f'{ruta}.{clave}')}")
        return "{" + ",".join(partes) + "}"
    if isinstance(valor, Sequence):
        # El orden de un arreglo SÍ es significativo: las partidas de una venta
        # llevan número de línea y el ticket impreso las muestra en ese orden.
        return "[" + ",".join(
            _canonizar(v, f"{ruta}[{i}]") for i, v in enumerate(valor)
        ) + "]"
    if valor is None:
        raise PayloadNoCanonico(f"{ruta}: null solo es válido como valor de una clave")
    raise PayloadNoCanonico(f"{ruta}: tipo no canonizable ({type(valor).__name__})")


def a_texto_canonico(payload: Mapping[str, Any]) -> str:
    """Forma canónica del payload. Determinista en Python y en Dart."""
    if not isinstance(payload, Mapping):
        raise PayloadNoCanonico("el payload raíz debe ser un objeto")
    return _canonizar(payload, "$")


def hash_payload(payload: Mapping[str, Any]) -> str:
    """SHA-256 hexadecimal en minúsculas de la forma canónica."""
    return hashlib.sha256(a_texto_canonico(payload).encode("utf-8")).hexdigest()


def validar_dinero(texto: str) -> str:
    if not _RE_DINERO.match(texto):
        raise PayloadNoCanonico(f"dinero mal formado: {texto!r} (se esperaba p. ej. '250.00')")
    return texto


def validar_cantidad(texto: str) -> str:
    if not _RE_CANTIDAD.match(texto):
        raise PayloadNoCanonico(f"cantidad mal formada: {texto!r} (se esperaba p. ej. '12.000')")
    return texto


def validar_instante(texto: str) -> str:
    if not _RE_INSTANTE.match(texto):
        raise PayloadNoCanonico(
            f"instante mal formado: {texto!r} (se esperaba '2026-09-15T03:14:07.123Z')"
        )
    return texto
