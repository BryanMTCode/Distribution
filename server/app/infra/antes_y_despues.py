"""De cuánto estaba a cuánto pasó: cada movimiento con lo que había y lo que quedó.

Pedido de la dirección (octubre 2026): «en los ajustes y eso quiero ver de
cuántos estaba a cuánto pasó: entradas, salidas, ajustes, todo eso».

El libro mayor no guarda la existencia de antes ni la de después: guarda lo que
se movió. Las dos se sacan de él, igual que el Excel (§179, §183): **lo que
quedó** después de un documento es lo que hay hoy en ese almacén menos todo lo
que se movió después; **lo que había** es lo que quedó menos lo que movió el
documento. Así la última línea de cada artículo siempre dice lo mismo que el
Inventario, y había + cambio = quedó en todos los renglones.

Se agrupa por documento, almacén y artículo: una entrada de tres artículos son
tres renglones; mover mercancía entre camiones, dos por artículo (el que da y el
que recibe). Los almacenes de merma y de tránsito no son anaqueles —son cuentas—
y no salen.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import text

# Qué se puede pedir en la pantalla de Movimientos, en el orden del filtro.
GRUPOS: tuple[tuple[str, str], ...] = (
    ("", "Todo menos las ventas"),
    ("entradas", "Entradas"),
    ("salidas", "Salidas"),
    ("ajustes", "Ajustes y mover mercancía"),
    ("cargas", "Cargas y regresos a bodega"),
    ("cortes", "Cortes"),
    ("mermas", "Mermas y devoluciones"),
    ("mayoreo", "Mayoreo"),
    ("ventas", "Ventas de los vendedores"),
    ("eliminado", "Eliminado en Operaciones"),
    ("todo", "Todo"),
)
NOMBRE_DEL_GRUPO = dict(GRUPOS)

# Los documentos del libro mayor de cada grupo, para no recorrer de más.
_VENTAS = ("venta", "venta_modificada", "venta_cancelada", "venta_corregida")
DOCUMENTOS_DEL_GRUPO: dict[str, tuple[str, ...]] = {
    "entradas": ("entrada",),
    "salidas": ("salida",),
    "ajustes": ("ajuste_camion",),
    "cargas": ("carga", "traspaso"),
    "cortes": ("liquidacion",),
    "mermas": ("merma",),
    "mayoreo": ("venta_mayoreo",),
    "ventas": _VENTAS,
    "eliminado": ("ajuste_camion", "anulacion"),
}

# Más renglones ya no se leen; para eso están los filtros.
LIMITE = 1000

# El grupo de cada documento del libro mayor. Los ajustes que dejó una
# anulación (§126) son lo eliminado, no un ajuste de la oficina.
_GRUPO = """
    CASE
      WHEN d.documento_tipo = 'entrada' THEN 'entradas'
      WHEN d.documento_tipo = 'salida' THEN 'salidas'
      WHEN d.documento_tipo = 'ajuste_camion' AND j.tipo = 'anulacion' THEN 'eliminado'
      WHEN d.documento_tipo = 'ajuste_camion' THEN 'ajustes'
      WHEN d.documento_tipo IN ('carga', 'traspaso') THEN 'cargas'
      WHEN d.documento_tipo = 'liquidacion' THEN 'cortes'
      WHEN d.documento_tipo = 'merma' THEN 'mermas'
      WHEN d.documento_tipo = 'venta_mayoreo' THEN 'mayoreo'
      WHEN d.documento_tipo = 'anulacion' THEN 'eliminado'
      ELSE 'ventas'
    END
