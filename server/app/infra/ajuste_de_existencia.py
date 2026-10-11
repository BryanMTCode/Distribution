"""Registrar un ajuste de existencia: el documento, el asiento y la caché.

Lo usan el ajuste a mano del Inventario del panel y la carga del estado de los
camiones desde el Excel de la dirección (`camiones_al_sabado.py`, ADR 0002 §99).
Vive aparte para que los dos escriban exactamente lo mismo.

El ajuste de un camión viaja al teléfono de su dueño —el disparador de
`ajustes_camion` publica la diferencia firmada (migración 0037)—; el de una
bodega no viaja a ninguno.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text


async def existencia_con_candado(sesion, almacen_id, producto_id) -> Decimal:
    """La existencia, leída con candado: entre leerla y ajustarla pudo entrar
    una venta del teléfono."""
    return Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT COALESCE(("
                    "  SELECT cantidad FROM existencias "
                    "   WHERE almacen_id = :a AND producto_id = :p FOR UPDATE"
                    "), 0)"
                ),
                {"a": almacen_id, "p": producto_id},
            )
        ).scalar_one()
    )


async def registrar_ajuste(
    sesion,
    *,
    almacen_id,
    producto_id,
    es_camion: bool,
    tipo: str,
    actual: Decimal,
    delta: Decimal,
    contado: Decimal | None,
    nota: str,
    quien,
    motivo_codigo: str | None = None,
) -> str:
    """Escribe el ajuste y devuelve su folio (AC- camión, AB- bodega). No hace
    commit: quien llama decide la transacción."""
    ahora = datetime.now(UTC)
    ajuste_id = uuid.uuid4()
    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_ajuste_camion')"))
    ).scalar_one()
    # AC para camión, AB para bodega: el folio dice de dónde es sin abrir el
    # documento. La serie es una sola.
    folio = f"{'AC' if es_camion else 'AB'}-{consecutivo:06d}"
    await sesion.execute(
        text(
            """
            INSERT INTO ajustes_camion
              (id, folio, almacen_id, producto_id, tipo, existencia_al_capturar,
               contado, delta, motivo_codigo, nota, usuario_id, creado_en)
            VALUES (:id, :folio, :a, :p, :tipo, :actual, :contado, :delta,
                    :motivo, :nota, :quien, :ahora)
            """
        ),
        {
            "id": ajuste_id,
            "folio": folio,
            "a": almacen_id,
            "p": producto_id,
            "tipo": tipo,
            "actual": actual,
            "contado": contado,
            "delta": delta,
            "motivo": motivo_codigo or None,
            "nota": nota[:600],
            "quien": quien,
            "ahora": ahora,
        },
    )

    # El asiento en el libro mayor y la caché, juntos y siempre: si divergieran, la
    # propia pantalla de inventario marcaría un descuadre que no corresponde a nada
    # físico.
    #
    # El tipo del asiento es `ajuste` para los tres casos. Por qué se ajustó vive en
    # el documento, que es donde se puede leer con su nota; un `merma` en el libro
    # mayor del camión no entraría de todos modos en la columna de merma de la
    # liquidación, que se calcula desde los documentos de merma del vendedor.
    sube = delta > 0
    await sesion.execute(
        text(
            """
            INSERT INTO movimientos_inventario
              (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
               documento_tipo, documento_id, usuario_id, fecha_servidor)
            VALUES ('ajuste', :origen, :destino, :p, :cant, 'ajuste_camion', :doc,
                    :quien, :ahora)
            """
        ),
        {
            "origen": None if sube else almacen_id,
            "destino": almacen_id if sube else None,
            "p": producto_id,
            "cant": abs(delta),
            "doc": ajuste_id,
            "quien": quien,
            "ahora": ahora,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
            VALUES (:a, :p, :cant, :ahora)
            ON CONFLICT (almacen_id, producto_id) DO UPDATE
               SET cantidad = existencias.cantidad + :cant, actualizado_en = :ahora
            """
        ),
        {"a": almacen_id, "p": producto_id, "cant": delta, "ahora": ahora},
    )
    return folio
