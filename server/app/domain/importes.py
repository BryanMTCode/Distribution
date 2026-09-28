"""La aritmética de una partida de venta.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTO ES UN MÓDULO Y NO UNA MULTIPLICACIÓN SUELTA
────────────────────────────────────────────────────────────────────────────
El importe de una línea se calcula en **tres lugares**: el teléfono al armar
el carrito, este servidor al recibir el sobre, y PostgreSQL en el CHECK de
`venta_partidas`. Si los tres no coinciden al centavo, la venta legítima de un
vendedor cae en revisión por un centavo de diferencia de redondeo — y en
cuanto eso pasa dos o tres veces, la bandera de revisión deja de significar
algo y se ignora.

Así que la regla se escribe una vez, aquí, y se replica textualmente:

    importe = redondear_medio_arriba(cantidad × precio_unitario, 2 decimales)

**Una sola vez, al final.** No se redondea el precio antes de multiplicar: una
caja de 24 piezas a $12.3333 vale $296.00, no $295.92.

**Medio hacia arriba**, explícito: el modo por omisión de `Decimal` en Python
es medio-al-par (`ROUND_HALF_EVEN`), que redondearía 0.125 → 0.12 mientras
PostgreSQL y el cliente Dart dan 0.13. Es exactamente la clase de divergencia
que no se nota hasta que hay miles de tickets.

────────────────────────────────────────────────────────────────────────────
Y POR QUÉ NO HAY UN PARÁMETRO DE DESCUENTO
────────────────────────────────────────────────────────────────────────────
Regla de negocio cerrada (ADR 0002 §7): **el vendedor no otorga descuentos en
la calle.** El precio unitario es el de la lista del cliente, sin excepción y
sin flujo de autorización. Un descuento no es un campo que se deje en cero
"por ahora": es un parámetro que no existe, para que no haya dónde escribirlo.

Módulo de dominio puro: sin imports de FastAPI ni de SQLAlchemy.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "CantidadInvalida",
    "cantidad_base",
    "importe_de_linea",
    "redondear_dinero",
]

_CENTAVO = Decimal("0.01")


class CantidadInvalida(ValueError):
    """Una cantidad o un precio que no puede formar parte de una venta."""


def redondear_dinero(valor: Decimal) -> Decimal:
    """A 2 decimales, medio hacia arriba. El único redondeo permitido."""
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def importe_de_linea(cantidad: Decimal, precio_unitario: Decimal) -> Decimal:
    """El importe de una partida. La multiplicación es exacta; el redondeo, uno.

    `cantidad` y `precio_unitario` son `Decimal` a propósito: con `float` la
    multiplicación ya trae error antes de redondear.
    """
    if isinstance(cantidad, float) or isinstance(precio_unitario, float):
        raise CantidadInvalida("la aritmética de una partida nunca usa float")
    if cantidad <= 0:
        raise CantidadInvalida(f"la cantidad debe ser positiva, llegó {cantidad}")
    if precio_unitario < 0:
        raise CantidadInvalida(f"el precio no puede ser negativo, llegó {precio_unitario}")
    return redondear_dinero(cantidad * precio_unitario)


def cantidad_base(cantidad: Decimal, factor_unidad: Decimal) -> Decimal:
    """Cuántas unidades base salen del camión por esta línea.

    Se congela en la partida porque es lo que se descontó del inventario: si
    mañana alguien corrige el factor de la caja en el catálogo, el movimiento
    de inventario de ayer no puede cambiar de tamaño.
    """
    if factor_unidad <= 0:
        raise CantidadInvalida(f"el factor debe ser positivo, llegó {factor_unidad}")
    return (cantidad * factor_unidad).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
