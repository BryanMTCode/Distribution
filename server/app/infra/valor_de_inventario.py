"""Lo que vale el inventario: la existencia por su precio de venta.

Pedido de la dirección (octubre 2026): «en el inventario quiero que salga el
total de dinero que hay por cada artículo», como en su hoja: existencia ×
precio de venta. Lo usan el Inventario del panel y las Existencias de la app.

El precio es el de la **lista por omisión**, por unidad base: el de la pieza si
lo tiene; si solo tiene precio por caja, el de la caja entre su factor. Un
artículo sin precio no vale cero —vale «sin precio»—: sumarlo como cero haría
que el total pareciera completo sin estarlo.
"""

from __future__ import annotations

# El precio de venta de una pieza del producto `{producto}`, como `pv.precio`.
# Se usa dentro de un FROM: `... FROM existencias e {SQL} WHERE ...`.
_SQL_PRECIO_DE_VENTA = """
LEFT JOIN LATERAL (
      SELECT round(pr.precio / pu.factor, 4) AS precio
        FROM precios pr
        JOIN producto_unidades pu
          ON pu.producto_id = pr.producto_id AND pu.unidad_codigo = pr.unidad_codigo
        JOIN listas_precios l ON l.id = pr.lista_id AND l.activo
       WHERE pr.producto_id = {producto} AND pu.factor > 0
       ORDER BY l.es_default DESC, (pu.factor = 1) DESC, pu.factor
       LIMIT 1
) {alias} ON true
"""


def precio_de_venta(producto: str, alias: str = "pv") -> str:
    """El fragmento de SQL, para la columna de producto que se le diga."""
    return _SQL_PRECIO_DE_VENTA.format(producto=producto, alias=alias)
