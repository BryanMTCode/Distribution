"""El andamio que comparten las pantallas del panel.

Existe para que `panel.py`, `productos.py` y `clientes.py` no se importen entre
sí: la navegación y el `render` los necesitan las tres, y ponerlos en cualquiera
de ellas obligaría a las otras dos a importarla.

────────────────────────────────────────────────────────────────────────────
LEER NÚMEROS QUE ESCRIBIÓ UNA PERSONA
────────────────────────────────────────────────────────────────────────────
Aquí viven los lectores de campos numéricos, y no son un detalle: un precio mal
leído es dinero mal cobrado en cinco camiones.

Las tres reglas que siguen:

1. **Nunca `float`.** `float("296.10")` ya no vale 296.10, y de ahí al centavo
   perdido hay un paso. Todo pasa por `Decimal` construido **desde el texto**.
2. **Se acepta lo que la gente escribe.** `$1,296.50` es lo que sale de copiar
   una celda de Excel. Se limpian el signo de pesos, las comas y los espacios
   antes de convertir; lo que no se limpia es un error que hay que decir.
3. **Un error se explica en español y con el valor que llegó.** "Cantidad
   inválida" no le dice a nadie qué corregir.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.sesion_web import token_csrf
from app.core.db import obtener_sesion

plantillas = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# La navegación se declara una vez: una pantalla que no está aquí no existe para
# quien usa el panel, por más que su ruta responda.
NAVEGACION = [
    ("/panel", "Tablero"),
    ("/panel/productos", "Productos"),
    ("/panel/clientes", "Clientes"),
    ("/panel/ventas", "Ventas"),
    ("/panel/cuarentena", "Cuarentena"),
]

SesionDep = Annotated[AsyncSession, Depends(obtener_sesion)]

# Cuatro decimales para precios, dos para dinero. La razón está en ADR 0002: una
# caja de 24 a $296.00 da $12.3333 la pieza, y a dos decimales las 24 piezas
# sumarían $295.92. El precio guarda cuatro; el importe redondea a dos una sola
# vez, al final.
DIEZMILESIMA = Decimal("0.0001")
CENTAVO = Decimal("0.01")


class CapturaInvalida(ValueError):
    """Lo que escribió la persona no se puede usar. El mensaje es para ella."""


def _limpiar(texto: str) -> str:
    return texto.strip().replace("$", "").replace(",", "").replace(" ", "")


def _a_decimal(texto: str | None, campo: str) -> Decimal | None:
    crudo = _limpiar(texto or "")
    if not crudo:
        return None
    try:
        valor = Decimal(crudo)
    except InvalidOperation as e:
        raise CapturaInvalida(f"{campo} no es un número: «{texto}».") from e
    if not valor.is_finite():
        raise CapturaInvalida(f"{campo} no es un número: «{texto}».")
    return valor


def leer_precio(texto: str | None, *, campo: str = "El precio") -> Decimal:
    """Un precio de lista, a cuatro decimales.

    Acepta `296`, `296.00`, `$1,296.50` y `12.3333`. Rechaza el vacío: un precio
    en blanco no es cero, es un precio que falta capturar, y guardarlo como cero
    haría que el vendedor regalara el producto.
    """
    valor = _a_decimal(texto, campo)
    if valor is None:
        raise CapturaInvalida(f"{campo} viene vacío.")
    if valor < 0:
        raise CapturaInvalida(f"{campo} no puede ser negativo.")
    if valor == 0:
        # Un precio en cero se vería idéntico a "regalado" en el ticket del
        # cliente. Si algún día hay producto gratis será una promoción, no un
        # precio de lista.
        raise CapturaInvalida(f"{campo} no puede ser cero.")
    if valor >= Decimal("1000000"):
        raise CapturaInvalida(f"{campo} parece un error de dedo: {valor}.")
    return valor.quantize(DIEZMILESIMA, rounding=ROUND_HALF_UP)


def leer_dinero(texto: str | None, *, campo: str = "El monto") -> Decimal:
    """Un monto de dinero, a dos decimales. El vacío vale cero.

    A diferencia de un precio, un límite de crédito en blanco **sí** significa
    cero: es el valor con el que nace todo cliente.
    """
    valor = _a_decimal(texto, campo)
    if valor is None:
        return Decimal("0.00")
    if valor < 0:
        raise CapturaInvalida(f"{campo} no puede ser negativo.")
    if valor >= Decimal("100000000"):
        raise CapturaInvalida(f"{campo} parece un error de dedo: {valor}.")
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def leer_factor(texto: str | None) -> Decimal:
    """Cuántas unidades base trae una presentación: el 24 de "caja de 24".

    Se guarda con cuatro decimales porque la columna los tiene, pero en este
    negocio siempre es entero: no hay producto a granel (ADR 0002), así que no
    existe la media caja.
    """
    valor = _a_decimal(texto, "El número de piezas")
    if valor is None:
        raise CapturaInvalida("Falta cuántas piezas trae la presentación.")
    if valor <= 0:
        raise CapturaInvalida("La presentación tiene que traer al menos una pieza.")
    if valor != valor.to_integral_value():
        raise CapturaInvalida(
            f"Una presentación trae piezas completas, no {valor}. "
            "Ningún producto se vende a granel."
        )
    if valor > 10000:
        raise CapturaInvalida(f"{valor} piezas por presentación parece un error de dedo.")
    return valor.quantize(DIEZMILESIMA)


def leer_entero(texto: str | None, *, campo: str, maximo: int) -> int:
    valor = _a_decimal(texto, campo)
    if valor is None:
        return 0
    if valor != valor.to_integral_value():
        raise CapturaInvalida(f"{campo} tiene que ser un número entero, no {valor}.")
    if valor < 0:
        raise CapturaInvalida(f"{campo} no puede ser negativo.")
    if valor > maximo:
        raise CapturaInvalida(f"{campo} no puede pasar de {maximo}.")
    return int(valor)


def texto_o_nulo(valor: str | None, *, maximo: int = 200) -> str | None:
    """Una cadena vacía en un formulario es "no capturado", no la cadena vacía.

    Guardar `''` en vez de `NULL` rompe los `IS NULL` de las consultas y hace que
    un campo sin capturar se vea como capturado con nada.
    """
    limpio = (valor or "").strip()
    if not limpio:
        return None
    return limpio[:maximo]


def dinero(valor) -> str:
    """Para mostrar. El dinero se calcula en `Decimal` y se formatea al final."""
    return f"${Decimal(valor or 0):,.2f}"


def precio_corto(valor) -> str:
    """`296.0000` → `296.00`, y `12.3333` se queda como está.

    El precio guarda cuatro decimales porque los necesita para que la caja y la
    pieza cuadren, pero imprimir `$296.0000` en una tabla hace que quien la lee
    dude de si le falta algo. Se muestran los decimales que aportan, con un mínimo
    de dos: los centavos siempre se ven.
    """
    entero, _, fraccion = f"{Decimal(str(valor)):.4f}".partition(".")
    fraccion = fraccion.rstrip("0").ljust(2, "0")
    return f"{entero}.{fraccion}"


def sin_decimales(valor) -> str:
    """El factor de una presentación: `24.0000` → `24`. Nunca hay media caja."""
    return f"{Decimal(str(valor)).to_integral_value():,}"


plantillas.env.filters["precio"] = precio_corto
plantillas.env.filters["entero"] = sin_decimales


def render(
    peticion: Request,
    plantilla: str,
    contexto: dict,
    *,
    actor=None,
    seccion: str = "",
) -> HTMLResponse:
    return plantillas.TemplateResponse(
        peticion,
        plantilla,
        {
            **contexto,
            "actor": actor,
            "seccion": seccion,
            "navegacion": NAVEGACION,
            "csrf": token_csrf(peticion),
        },
    )
