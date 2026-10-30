"""La tarjeta de la empresa, y lo que vale la empresa (ADR 0002 §190).

Pedido de la dirección (octubre 2026): «quiero en el dashboard tener la cuenta
del almacén, más los camiones, más la tarjeta de débito de la empresa: las tres
visiones distintas y el total. Y las compras poder agregarlas de la tarjeta: a la
tarjeta, la venta y las salidas.»

LA TARJETA ES UNA CUENTA QUE SE CALCULA
---------------------------------------
Empieza con lo que tenía el día en que se capturó su saldo («inicial»), y desde
ese momento:

  · **entra solo** el efectivo que entrega cada vendedor en su corte, la venta de
    mayoreo y lo que un vendedor paga de su cuenta;
  · **sale solo** el pago de una compra hecho con la tarjeta;
  · **a mano**, el dinero que entra o sale por otra cosa (gasolina, sueldos, un
    retiro) y el ajuste para que diga lo mismo que el banco.

Nada de lo automático se copia a otra tabla: se lee de donde ya está. Un corte o
un mayoreo eliminado en Operaciones deja de contar, y un pago de compra que se
borra con su compra, también.

LO QUE VALE LA EMPRESA
----------------------
La mercancía de las bodegas, la de los camiones —a precio de venta, la misma
cuenta del Excel (§183)— y la tarjeta. Las tres por separado, y el total.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.infra.valor_de_inventario import SQL_VALOR_DE_LOS_ALMACENES, precio_de_venta


class MovimientoInvalido(Exception):
    """No se guarda así; el mensaje dice por qué."""


# Lo que se captura a mano, y cómo se dice.
A_MANO = {"entrada": "Entrada de dinero", "salida": "Salida de dinero"}

# Cómo se dice cada clase de movimiento en la lista.
CLASES = {
    "inicial": "Saldo con que empezó",
    "entrada": "Entrada de dinero",
    "salida": "Salida de dinero",
    "ajuste": "Ajuste con el banco",
    "corte": "Corte de un vendedor",
    "mayoreo": "Venta de mayoreo",
    "pago_vendedor": "Pago de un vendedor",
    "compra": "Compra pagada con la tarjeta",
}

SQL_MOVIMIENTOS = """
WITH inicio AS (
    SELECT min(registrado_en) AS desde FROM movimientos_tarjeta
     WHERE tipo = 'inicial' AND quitado_en IS NULL
)
SELECT * FROM (
    -- Lo capturado a mano.
    SELECT t.registrado_en AS cuando, t.fecha, t.tipo AS clase, t.concepto, t.importe,
           u.nombre AS quien, t.id AS ref, CAST(NULL AS text) AS enlace,
           t.tipo IN ('entrada', 'salida', 'ajuste') AS se_quita
      FROM movimientos_tarjeta t
      LEFT JOIN usuarios u ON u.id = t.registrado_por
     WHERE t.quitado_en IS NULL

    -- El efectivo que entregó cada vendedor en su corte.
    UNION ALL
    SELECT l.cerrada_en, COALESCE(l.cuenta_hasta, l.fecha_operativa), 'corte',
           'Corte ' || l.folio || ' · ' || u.nombre, l.efectivo_entregado,
           NULL, l.id, '/panel/liquidaciones/' || l.id, false
      FROM liquidaciones l
      JOIN usuarios u ON u.id = l.vendedor_id
      CROSS JOIN inicio i
     WHERE l.estado = 'cerrada' AND l.efectivo_entregado <> 0
       AND l.cerrada_en >= i.desde

    -- La venta de mayoreo, de contado.
    UNION ALL
    SELECT vm.creada_en, vm.fecha, 'mayoreo',
           'Mayoreo ' || vm.folio || COALESCE(' · ' || c.nombre_comercial, ''), vm.total,
           NULL, vm.id, '/panel/mayoreo/' || vm.id, false
      FROM ventas_mayoreo vm
      LEFT JOIN clientes c ON c.id = vm.cliente_id
      CROSS JOIN inicio i
     WHERE vm.estado = 'confirmada' AND vm.total > 0 AND vm.creada_en >= i.desde

    -- Lo que un vendedor pagó de su cuenta (el descuento de nómina no es dinero
    -- que entre).
    UNION ALL
    SELECT cv.registrado_en, cv.fecha, 'pago_vendedor',
           'Pagó de su cuenta · ' || u.nombre, cv.importe,
           NULL, cv.id, '/panel/vendedores/cuenta/' || cv.vendedor_id, false
      FROM cuenta_vendedor cv
      JOIN usuarios u ON u.id = cv.vendedor_id
      CROSS JOIN inicio i
     WHERE cv.tipo = 'abono' AND cv.origen = 'pago' AND cv.registrado_en >= i.desde

    -- La compra que se pagó con la tarjeta.
    UNION ALL
    SELECT g.registrado_en, g.fecha_pago, 'compra',
           'Pago de la compra ' || e.folio || COALESCE(' · ' || pr.nombre, ''), -g.importe,
           u.nombre, g.id, '/panel/compras/cuenta/' || g.entrada_id, false
      FROM pagos_proveedor g
      JOIN entradas e ON e.id = g.entrada_id
      LEFT JOIN cuentas_por_pagar cp ON cp.entrada_id = g.entrada_id
      LEFT JOIN proveedores pr ON pr.id = cp.proveedor_id
      LEFT JOIN usuarios u ON u.id = g.registrado_por
      CROSS JOIN inicio i
     WHERE g.forma_pago = 'tarjeta' AND g.registrado_en >= i.desde
) x
ORDER BY x.cuando, x.ref
"""


# «Cuando venda en mayoreo se perderá dinero respecto al precio de venta» (§191):
# lo vendido en mayoreo en el mes, lo que valía a precio de lista de hoy —como se
# valúa la mercancía—, y la diferencia. Lo que no tiene precio, a lo que se cobró.
SQL_MAYOREO_DEL_MES = f"""
SELECT COALESCE(sum(m.total), 0) AS vendido,
       COALESCE(sum(COALESCE(
           (SELECT sum(COALESCE(p.cantidad * pv.precio, p.importe))
              FROM venta_mayoreo_partidas p
              {precio_de_venta("p.producto_id")}
             WHERE p.venta_id = m.id), m.total)), 0) AS a_precio_de_lista
  FROM ventas_mayoreo m
 WHERE m.estado = 'confirmada'
   AND m.fecha >= date_trunc('month', CURRENT_DATE)::date AND m.fecha <= CURRENT_DATE
