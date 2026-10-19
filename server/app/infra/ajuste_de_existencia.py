"""Registrar un ajuste de existencia: el documento, el asiento y la caché.

Lo usan el ajuste a mano del Inventario del panel y la carga del estado de los
camiones desde el Excel de la dirección (`camiones_al_sabado.py`, ADR 0002 §99).
Vive aparte para que los dos escriban exactamente lo mismo.

El ajuste de un camión viaja al teléfono de su dueño —el disparador de
`ajustes_camion` publica la diferencia firmada (migración 0037)—; el de una
bodega no viaja a ninguno.

Una captura de varios artículos —el conteo, mover mercancía— es UNA operación
(ADR 0002 §146): sus ajustes van juntos con `UnaSolaOperacion`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text


@dataclass
class UnaSolaOperacion:
    """Los ajustes de una captura de varios artículos, juntos (ADR 0002 §146).

    «En general las operaciones son en conjunto: si hago una entrada, no quiero
    que haya una por cada artículo.» El ajuste es de un artículo, así que un
    conteo de 80 artículos son 80 ajustes; lo que los hace una operación es esto.
    El primero es la **cabeza**: lleva el folio de la operación y es el renglón
    que enseña Operaciones (§119). Los demás apuntan a él (`pareja_id`, el mismo
    amarre de las dos mitades de un traspaso, §135) y llevan su número con el del
    renglón —AC-000120-2, AC-000120-3…—: se ve que son de la misma. Anular la
    cabeza los deshace todos.

    Se crea vacío, uno por captura, y `registrar_ajuste` lo va llenando.
    """

    cabeza_id: uuid.UUID | None = None
    numero: int | None = None
    folio: str | None = None
    renglones: int = 0


async def folio_del_ajuste(
    sesion, *, prefijo: str, ajuste_id: uuid.UUID, operacion: UnaSolaOperacion | None
) -> tuple[str, uuid.UUID | None]:
    """El folio del ajuste y la cabeza a la que apunta (None si él es la cabeza
    o va solo). AC para camión, AB para bodega: el folio dice de dónde es sin
    abrir el documento. La serie es una sola."""
    if operacion is not None and operacion.cabeza_id is not None:
        operacion.renglones += 1
        return f"{prefijo}-{operacion.numero:06d}-{operacion.renglones}", operacion.cabeza_id
    consecutivo = (
        await sesion.execute(text("SELECT nextval('seq_folio_ajuste_camion')"))
    ).scalar_one()
    folio = f"{prefijo}-{consecutivo:06d}"
    if operacion is not None:
        operacion.cabeza_id, operacion.numero = ajuste_id, consecutivo
        operacion.folio, operacion.renglones = folio, 1
    return folio, None


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
    operacion: UnaSolaOperacion | None = None,
) -> str:
    """Escribe el ajuste y devuelve su folio (AC- camión, AB- bodega). No hace
    commit: quien llama decide la transacción.

    Con `operacion`, va junto con los demás ajustes de la misma captura (§146);
    sin ella, es una operación por sí solo."""
    ahora = datetime.now(UTC)
    ajuste_id = uuid.uuid4()
    folio, pareja_id = await folio_del_ajuste(
        sesion, prefijo="AC" if es_camion else "AB", ajuste_id=ajuste_id, operacion=operacion
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ajustes_camion
              (id, folio, almacen_id, producto_id, tipo, existencia_al_capturar,
               contado, delta, motivo_codigo, nota, usuario_id, creado_en, pareja_id)
            VALUES (:id, :folio, :a, :p, :tipo, :actual, :contado, :delta,
                    :motivo, :nota, :quien, :ahora, :pareja)
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
            "pareja": pareja_id,
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