"""

# `docs` lo pone quien llama: los documentos que se quieren ver. Lo demás es lo
# mismo para la pantalla de Movimientos y para el detalle de cada folio.
_SQL = """
WITH docs AS ({docs}),
-- Solo se recorre la historia de los artículos y almacenes que tocaron.
tocados AS (
    SELECT DISTINCT x.almacen_id, m.producto_id
      FROM movimientos_inventario m
      JOIN docs d ON d.documento_tipo = m.documento_tipo AND d.documento_id = m.documento_id
     CROSS JOIN LATERAL (VALUES (m.almacen_origen_id), (m.almacen_destino_id)) x(almacen_id)
     WHERE x.almacen_id IS NOT NULL
),
-- Cada movimiento le cambia a lo más dos almacenes: al que sale, en negativo, y
-- al que llega, en positivo.
efectos AS (
    SELECT m.id, m.fecha_servidor, m.documento_tipo, m.documento_id, m.producto_id,
           m.usuario_id, x.almacen_id, x.delta
      FROM movimientos_inventario m
     CROSS JOIN LATERAL (VALUES (m.almacen_origen_id, -m.cantidad),
                                (m.almacen_destino_id, m.cantidad)) x(almacen_id, delta)
      JOIN tocados t ON t.almacen_id = x.almacen_id AND t.producto_id = m.producto_id
     WHERE m.producto_id IN (SELECT producto_id FROM tocados)
),
-- Lo que quedó después de cada renglón: lo de hoy menos lo que vino después.
corrido AS (
    SELECT e.*,
           COALESCE(ex.cantidad, 0) - COALESCE(sum(e.delta) OVER (
               PARTITION BY e.almacen_id, e.producto_id
               ORDER BY e.fecha_servidor, e.documento_tipo, e.documento_id, e.id
               ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING), 0) AS despues
      FROM efectos e
      LEFT JOIN existencias ex
             ON ex.almacen_id = e.almacen_id AND ex.producto_id = e.producto_id
),
por_documento AS (
    SELECT c.documento_tipo, c.documento_id, c.almacen_id, c.producto_id,
           max(c.fecha_servidor) AS cuando,
           (array_agg(c.usuario_id ORDER BY c.fecha_servidor DESC, c.id DESC))[1]
             AS usuario_id,
           sum(c.delta) AS cambio,
           (array_agg(c.despues ORDER BY c.fecha_servidor DESC, c.id DESC))[1] AS despues
      FROM corrido c
      JOIN docs d ON d.documento_tipo = c.documento_tipo AND d.documento_id = c.documento_id
     GROUP BY 1, 2, 3, 4
)
SELECT * FROM (
SELECT d.documento_tipo, d.documento_id, d.cuando, d.cambio, d.despues,
       d.despues - d.cambio AS antes,
       a.id AS almacen_id, a.nombre AS almacen, a.tipo AS almacen_tipo,
       p.id AS producto_id, p.sku, p.nombre AS producto, p.unidad_base,
       u.nombre AS quien,
       {grupo} AS grupo,
       -- Qué fue, con palabras.
       CASE d.documento_tipo
         WHEN 'entrada' THEN 'Entrada'
         WHEN 'salida' THEN CASE s.tipo WHEN 'merma' THEN 'Salida por merma'
                                        ELSE 'Salida por conteo' END
         WHEN 'ajuste_camion' THEN CASE j.tipo WHEN 'traspaso' THEN 'Mover mercancía'
                                               WHEN 'conteo' THEN 'Ajuste por conteo'
                                               WHEN 'merma' THEN 'Ajuste por merma'
                                               WHEN 'entrada' THEN 'Ajuste: entrada'
                                               ELSE 'Eliminado en Operaciones' END
         WHEN 'carga' THEN 'Carga'
         WHEN 'traspaso' THEN CASE WHEN ot.tipo = 'camion' THEN 'Regreso a bodega'
                                   ELSE 'Traspaso' END
         WHEN 'liquidacion' THEN 'Corte'
         WHEN 'merma' THEN CASE me.tipo WHEN 'devolucion_cliente'
                                          THEN 'Devolución del cliente'
                                        WHEN 'cambio' THEN 'Cambio físico'
                                        ELSE 'Merma' END
         WHEN 'venta_mayoreo' THEN 'Venta de mayoreo'
         WHEN 'venta_cancelada' THEN 'Venta cancelada'
         WHEN 'venta_corregida' THEN 'Venta corregida'
         WHEN 'venta_modificada' THEN 'Venta modificada'
         WHEN 'anulacion' THEN 'Eliminado en Operaciones'
         ELSE 'Venta'
       END AS que,
       COALESCE(en.folio, s.folio, j.folio, g.folio, t.folio, l.folio, me.folio_local,
                vm.folio, v.folio_local, an.folio) AS folio,
       CASE
         WHEN en.id IS NOT NULL THEN '/panel/entradas/' || en.id
         WHEN s.id IS NOT NULL THEN '/panel/salidas/' || s.id
         WHEN j.id IS NOT NULL AND j.tipo <> 'anulacion'
           THEN '/panel/operaciones/ajuste/' || COALESCE(j.pareja_id, j.id)
         WHEN g.id IS NOT NULL THEN '/panel/cargas/' || g.id
         WHEN t.id IS NOT NULL
           THEN '/panel/operaciones/'
                || CASE WHEN ot.tipo = 'camion' THEN 'devolucion_bodega' ELSE 'traspaso' END
                || '/' || t.id
         WHEN l.id IS NOT NULL THEN '/panel/liquidaciones/' || l.id
         WHEN me.id IS NOT NULL
           THEN '/panel/operaciones/'
                || CASE me.tipo WHEN 'devolucion_cliente' THEN 'devolucion' ELSE me.tipo END
                || '/' || me.id
         WHEN vm.id IS NOT NULL THEN '/panel/mayoreo/' || vm.id
         WHEN v.id IS NOT NULL THEN '/panel/ventas/' || v.id
         WHEN an.id IS NOT NULL
           THEN '/panel/operaciones/' || an.tipo || '/' || an.documento_id
       END AS enlace
  FROM por_documento d
  JOIN almacenes a ON a.id = d.almacen_id AND a.tipo IN ('bodega', 'camion')
  JOIN productos p ON p.id = d.producto_id
  LEFT JOIN usuarios u ON u.id = d.usuario_id
  LEFT JOIN entradas en ON d.documento_tipo = 'entrada' AND en.id = d.documento_id
  LEFT JOIN salidas s ON d.documento_tipo = 'salida' AND s.id = d.documento_id
  LEFT JOIN ajustes_camion j ON d.documento_tipo = 'ajuste_camion' AND j.id = d.documento_id
  LEFT JOIN cargas g ON d.documento_tipo = 'carga' AND g.id = d.documento_id
  LEFT JOIN traspasos t ON d.documento_tipo = 'traspaso' AND t.id = d.documento_id
  LEFT JOIN almacenes ot ON ot.id = t.almacen_origen_id
  LEFT JOIN liquidaciones l ON d.documento_tipo = 'liquidacion' AND l.id = d.documento_id
  LEFT JOIN mermas me ON d.documento_tipo = 'merma' AND me.id = d.documento_id
  LEFT JOIN ventas_mayoreo vm ON d.documento_tipo = 'venta_mayoreo' AND vm.id = d.documento_id
  LEFT JOIN ventas v ON d.documento_tipo IN ('venta', 'venta_modificada')
                    AND v.id = d.documento_id
  LEFT JOIN anulaciones an ON d.documento_tipo = 'anulacion' AND an.id = d.documento_id
) x
{filtro}
ORDER BY {orden}
LIMIT :limite
"""


async def de_los_documentos(sesion, documentos: list[tuple[str, uuid.UUID]]) -> list[dict]:
    """Lo que había y lo que quedó con cada artículo que movieron esos documentos.

    Para el detalle de un folio: por artículo y almacén, en orden de artículo.
    """
    if not documentos:
        return []
    sql = _SQL.format(
        docs="SELECT * FROM unnest(CAST(:tipos AS text[]), CAST(:ids AS uuid[])) "
             "AS d(documento_tipo, documento_id)",
        grupo=_GRUPO,
        filtro="",
        orden="x.producto, x.sku, x.almacen_tipo, x.almacen, x.cuando",
    )
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(sql),
                {"tipos": [t for t, _ in documentos], "ids": [i for _, i in documentos],
                 "limite": LIMITE},
            )
        ).mappings().all()
    ]


async def del_periodo(
    sesion, *, desde: date, hasta: date, grupo: str = "", almacen: uuid.UUID | None = None
) -> list[dict]:
    """Todo lo que se movió en esos días, lo más nuevo primero (para la pantalla
    de Movimientos). Sin grupo, todo menos las ventas de los vendedores: son la
    mayoría de los renglones y tienen su propia pantalla."""
    if grupo not in NOMBRE_DEL_GRUPO:
        grupo = ""
    sql = _SQL.format(
        docs="SELECT DISTINCT m.documento_tipo, m.documento_id "
             "  FROM movimientos_inventario m "
             " WHERE CAST(m.fecha_servidor AS date) BETWEEN :desde AND :hasta "
             "   AND (CAST(:documentos AS text[]) IS NULL "
             "        OR m.documento_tipo = ANY(CAST(:documentos AS text[]))) "
             "   AND NOT (m.documento_tipo = ANY(CAST(:sin AS text[]))) "
             "   AND (CAST(:almacen AS uuid) IS NULL "
             "        OR CAST(:almacen AS uuid) IN (m.almacen_origen_id, m.almacen_destino_id))",
        grupo=_GRUPO,
        filtro="WHERE (CAST(:grupo AS text) = 'todo' OR x.grupo = :grupo "
               "       OR (CAST(:grupo AS text) = '' AND x.grupo <> 'ventas')) "
               "  AND (CAST(:almacen AS uuid) IS NULL OR x.almacen_id = :almacen)",
        orden="x.cuando DESC, x.documento_id, x.producto, x.almacen",
    )
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(sql),
                {"desde": desde, "hasta": hasta, "grupo": grupo,
                 "documentos": list(DOCUMENTOS_DEL_GRUPO[grupo])
                 if grupo in DOCUMENTOS_DEL_GRUPO else None,
                 "sin": list(_VENTAS) if grupo == "" else [],
                 "almacen": almacen, "limite": LIMITE},
            )
        ).mappings().all()
    ]


async def de_la_operacion(sesion, tipo: str, ref: uuid.UUID) -> list[dict]:
    """Lo mismo, para una operación de la pantalla de Operaciones: sus documentos
    del libro mayor. Una captura de varios artículos (§146) es una cabeza con sus
    parejas, y se ven todas."""
    documentos: list[tuple[str, uuid.UUID]] = {
        "venta": [("venta", ref), ("venta_modificada", ref)],
        "mayoreo": [("venta_mayoreo", ref)],
        "merma": [("merma", ref)],
        "devolucion": [("merma", ref)],
        "cambio": [("merma", ref)],
        "carga": [("carga", ref)],
        "devolucion_bodega": [("traspaso", ref)],
        "traspaso": [("traspaso", ref)],
        "entrada": [("entrada", ref)],
        "salida": [("salida", ref)],
        "ajuste": [("ajuste_camion", ref)],
        "corte": [("liquidacion", ref)],
    }.get(tipo, [])
    if tipo == "ajuste":
        documentos += [
            ("ajuste_camion", pareja)
            for pareja in (
                await sesion.execute(
                    text("SELECT id FROM ajustes_camion WHERE pareja_id = :j"), {"j": ref}
                )
            ).scalars()
        ]
    return await de_los_documentos(sesion, documentos)
