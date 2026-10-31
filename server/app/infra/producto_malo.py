"""El producto malo que el vendedor tiene que regresar a la bodega (ADR 0002 §196).

«Si Federico tiene 4 malas, quiero llevar control de esas para que las regrese al
almacén.»

En un cambio físico (§54) el vendedor le da a la tienda producto bueno y se lleva
el malo: el bueno sale de su camión con documento, y el malo se quedaba sin
nombre. Desde ahora queda POR REGRESAR a nombre del vendedor:

    por regresar = lo cambiado (cambios físicos confirmados)
                 − lo que la oficina recibió en la bodega
                 − lo que se le cargó a su cuenta

No se guarda: se calcula. Un cambio eliminado deja de contar solo, y los cambios
de antes de esta pantalla también cuentan.

· **Recibir** mete lo que entregó al almacén «Producto malo» de la bodega con un
  ajuste: se ve en Operaciones y ahí se elimina, y eliminarlo lo deja pendiente
  otra vez. De ese almacén se tira o se le regresa al proveedor con una salida.
· **Cargar a su cuenta** se lo cobra a costo (§54), con un cargo en su cuenta. Un
  cargo no se borra: si fue un error, se condona en su cuenta.

Lo que no entrega se queda pendiente: nada se le cobra solo.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text

from app.infra.ajuste_de_existencia import (
    UnaSolaOperacion,
    existencia_con_candado,
    registrar_ajuste,
)
from app.infra.recorrer_dias import hoy_en_mazatlan

CODIGO = "PRODUCTO_MALO"
NOMBRE = "Producto malo"
CENTAVO = Decimal("0.01")


class NoSePuede(Exception):
    """No se puede así; el mensaje dice por qué y qué hacer."""


SQL_POR_REGRESAR = """
WITH cambios AS (
    SELECT m.vendedor_id, d.producto_id, sum(d.cantidad_base) AS cambiadas,
           min(m.fecha_operativa) AS desde
      FROM mermas m
      JOIN merma_detalle d ON d.merma_id = m.id
     WHERE m.tipo = 'cambio' AND m.estado = 'confirmada'
     GROUP BY m.vendedor_id, d.producto_id
), resuelto AS (
    SELECT r.vendedor_id, r.producto_id,
           COALESCE(sum(r.cantidad) FILTER (WHERE r.como = 'recibido'), 0) AS recibidas,
           COALESCE(sum(r.cantidad) FILTER (WHERE r.como = 'cargado'), 0) AS cargadas
      FROM producto_malo_resuelto r
      LEFT JOIN ajustes_camion a ON a.id = r.ajuste_id
     -- Lo recibido cuyo ajuste se eliminó en Operaciones vuelve a estar pendiente.
     WHERE r.como = 'cargado'
        OR NOT EXISTS (SELECT 1 FROM anulaciones an
                        WHERE an.tipo = 'ajuste'
                          AND an.documento_id = COALESCE(a.pareja_id, a.id))
     GROUP BY r.vendedor_id, r.producto_id
)
SELECT c.vendedor_id, u.nombre AS vendedor, c.producto_id, p.sku, p.nombre AS articulo,
       c.cambiadas, COALESCE(r.recibidas, 0) AS recibidas,
       COALESCE(r.cargadas, 0) AS cargadas,
       c.cambiadas - COALESCE(r.recibidas, 0) - COALESCE(r.cargadas, 0) AS pendientes,
       c.desde, pc.costo_promedio AS costo
  FROM cambios c
  JOIN usuarios u ON u.id = c.vendedor_id
  JOIN productos p ON p.id = c.producto_id
  LEFT JOIN resuelto r ON r.vendedor_id = c.vendedor_id AND r.producto_id = c.producto_id
  LEFT JOIN producto_costos pc ON pc.producto_id = c.producto_id
 ORDER BY u.nombre, p.nombre
"""

SQL_CAMBIOS = """
SELECT m.vendedor_id, m.fecha_operativa AS fecha, m.folio_local AS folio,
       c.nombre_comercial AS tienda, p.nombre AS articulo, d.cantidad_base AS piezas
  FROM mermas m
  JOIN merma_detalle d ON d.merma_id = m.id
  JOIN productos p ON p.id = d.producto_id
  LEFT JOIN clientes c ON c.id = m.cliente_id
 WHERE m.tipo = 'cambio' AND m.estado = 'confirmada'
 ORDER BY m.fecha_operativa DESC, m.folio_local, p.nombre
 LIMIT 300
