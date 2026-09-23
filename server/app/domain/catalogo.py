"""Reglas del catálogo.

La conversión entre presentaciones es donde nace el descuadre clásico del DSD:
se carga el camión en cajas, se vende en piezas, y al liquidar no cuadra nada.
Todo el inventario se lleva en la **unidad base** del producto, y aquí se
valida que esa conversión sea consistente antes de que exista un solo
movimiento.

Módulo de dominio puro: sin imports de FastAPI ni de SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["UnidadVendible", "UnidadesInvalidas", "a_unidad_base", "validar_unidades"]


class UnidadesInvalidas(ValueError):
    """La configuración de presentaciones haría imposible cuadrar el inventario."""


@dataclass(frozen=True)
class UnidadVendible:
    codigo: str
    factor: Decimal      # cuántas unidades base contiene
    es_default: bool = False


def validar_unidades(unidad_base: str, unidades: list[UnidadVendible]) -> None:
    """Verifica que las presentaciones permitan cuadrar el inventario.

    Cuatro reglas, y cada una corresponde a una forma real de descuadrar:

    1. La unidad base tiene que estar entre las vendibles. Si no, el inventario
       se lleva en una unidad en la que nadie puede vender ni cargar.
    2. Su factor tiene que ser exactamente 1. Un factor distinto significa que
       "una unidad base" contiene varias unidades base.
    3. Exactamente una presentación por defecto, o el vendedor abre el producto
       y la app no sabe qué mostrar.
    4. Sin factores repetidos: dos presentaciones del mismo tamaño con nombres
       distintos hacen imposible auditar qué se vendió realmente.
    """
    if not unidades:
        raise UnidadesInvalidas("el producto necesita al menos una presentación vendible")

    codigos = [u.codigo for u in unidades]
    if len(codigos) != len(set(codigos)):
        raise UnidadesInvalidas("hay presentaciones repetidas")

    base = next((u for u in unidades if u.codigo == unidad_base), None)
    if base is None:
        raise UnidadesInvalidas(
            f"la unidad base '{unidad_base}' debe estar entre las presentaciones vendibles"
        )
    if base.factor != Decimal("1"):
        raise UnidadesInvalidas(
            f"la unidad base '{unidad_base}' debe tener factor 1, no {base.factor}"
        )

    for u in unidades:
        if u.factor <= 0:
            raise UnidadesInvalidas(f"la presentación '{u.codigo}' tiene factor {u.factor}")

    defaults = [u.codigo for u in unidades if u.es_default]
    if len(defaults) != 1:
        raise UnidadesInvalidas(
            f"debe haber exactamente una presentación por defecto, hay {len(defaults)}"
        )

    factores = [u.factor for u in unidades]
    if len(factores) != len(set(factores)):
        raise UnidadesInvalidas("dos presentaciones no pueden tener el mismo factor")


def a_unidad_base(cantidad: Decimal, factor: Decimal) -> Decimal:
    """Convierte a unidad base. Es lo que se congela en cada partida de venta:
    un cambio de empaque no debe reescribir la historia."""
    if factor <= 0:
        raise UnidadesInvalidas(f"factor inválido: {factor}")
    return cantidad * factor
