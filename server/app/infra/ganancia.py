"""La ganancia de lo que se vendió (ADR 0002 §180).

«Quiero también la ganancia de lo que ya se vendió»: por vendedor, por día y por
artículo, lo vendido menos lo que costó.

────────────────────────────────────────────────────────────────────────────
CON EL COSTO DE CUANDO SE VENDIÓ
────────────────────────────────────────────────────────────────────────────
Cada renglón de venta guarda el costo que tenía su artículo al venderse
(`costo_de_lo_vendido`, migración 0071). Lo vendido antes de esa migración, o lo
que no tenía costo en su momento, se calcula con el costo de hoy: es el mejor
dato que hay, y el que se capturó después para eso.

Un artículo que no tiene costo ni entonces ni ahora no entra en la ganancia: se
dice cuánto se vendió así, aparte. Sumarlo como si no costara nada haría parecer
que se ganó más de lo que se ganó.

Lo vendido es lo de cada renglón —su importe, ya con su descuento— de las
ventas confirmadas de la ruta y las de mayoreo de la oficina; lo eliminado en
Operaciones no cuenta, como en el resto del dashboard.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text

from app.infra import tarjeta

MAYOREO = "Mayoreo (oficina)"

# Por día, vendedor y artículo: piezas, lo vendido, lo que costó lo que tenía
# costo, y lo vendido de lo que no lo tenía.
SQL_LO_VENDIDO = f"""
WITH renglones AS (
    SELECT v.fecha_operativa AS dia, u.nombre AS vendedor, vp.producto_id,
           vp.cantidad_base AS piezas, vp.importe,
           COALESCE(cv.costo_unitario, pc.costo_promedio) AS costo_unitario
      FROM ventas v
      JOIN venta_partidas vp ON vp.venta_id = v.id
      JOIN usuarios u ON u.id = v.vendedor_id
      LEFT JOIN costo_de_lo_vendido cv ON cv.partida_id = vp.id
      LEFT JOIN producto_costos pc ON pc.producto_id = vp.producto_id
     WHERE v.estado = 'confirmada' AND v.fecha_operativa BETWEEN :desde AND :hasta
    UNION ALL
    SELECT m.fecha, '{MAYOREO}', mp.producto_id, mp.cantidad, mp.importe,
           COALESCE(cm.costo_unitario, pc.costo_promedio)
      FROM ventas_mayoreo m
      JOIN venta_mayoreo_partidas mp ON mp.venta_id = m.id
      LEFT JOIN costo_de_lo_vendido_mayoreo cm ON cm.partida_id = mp.id
      LEFT JOIN producto_costos pc ON pc.producto_id = mp.producto_id
     WHERE m.estado = 'confirmada' AND m.fecha BETWEEN :desde AND :hasta
)
SELECT r.dia, r.vendedor, r.producto_id,
       COALESCE(cat.nombre, 'Sin familia') AS familia, cat.orden,
       p.sku, p.nombre AS articulo,
       sum(r.piezas) AS piezas,
       sum(r.importe) AS vendido,
       COALESCE(sum(round(r.piezas * r.costo_unitario, 2))
                  FILTER (WHERE r.costo_unitario IS NOT NULL), 0) AS costo,
       COALESCE(sum(r.importe) FILTER (WHERE r.costo_unitario IS NULL), 0) AS sin_costo
  FROM renglones r
  JOIN productos p ON p.id = r.producto_id
  LEFT JOIN categorias cat ON cat.id = p.categoria_id
 GROUP BY r.dia, r.vendedor, r.producto_id, cat.nombre, cat.orden, p.sku, p.nombre
"""


def _vacia() -> dict:
    return {"piezas": Decimal(0), "vendido": Decimal(0), "costo": Decimal(0),
            "sin_costo": Decimal(0)}


def _con_ganancia(cifras: dict) -> dict:
    """La ganancia es de lo que tiene costo; el margen, sobre eso mismo."""
    con_costo = cifras["vendido"] - cifras["sin_costo"]
    ganancia = con_costo - cifras["costo"]
    return {
        **cifras,
        "ganancia": ganancia,
        "margen": (ganancia * 100 / con_costo) if con_costo > 0 else None,
    }


async def ganancia_de_lo_vendido(sesion, desde, hasta) -> dict:
    """El total, y por vendedor, por día y por artículo, del periodo."""
    filas = (
        await sesion.execute(text(SQL_LO_VENDIDO), {"desde": desde, "hasta": hasta})
    ).mappings().all()

    total = _vacia()
    por_vendedor: dict[str, dict] = defaultdict(_vacia)
    por_dia: dict = defaultdict(_vacia)
    por_articulo: dict = {}
    for f in filas:
        for destino in (total, por_vendedor[f["vendedor"]], por_dia[f["dia"]]):
            for campo in ("piezas", "vendido", "costo", "sin_costo"):
                destino[campo] += Decimal(f[campo])
        articulo = por_articulo.setdefault(
            f["producto_id"],
            {**_vacia(), "familia": f["familia"], "orden": f["orden"], "sku": f["sku"],
             "articulo": f["articulo"]},
        )
        for campo in ("piezas", "vendido", "costo", "sin_costo"):
            articulo[campo] += Decimal(f[campo])

    articulos = [_con_ganancia(a) for a in por_articulo.values()]
    for a in articulos:
        # Un artículo sin costo no tiene ganancia que decir.
        a["tiene_costo"] = a["sin_costo"] < a["vendido"]
    articulos.sort(key=lambda a: (not a["tiene_costo"], -a["ganancia"], a["articulo"]))
    return {
        "total": _con_ganancia(total),
        # Los vendedores por lo que ganaron; el mayoreo de la oficina al final.
        "por_vendedor": sorted(
            ({"vendedor": n, **_con_ganancia(c)} for n, c in por_vendedor.items()),
            key=lambda v: (v["vendedor"] == MAYOREO, -v["ganancia"], v["vendedor"]),
        ),
        "por_dia": [{"dia": d, **_con_ganancia(c)} for d, c in sorted(por_dia.items())],
        "por_articulo": articulos,
        "articulos_sin_costo": sum(1 for a in articulos if a["sin_costo"] > 0),
    }


async def ganancia_neta(sesion, desde, hasta) -> dict:
    """«La ganancia del periodo que yo quiera; en la tarjeta meteré sueldos,
    gasolina, todos los gastos de la empresa» (§192): la ganancia de lo vendido
    menos los gastos que se capturaron en la tarjeta con fecha del periodo."""
    vendido = await ganancia_de_lo_vendido(sesion, desde, hasta)
    gastos = await tarjeta.gastos(sesion, desde, hasta)
    return {**vendido, "gastos": gastos,
            "neta": vendido["total"]["ganancia"] - gastos["total"]}