"""

SQL_RESUELTO = """
SELECT r.registrado_en, r.fecha_operativa AS fecha, u.nombre AS vendedor,
       p.nombre AS articulo, r.cantidad, r.como, r.importe, q.nombre AS quien,
       a.folio, COALESCE(a.pareja_id, a.id) AS operacion_id,
       (an.id IS NOT NULL) AS eliminado, r.vendedor_id
  FROM producto_malo_resuelto r
  JOIN usuarios u ON u.id = r.vendedor_id
  JOIN productos p ON p.id = r.producto_id
  JOIN usuarios q ON q.id = r.registrado_por
  LEFT JOIN ajustes_camion a ON a.id = r.ajuste_id
  LEFT JOIN anulaciones an
         ON an.tipo = 'ajuste' AND an.documento_id = COALESCE(a.pareja_id, a.id)
 ORDER BY r.registrado_en DESC
 LIMIT 200
"""


async def almacen(sesion, *, crear: bool = False) -> uuid.UUID | None:
    """El almacén «Producto malo» de la bodega. Se crea la primera vez que se
    recibe algo: sin nada recibido no hace falta que aparezca en ninguna lista."""
    existente = (
        await sesion.execute(text("SELECT id FROM almacenes WHERE producto_malo"))
    ).scalar_one_or_none()
    if existente is not None or not crear:
        return existente
    return (
        await sesion.execute(
            text(
                "INSERT INTO almacenes (codigo, nombre, tipo, producto_malo) "
                "VALUES (:c, :n, 'merma', true) "
                "ON CONFLICT (codigo) DO UPDATE SET producto_malo = true, activo = true "
                "RETURNING id"
            ),
            {"c": CODIGO, "n": NOMBRE},
        )
    ).scalar_one()


async def por_regresar(sesion) -> list[dict]:
    """Por vendedor y artículo: lo cambiado, lo recibido, lo cargado y lo que
    falta. También los ya resueltos (falta 0), para la historia."""
    return [dict(r) for r in (await sesion.execute(text(SQL_POR_REGRESAR))).mappings()]


async def cambios(sesion) -> list[dict]:
    """Los cambios físicos de donde sale lo pendiente: cuándo, en qué tienda y qué."""
    return [dict(r) for r in (await sesion.execute(text(SQL_CAMBIOS))).mappings()]


async def resuelto(sesion) -> list[dict]:
    """Lo recibido y lo cargado, lo último primero."""
    return [dict(r) for r in (await sesion.execute(text(SQL_RESUELTO))).mappings()]


async def en_la_bodega(sesion) -> list[dict]:
    """Lo que hay en el almacén «Producto malo»: lo entregado que todavía no se
    tira ni se le regresa al proveedor."""
    almacen_id = await almacen(sesion)
    if almacen_id is None:
        return []
    return [
        dict(r)
        for r in (
            await sesion.execute(
                text(
                    "SELECT p.sku, p.nombre AS articulo, e.cantidad "
                    "  FROM existencias e JOIN productos p ON p.id = e.producto_id "
                    " WHERE e.almacen_id = :a AND e.cantidad <> 0 ORDER BY p.nombre"
                ),
                {"a": almacen_id},
            )
        ).mappings()
    ]


async def _lo_que_debe(sesion, vendedor_id, cantidades: dict) -> tuple[str, dict]:
    """El nombre del vendedor y lo pendiente de cada artículo pedido. Revisa que
    no se resuelva más de lo que tiene por regresar. Con candado por vendedor:
    dos clics a la vez no resuelven dos veces lo mismo."""
    if not cantidades:
        raise NoSePuede("Escribe cuántas piezas de algún artículo.")
    await sesion.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('producto_malo:' || :v))"),
        {"v": str(vendedor_id)},
    )
    renglones = {
        r["producto_id"]: r for r in await por_regresar(sesion)
        if r["vendedor_id"] == vendedor_id
    }
    nombre = (
        await sesion.execute(text("SELECT nombre FROM usuarios WHERE id = :v"), {"v": vendedor_id})
    ).scalar_one_or_none()
    if nombre is None:
        raise NoSePuede("Ese vendedor no existe.")
    for producto_id, cantidad in cantidades.items():
        r = renglones.get(producto_id)
        pendiente = Decimal(r["pendientes"]) if r else Decimal(0)
        if cantidad > pendiente:
            articulo = r["articulo"] if r else "ese artículo"
            raise NoSePuede(
                f"{nombre} tiene {pendiente.normalize():f} de {articulo} por regresar, "
                f"no {cantidad.normalize():f}."
            )
    return nombre, renglones


async def recibir(sesion, *, vendedor_id, cantidades: dict, quien) -> str:
    """Lo que el vendedor entregó en la bodega: entra al almacén «Producto malo»
    con un ajuste —una sola operación aunque sean varios artículos (§146)— y deja
    de estar pendiente. Devuelve el folio. No hace commit."""
    nombre, _ = await _lo_que_debe(sesion, vendedor_id, cantidades)
    almacen_id = await almacen(sesion, crear=True)
    operacion = UnaSolaOperacion()
    hoy = hoy_en_mazatlan()
    for producto_id, cantidad in sorted(cantidades.items(), key=lambda par: str(par[0])):
        actual = await existencia_con_candado(sesion, almacen_id, producto_id)
        folio = await registrar_ajuste(
            sesion,
            almacen_id=almacen_id,
            producto_id=producto_id,
            es_camion=False,
            tipo="entrada",
            actual=actual,
            delta=cantidad,
            contado=None,
            nota=f"Lo entregó {nombre}: producto malo de sus cambios físicos.",
            quien=quien,
            operacion=operacion,
        )
        ajuste_id = (
            await sesion.execute(
                text("SELECT id FROM ajustes_camion WHERE folio = :f"), {"f": folio}
            )
        ).scalar_one()
        await sesion.execute(
            text(
                "INSERT INTO producto_malo_resuelto "
                "  (vendedor_id, producto_id, cantidad, como, ajuste_id, fecha_operativa, "
                "   registrado_por) "
                "VALUES (:v, :p, :c, 'recibido', :a, :hoy, :quien)"
            ),
            {"v": vendedor_id, "p": producto_id, "c": cantidad, "a": ajuste_id,
             "hoy": hoy, "quien": quien},
        )
    return operacion.folio or ""


async def cargar(sesion, *, vendedor_id, cantidades: dict, quien) -> Decimal:
    """Lo que no entregó, a su cuenta y a costo (§54): un cargo con el desglose
    que ve el vendedor. Devuelve el importe. No hace commit."""
    _, renglones = await _lo_que_debe(sesion, vendedor_id, cantidades)
    sin_costo = [
        renglones[p]["articulo"] for p in cantidades if renglones[p]["costo"] is None
    ]
    if sin_costo:
        raise NoSePuede(
            f"Sin costo capturado: {', '.join(sin_costo)}. Captúralo en Productos → "
            "Costos y vuelve a cargarlo: lo que no tiene costo no se cobra en cero."
        )
    detalle = []
    total = Decimal(0)
    importes = {}
    for producto_id, cantidad in cantidades.items():
        r = renglones[producto_id]
        importe = (cantidad * Decimal(r["costo"])).quantize(CENTAVO, ROUND_HALF_UP)
        importes[producto_id] = importe
        total += importe
        detalle.append(
            {
                "producto_id": str(producto_id),
                "sku": r["sku"],
                "nombre": r["articulo"],
                "cantidad": str(cantidad),
                "costo": str(r["costo"]),
                "importe": str(importe),
            }
        )
    if total <= 0:
        raise NoSePuede("Con costo en cero no hay nada que cargar.")
    hoy = hoy_en_mazatlan()
    cargo_id = (
        await sesion.execute(
            text(
                """
                INSERT INTO cuenta_vendedor
                  (vendedor_id, tipo, origen, importe, fecha, concepto, detalle,
                   registrado_por, registrado_en)
                VALUES (:v, 'cargo', 'cargo_manual', :importe, :hoy, :concepto,
                        CAST(:detalle AS jsonb), :quien, :ahora)
                RETURNING id
                """
            ),
            {
                "v": vendedor_id,
                "importe": total,
                "hoy": hoy,
                "concepto": "Producto malo de cambios físicos que no entregó en la bodega",
                "detalle": json.dumps(detalle, ensure_ascii=False),
                "quien": quien,
                "ahora": datetime.now(UTC),
            },
        )
    ).scalar_one()
    for producto_id, cantidad in cantidades.items():
        await sesion.execute(
            text(
                "INSERT INTO producto_malo_resuelto "
                "  (vendedor_id, producto_id, cantidad, como, cargo_id, importe, "
                "   fecha_operativa, registrado_por) "
                "VALUES (:v, :p, :c, 'cargado', :cargo, :importe, :hoy, :quien)"
            ),
            {"v": vendedor_id, "p": producto_id, "c": cantidad, "cargo": cargo_id,
             "importe": importes[producto_id], "hoy": hoy, "quien": quien},
        )
    return total
