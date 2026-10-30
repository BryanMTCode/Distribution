"""Las compras a proveedores y los gastos del periodo, de un vistazo (ADR 0002 §193).

«Que en el dashboard se pueda llevar el control de los gastos y compras a
proveedores, pero con una vista buena: no quiero tener que meterme a cada
compra.»

Cada compra con lo que trajo a la vista —artículo y piezas—, lo que se pagó y lo
que se debe; las mismas compras por proveedor y por artículo; y los gastos de la
empresa capturados en la tarjeta (§192). Los renglones de las compras son los
mismos del Excel (§191).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from app.infra import tarjeta
from app.infra.reporte_excel import SQL_COMPRAS

# Lo pagado y lo que se debe de cada compra, y cómo se pagó. La compra sin
# proveedor del catálogo no tiene cuenta por pagar: no se sabe.
SQL_CUENTAS = """
SELECT cp.entrada_id, cp.importe_pagado AS pagado, cp.saldo AS debe,
       array_remove(array_agg(DISTINCT g.forma_pago), NULL) AS formas
  FROM cuentas_por_pagar cp
  LEFT JOIN pagos_proveedor g ON g.entrada_id = cp.entrada_id
 WHERE cp.entrada_id = ANY(:ids)
 GROUP BY cp.entrada_id, cp.importe_pagado, cp.saldo
"""

FORMAS = {"efectivo": "efectivo", "transferencia": "transferencia", "cheque": "cheque",
          "tarjeta": "tarjeta de la empresa"}


async def del_periodo(sesion, desde, hasta) -> dict:
    renglones = [
        dict(f)
        for f in (
            await sesion.execute(text(SQL_COMPRAS), {"desde": desde, "hasta": hasta})
        ).mappings().all()
    ]
    ids = list({r["entrada"] for r in renglones})
    cuentas = {
        c["entrada_id"]: dict(c)
        for c in (await sesion.execute(text(SQL_CUENTAS), {"ids": ids})).mappings().all()
    } if ids else {}

    compras: dict = {}
    por_articulo: dict = {}
    for r in renglones:
        c = compras.setdefault(r["entrada"], {
            "id": r["entrada"], "fecha": r["fecha"], "folio": r["folio"],
            "proveedor": r["proveedor"], "referencia": r["referencia"],
            "almacen": r["almacen"], "articulos": [], "piezas": Decimal(0),
            "importe": Decimal(0),
        })
        c["articulos"].append({"articulo": r["articulo"], "piezas": Decimal(r["piezas"])})
        c["piezas"] += Decimal(r["piezas"])
        c["importe"] += Decimal(r["importe"] or 0)
        a = por_articulo.setdefault(r["producto"], {
            "orden": (r["orden"] is None, r["orden"] or 0, r["familia"], r["articulo"]),
            "familia": r["familia"], "sku": r["sku"], "articulo": r["articulo"],
            "piezas": Decimal(0), "importe": Decimal(0), "compras": 0,
        })
        a["piezas"] += Decimal(r["piezas"])
        a["importe"] += Decimal(r["importe"] or 0)
        a["compras"] += 1

    por_proveedor: dict = {}
    for c in compras.values():
        cuenta = cuentas.get(c["id"])
        c["con_cuenta"] = cuenta is not None
        c["pagado"] = Decimal(cuenta["pagado"]) if cuenta else None
        c["debe"] = Decimal(cuenta["debe"]) if cuenta else None
        c["formas"] = [FORMAS.get(f, f) for f in (cuenta["formas"] if cuenta else [])]
        p = por_proveedor.setdefault(c["proveedor"], {
            "proveedor": c["proveedor"], "compras": 0, "importe": Decimal(0),
            "pagado": Decimal(0), "debe": Decimal(0),
        })
        p["compras"] += 1
        p["importe"] += c["importe"]
        p["pagado"] += c["pagado"] or Decimal(0)
        p["debe"] += c["debe"] or Decimal(0)

    for a in por_articulo.values():
        a["por_pieza"] = (a["importe"] / a["piezas"]).quantize(Decimal("0.01")) \
            if a["piezas"] and a["importe"] else None

    lista = sorted(compras.values(), key=lambda c: (c["fecha"], c["folio"]), reverse=True)
    gastos = await tarjeta.gastos(sesion, desde, hasta)
    importe = sum((c["importe"] for c in lista), Decimal(0))
    return {
        "compras": lista,
        "por_proveedor": sorted(por_proveedor.values(), key=lambda p: -p["importe"]),
        "por_articulo": sorted(por_articulo.values(), key=lambda a: a["orden"]),
        "importe": importe,
        "piezas": sum((c["piezas"] for c in lista), Decimal(0)),
        "pagado": sum((c["pagado"] or Decimal(0) for c in lista), Decimal(0)),
        "debe": sum((c["debe"] or Decimal(0) for c in lista), Decimal(0)),
        "gastos": gastos,
        "salio": importe + gastos["total"],
    }