"""


async def movimientos(sesion) -> list[dict]:
    """Todo lo que movió la tarjeta desde su inicio, en orden, con el saldo que
    dejó cada uno."""
    saldo = Decimal(0)
    filas = []
    for f in (await sesion.execute(text(SQL_MOVIMIENTOS))).mappings().all():
        saldo += Decimal(f["importe"])
        filas.append({**dict(f), "saldo": saldo, "nombre_clase": CLASES[f["clase"]]})
    return filas


async def empezo(sesion) -> bool:
    return bool(
        (
            await sesion.execute(
                text("SELECT 1 FROM movimientos_tarjeta "
                     " WHERE tipo = 'inicial' AND quitado_en IS NULL")
            )
        ).first()
    )


async def saldo(sesion) -> Decimal | None:
    """Lo que debe haber hoy en la tarjeta; `None` si todavía no se captura con
    cuánto empezó."""
    if not await empezo(sesion):
        return None
    filas = await movimientos(sesion)
    return filas[-1]["saldo"] if filas else Decimal(0)


async def lo_que_vale(sesion) -> dict:
    """Las bodegas, los camiones y la tarjeta, y el total."""
    almacenes = [
        dict(f)
        for f in (await sesion.execute(text(SQL_VALOR_DE_LOS_ALMACENES))).mappings().all()
    ]
    almacenes.sort(key=lambda a: a["nombre"])
    bodegas = [a for a in almacenes if a["tipo"] == "bodega"]
    camiones = [a for a in almacenes if a["tipo"] == "camion"]
    en_bodegas = sum((Decimal(a["valor"]) for a in bodegas), Decimal(0))
    en_camiones = sum((Decimal(a["valor"]) for a in camiones), Decimal(0))
    en_tarjeta = await saldo(sesion)
    sin_precio = (
        await sesion.execute(
            text(
                """
                SELECT count(DISTINCT e.producto_id)
                  FROM existencias e
                  JOIN almacenes a ON a.id = e.almacen_id AND a.tipo IN ('bodega', 'camion')
                 WHERE e.cantidad <> 0
                   AND NOT EXISTS (SELECT 1 FROM precios pr
                                     JOIN listas_precios l ON l.id = pr.lista_id AND l.activo
                                    WHERE pr.producto_id = e.producto_id)
                """
            )
        )
    ).scalar_one()
    mayoreo = (await sesion.execute(text(SQL_MAYOREO_DEL_MES))).mappings().one()
    return {
        "mayoreo_del_mes": {
            "vendido": Decimal(mayoreo["vendido"]),
            "a_precio_de_lista": Decimal(mayoreo["a_precio_de_lista"]),
            "abajo": Decimal(mayoreo["a_precio_de_lista"]) - Decimal(mayoreo["vendido"]),
        },
        "bodegas": bodegas,
        "camiones": camiones,
        "en_bodegas": en_bodegas,
        "en_camiones": en_camiones,
        "en_tarjeta": en_tarjeta,
        "total": en_bodegas + en_camiones + (en_tarjeta or Decimal(0)),
        "sin_precio": sin_precio,
    }


def _concepto(concepto: str) -> str:
    limpio = " ".join((concepto or "").split())
    if len(limpio) < 3:
        raise MovimientoInvalido("Escribe en qué se fue o de dónde vino el dinero.")
    return limpio[:300]


async def _insertar(sesion, *, tipo, importe, fecha, concepto, quien) -> None:
    await sesion.execute(
        text(
            "INSERT INTO movimientos_tarjeta (tipo, importe, fecha, concepto, registrado_por) "
            "VALUES (:t, :i, :f, :c, :q)"
        ),
        {"t": tipo, "i": importe, "f": fecha, "c": concepto, "q": quien},
    )


async def empezar(sesion, *, saldo_hoy: Decimal, quien: uuid.UUID) -> None:
    """Con cuánto empieza la tarjeta: desde ahora cuenta lo automático. No hace
    commit."""
    if await empezo(sesion):
        raise MovimientoInvalido(
            "La tarjeta ya tiene su saldo inicial. Si no cuadra con el banco, usa "
            "«Así está hoy la tarjeta»."
        )
    if saldo_hoy < 0:
        raise MovimientoInvalido("El saldo de la tarjeta no puede ser negativo.")
    await _insertar(sesion, tipo="inicial", importe=saldo_hoy, fecha=date.today(),
                    concepto="Con esto empezó la tarjeta", quien=quien)


async def registrar(
    sesion, *, tipo: str, importe: Decimal, concepto: str, fecha: date, quien: uuid.UUID
) -> None:
    """Una entrada o una salida de dinero a mano. No hace commit."""
    if tipo not in A_MANO:
        raise MovimientoInvalido("Elige si el dinero entra o sale.")
    if not await empezo(sesion):
        raise MovimientoInvalido("Primero escribe con cuánto empieza la tarjeta.")
    if importe <= 0:
        raise MovimientoInvalido("El importe tiene que ser mayor que cero.")
    if fecha > date.today():
        raise MovimientoInvalido("La fecha no puede ser futura.")
    await _insertar(sesion, tipo=tipo, importe=importe if tipo == "entrada" else -importe,
                    fecha=fecha, concepto=_concepto(concepto), quien=quien)


async def asi_esta(sesion, *, saldo_real: Decimal, quien: uuid.UUID) -> Decimal:
    """Lo que dice el banco hoy: se ajusta la diferencia y se devuelve. No hace
    commit."""
    actual = await saldo(sesion)
    if actual is None:
        raise MovimientoInvalido("Primero escribe con cuánto empieza la tarjeta.")
    diferencia = saldo_real - actual
    if diferencia == 0:
        return diferencia
    await _insertar(
        sesion, tipo="ajuste", importe=diferencia, fecha=date.today(),
        concepto=f"El banco dice ${saldo_real:,.2f}; el sistema decía ${actual:,.2f}",
        quien=quien,
    )
    return diferencia


async def quitar(sesion, *, movimiento_id: uuid.UUID, motivo: str, quien: uuid.UUID) -> None:
    """Quita lo capturado por error; se queda con quién y por qué. No hace commit."""
    motivo = " ".join((motivo or "").split())
    if len(motivo) < 3:
        raise MovimientoInvalido("Escribe por qué se quita.")
    quitado = (
        await sesion.execute(
            text(
                "UPDATE movimientos_tarjeta "
                "   SET quitado_en = now(), quitado_por = :q, motivo_quitado = :m "
                " WHERE id = :id AND quitado_en IS NULL "
                "   AND tipo IN ('entrada', 'salida', 'ajuste') "
                "RETURNING id"
            ),
            {"id": movimiento_id, "q": quien, "m": motivo[:300]},
        )
    ).first()
    if quitado is None:
        raise MovimientoInvalido("Ese movimiento no se puede quitar, o ya se quitó.")
