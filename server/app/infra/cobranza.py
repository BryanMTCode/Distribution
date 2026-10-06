"""Aplicar un cobro a las facturas del cliente, y deshacerlo.

Vive aparte del manejador de sincronización porque ya no lo usa solo él:

    · el manejador de `cobro.crear` aplica el efectivo en cuanto llega;
    · el panel aplica una transferencia o un cheque cuando la oficina confirma
      que el dinero está en el banco (migración 0038);
    · el panel revierte uno ya aplicado cuando el cheque rebota.

Si cada camino escribiera su propio FIFO, el día que uno cambie el orden de
aplicación habría dos carteras distintas según por dónde entró el pago.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CERO = Decimal("0")


@dataclass(frozen=True)
class Aplicacion:
    """Lo que el FIFO hizo con un cobro."""

    aplicado: Decimal
    """Lo que se abonó a facturas."""

    sobrante: Decimal
    """Lo que no cupo: saldo a favor del cliente."""

    habia_deuda: bool
    """Si el cliente tenía alguna factura abierta al aplicarlo."""


async def aplicar_fifo(
    sesion: AsyncSession,
    *,
    cobro_id: uuid.UUID,
    cliente_id: uuid.UUID,
    importe: Decimal,
    ahora: datetime,
) -> Aplicacion:
    """Abona `importe` a las facturas del cliente, la más vencida primero.

    El orden es por **vencimiento más antiguo**: se paga primero lo que lleva más
    tiempo vencido, que es lo que reduce el riesgo real de la cartera.

    Escribe `cobros_aplicaciones` y `cuentas_por_cobrar`; NO toca la fila del
    cobro. Quien llama decide el estado y las marcas, porque cada camino marca
    cosas distintas.
    """
    # `FOR UPDATE` sobre las facturas: dos cobros del mismo cliente llegando en
    # el mismo lote aplicarían los dos sobre el mismo saldo leído, y el segundo
    # sobrepasaría `pago_no_excede_original`. Serializar aquí es correcto porque
    # son las facturas de UN cliente, no de la cartera.
    facturas = (
        await sesion.execute(
            text(
                """
                SELECT venta_id, saldo FROM cuentas_por_cobrar
                 WHERE cliente_id = :c AND estado <> 'liquidada' AND saldo > 0
                 ORDER BY fecha_vencimiento, fecha_emision
                 FOR UPDATE
                """
            ),
            {"c": cliente_id},
        )
    ).mappings().all()

    por_aplicar = importe
    aplicado = CERO
    for factura in facturas:
        if por_aplicar <= 0:
            break
        # Lo que cabe en esta factura. El sobrante pasa a la siguiente, y lo que
        # quede al final es saldo a favor.
        cabe = min(por_aplicar, Decimal(factura["saldo"]))
        if cabe <= 0:
            continue

        await sesion.execute(
            text(
                """
                INSERT INTO cobros_aplicaciones (cobro_id, venta_id, importe, aplicado_en)
                VALUES (:cobro, :venta, :importe, :ahora)
                """
            ),
            {"cobro": cobro_id, "venta": factura["venta_id"], "importe": cabe, "ahora": ahora},
        )
        # `saldo` es una columna GENERADA (importe_original − importe_pagado): se
        # actualiza el pagado y la base recalcula. Escribir el saldo a mano daría
        # dos verdades que tarde o temprano no coinciden.
        await sesion.execute(
            text(
                """
                UPDATE cuentas_por_cobrar
                   SET importe_pagado = importe_pagado + :importe,
                       estado = CASE
                                  WHEN importe_pagado + :importe >= importe_original
                                    THEN 'liquidada'
                                  ELSE 'parcial'
                                END,
                       actualizado_en = :ahora
                 WHERE venta_id = :venta
                """
            ),
            {"importe": cabe, "venta": factura["venta_id"], "ahora": ahora},
        )
        por_aplicar -= cabe
        aplicado += cabe

    return Aplicacion(aplicado=aplicado, sobrante=por_aplicar, habia_deuda=bool(facturas))


async def revertir_aplicacion(
    sesion: AsyncSession, *, cobro_id: uuid.UUID, ahora: datetime
) -> Decimal:
    """Deshace lo que el FIFO abonó con este cobro. Devuelve cuánto se revirtió.

    Es el cheque que rebota después de confirmado: el dinero nunca llegó, así que
    cada factura que ese cheque pagó vuelve a deber exactamente lo que pagó. Una
    factura liquidada vuelve a `parcial` o `abierta`, y su vencimiento NO se
    mueve: el cliente debe desde cuando debía, no desde que rebotó el cheque.

    Una factura que la oficina ya declaró `incobrable` se queda así: esa es una
    decisión aparte, y reabrirla sola la sacaría de donde alguien la puso.
    """
    aplicaciones = (
        await sesion.execute(
            text(
                "SELECT venta_id, importe FROM cobros_aplicaciones "
                " WHERE cobro_id = :c FOR UPDATE"
            ),
            {"c": cobro_id},
        )
    ).mappings().all()

    total = CERO
    for a in aplicaciones:
        await sesion.execute(
            text(
                """
                UPDATE cuentas_por_cobrar
                   SET importe_pagado = importe_pagado - :importe,
                       estado = CASE
                                  WHEN estado = 'incobrable' THEN estado
                                  WHEN importe_pagado - :importe <= 0 THEN 'abierta'
                                  ELSE 'parcial'
                                END,
                       actualizado_en = :ahora
                 WHERE venta_id = :venta
                """
            ),
            {"importe": a["importe"], "venta": a["venta_id"], "ahora": ahora},
        )
        total += Decimal(a["importe"])

    await sesion.execute(
        text("DELETE FROM cobros_aplicaciones WHERE cobro_id = :c"), {"c": cobro_id}
    )
    return total
