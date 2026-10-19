"""Ajustar el inventario de una bodega o un camión de un jalón, contando (ADR 0002 §142).

«Desde el dashboard quiero poder modificar el inventario de los camiones y el
almacén de forma arbitraria, para hacer ajustes estos días que apenas empezaré
la operación.»

Se escribe **lo que hay** de cada artículo —no la diferencia: restar a mano es de
donde salen los errores que esto viene a corregir— y el sistema escribe, por cada
uno que cambió, el mismo ajuste de conteo que el de uno por uno
(`registrar_ajuste`, tipo `conteo`): con su folio, su asiento en el libro mayor y
su nota. El de un camión le llega al teléfono de su vendedor como diferencia y se
suma a lo que traiga (0032, 0037); el de una bodega no viaja a ninguno.

Lo que cuadra no se toca. Todo va en una transacción: o se guarda todo el conteo,
o nada. Cada ajuste se puede anular después en Operaciones (§119).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from app.infra.ajuste_de_existencia import existencia_con_candado, registrar_ajuste

TIPOS_AJUSTABLES = ("bodega", "camion")
NOTA_MINIMA = 10


class ConteoRechazado(Exception):
    """No se guardó nada; el mensaje dice por qué."""


@dataclass(frozen=True)
class ConteoAplicado:
    folios: list[str]
    articulos: int
    suben: Decimal
    bajan: Decimal
    mensaje: str


async def almacenes_ajustables(sesion) -> list[dict]:
    """Las bodegas y los camiones activos, con quién trae cada camión."""
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    "SELECT a.id, a.codigo, a.nombre, a.tipo, a.responsable_id, "
                    "       u.nombre AS responsable "
                    "  FROM almacenes a LEFT JOIN usuarios u ON u.id = a.responsable_id "
                    " WHERE a.activo AND a.tipo IN ('bodega', 'camion') "
                    " ORDER BY a.tipo, a.codigo"
                )
            )
        ).mappings()
    ]


async def catalogo_con_existencia(sesion, almacen_id: uuid.UUID, buscar: str = "") -> list[dict]:
    """Todo el catálogo activo —y lo inactivo que aún tenga existencia aquí, para
    poder dejarlo en cero—, por familia, con lo que dice el sistema y su caja."""
    condiciones = ["(p.activo OR COALESCE(x.cantidad, 0) <> 0)"]
    parametros: dict[str, object] = {"a": almacen_id}
    termino = (buscar or "").strip()
    if termino:
        condiciones.append("(p.nombre ILIKE :q OR p.sku ILIKE :q OR p.codigo_barras = :exacto)")
        parametros["q"] = f"%{termino}%"
        parametros["exacto"] = termino
    filas = await sesion.execute(
        text(
            f"""
            SELECT p.id AS producto_id, p.sku, p.nombre, p.unidad_base, p.activo,
                   cat.id AS familia_id, cat.nombre AS familia,
                   COALESCE(x.cantidad, 0) AS cantidad,
                   caja.unidad_codigo AS caja, caja.factor AS factor_caja
              FROM productos p
              LEFT JOIN categorias cat ON cat.id = p.categoria_id
              LEFT JOIN existencias x ON x.producto_id = p.id AND x.almacen_id = :a
              LEFT JOIN LATERAL (
                    SELECT u.unidad_codigo, u.factor FROM producto_unidades u
                     WHERE u.producto_id = p.id AND u.activo AND u.factor > 1
                     ORDER BY u.factor DESC LIMIT 1
              ) caja ON true
             WHERE {" AND ".join(condiciones)}
             -- Por familia, en el orden de la hoja de la dirección (§89).
             ORDER BY (cat.id IS NULL), cat.orden, cat.nombre, p.nombre
             LIMIT 2000
            """  # noqa: S608 — las condiciones son constantes del código
        ),
        parametros,
    )
    return [dict(f) for f in filas.mappings()]


async def aplicar_conteo(
    sesion,
    *,
    almacen_id: uuid.UUID,
    conteos: dict[uuid.UUID, Decimal],
    nota: str,
    quien: uuid.UUID,
) -> ConteoAplicado:
    """Deja cada artículo de `conteos` (en unidad base) en lo contado. No hace
    commit: quien llama decide la transacción."""
    almacen = next(
        (a for a in await almacenes_ajustables(sesion) if a["id"] == almacen_id), None
    )
    if almacen is None:
        raise ConteoRechazado("Elige una bodega o un camión.")
    limpia = " ".join((nota or "").split())
    if len(limpia) < NOTA_MINIMA:
        raise ConteoRechazado(
            f"Escribe la nota del conteo, con al menos {NOTA_MINIMA} letras: es lo que "
            "va a explicar, meses después, por qué cambió el inventario."
        )
    if not conteos:
        raise ConteoRechazado("Escribe lo que contaste de al menos un artículo.")
    if any(c < 0 for c in conteos.values()):
        raise ConteoRechazado("Lo contado no puede ser negativo.")
    nombres = {
        f.id: f.nombre
        for f in await sesion.execute(
            text("SELECT id, nombre FROM productos WHERE id = ANY(:ids)"),
            {"ids": list(conteos)},
        )
    }
    if len(nombres) != len(conteos):
        raise ConteoRechazado("Uno de los artículos ya no existe.")

    # Con candado y en orden: dos conteos a la vez no se pisan, y una venta que
    # entra en medio espera a que esto termine.
    cambios: list[tuple[uuid.UUID, Decimal, Decimal]] = []
    for producto_id in sorted(conteos, key=str):
        actual = await existencia_con_candado(sesion, almacen_id, producto_id)
        if conteos[producto_id] != actual:
            cambios.append((producto_id, actual, conteos[producto_id]))
    if not cambios:
        raise ConteoRechazado(
            "Todo lo que escribiste ya cuadra con el sistema: no hay nada que ajustar."
        )

    folios = []
    suben = bajan = Decimal(0)
    for producto_id, actual, contado in cambios:
        delta = contado - actual
        folios.append(
            await registrar_ajuste(
                sesion,
                almacen_id=almacen_id,
                producto_id=producto_id,
                es_camion=almacen["tipo"] == "camion",
                tipo="conteo",
                actual=actual,
                delta=delta,
                contado=contado,
                nota=limpia,
                quien=quien,
            )
        )
        if delta > 0:
            suben += delta
        else:
            bajan -= delta

    mensaje = (
        f"Se ajustaron {len(cambios)} artículo(s) en {almacen['nombre']}: "
        f"{_piezas(suben)} piezas de más y {_piezas(bajan)} de menos "
        f"({folios[0]}{' a ' + folios[-1] if len(folios) > 1 else ''})."
    )
    cuadran = len(conteos) - len(cambios)
    if cuadran:
        mensaje += f" {cuadran} ya cuadraban y no se tocaron."
    if almacen["tipo"] == "camion":
        mensaje += (
            f" Al teléfono de {almacen['responsable'] or 'su vendedor'} le llega al "
            "sincronizar."
        )
    return ConteoAplicado(
        folios=folios, articulos=len(cambios), suben=suben, bajan=bajan, mensaje=mensaje
    )


def _piezas(cantidad: Decimal) -> str:
    return f"{cantidad.normalize():f}"
