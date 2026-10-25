"""El vendedor vende antes de bajar la carga que ya le aceptaron (ADR 0002 §176).

«El vendedor ayer viernes hizo su corte; solo faltó actualizar para que le
subiera al camión, porque el gerente ya lo había aceptado.» El sábado vendió con
el teléfono sin la carga nueva: cada venta lleva la carga que el teléfono
conocía, la del viernes, ya cortada. Y si hace el corte antes de sincronizar, el
corte también la lleva: el teléfono sube primero y baja después.

Pase lo que pase con esa carga vieja, el corte del sábado es el de la carga del
sábado, y cuenta lo que vendió el sábado.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import obtener_manejador
from tests.test_cierre_del_vendedor import MOMENTO, _aceptar, _ctx, _solicitud, dia  # noqa: F401
from tests.test_cortes_api import _cab

pytestmark = pytest.mark.asyncio


async def _el_viernes(sesion, cliente, semilla, datos) -> tuple[uuid.UUID, uuid.UUID]:
    """El viernes: vendió 180 ($2,250), hizo su corte y el gerente aceptó la carga
    del sábado. Devuelve la carga del viernes y la del sábado."""
    viernes = datos["dia"] - timedelta(days=1)
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": viernes, "c": datos["carga"]},
    )
    await sesion.execute(
        text("UPDATE ventas SET fecha_operativa = :d WHERE id = :v"),
        {"d": viernes, "v": datos["venta"]},
    )
    await sesion.commit()
    corte = uuid.uuid4()
    await obtener_manejador("corte.crear")(
        sesion, _ctx(datos, semilla), corte,
        {"fecha_operativa": viernes.isoformat(), "carga_id": str(datos["carga"]),
         "efectivo_declarado": "2250.00", "observaciones": None,
         "fecha_dispositivo": MOMENTO},
    )
    await sesion.commit()
    sid = await _solicitud(sesion, datos, semilla, corte_id=corte, para=datos["dia"])
    r = await _aceptar(cliente, sid, await _cab(cliente, "GER01"))
    assert r.status_code == 200, r.text
    await sesion.rollback()
    sabado = (
        await sesion.execute(
            text("SELECT id FROM cargas WHERE vendedor_id = :v AND fecha_operativa = :d"),
            {"v": semilla["vendedor"], "d": datos["dia"]},
        )
    ).scalar_one()
    return datos["carga"], sabado


async def _vende_el_sabado(sesion, semilla, datos, *, carga) -> None:
    """100 piezas, $1,250, con la carga que el teléfono conocía."""
    venta = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, carga_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 2, 'VEND01-000002', :c, :u, :a, :carga, 'contado',
                    1250.00, 1250.00, now(), :dia)
            """
        ),
        {"v": venta, "d": datos["dispositivo"], "c": datos["cliente"], "u": semilla["vendedor"],
         "a": semilla["camion"], "carga": carga, "dia": datos["dia"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, 100.000, 100.000, 12.5000, 1250.00)
            """
        ),
        {"id": uuid.uuid4(), "v": venta, "p": datos["producto"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario "
            "  (tipo, almacen_origen_id, producto_id, cantidad, documento_tipo, documento_id) "
            "VALUES ('venta', :c, :p, 100, 'venta', :doc)"
        ),
        {"c": semilla["camion"], "p": datos["producto"], "doc": venta},
    )
    await sesion.execute(
        text("UPDATE existencias SET cantidad = cantidad - 100 "
             " WHERE almacen_id = :c AND producto_id = :p"),
        {"c": semilla["camion"], "p": datos["producto"]},
    )
    await sesion.commit()


async def _corte_del_sabado(cliente, sesion, semilla, datos, *, carga) -> dict:
    """El corte del sábado, con la carga que dice el teléfono, y el gerente
    acepta la de mañana. Devuelve la liquidación con la que quedó."""
    corte = uuid.uuid4()
    await obtener_manejador("corte.crear")(
        sesion, _ctx(datos, semilla), corte,
        {"fecha_operativa": datos["dia"].isoformat(),
         "carga_id": str(carga) if carga else None,
         "efectivo_declarado": "1250.00", "observaciones": None,
         "fecha_dispositivo": MOMENTO},
    )
    await sesion.commit()
    sid = await _solicitud(sesion, datos, semilla, corte_id=corte)
    r = await _aceptar(cliente, sid, await _cab(cliente, "GER01"))
    assert r.status_code == 200, r.text
    await sesion.rollback()
    return dict(
        (
            await sesion.execute(
                text(
                    """
                    SELECT k.estado, l.carga_id, l.efectivo_esperado, l.efectivo_entregado,
                           (SELECT sum(cant_vendida) FROM liquidacion_detalle
                             WHERE liquidacion_id = l.id) AS vendida
                      FROM cortes_vendedor k
                      LEFT JOIN liquidaciones l ON l.id = k.liquidacion_id
                     WHERE k.id = :k
                    """
                ),
                {"k": corte},
            )
        ).mappings().one()
    )


@pytest.mark.parametrize("con_cual", ["la_nueva", "la_de_ayer", "ninguna"])
async def test_el_corte_del_sabado_es_el_de_la_carga_del_sabado(
    cliente, sesion, semilla, dia, con_cual  # noqa: F811
):
    """Vendió con la carga del viernes en el teléfono. El corte lo hizo después
    de bajar la nueva (`la_nueva`), antes (`la_de_ayer`), o con el teléfono que
    ya había soltado la del viernes y no tenía ninguna (`ninguna`)."""
    viernes, sabado = await _el_viernes(sesion, cliente, semilla, dia)
    await _vende_el_sabado(sesion, semilla, dia, carga=viernes)

    carga = {"la_nueva": sabado, "la_de_ayer": viernes, "ninguna": None}[con_cual]
    corte = await _corte_del_sabado(cliente, sesion, semilla, dia, carga=carga)

    assert corte["estado"] == "cerrado"
    assert corte["carga_id"] == sabado
    assert corte["vendida"] == Decimal("100.000")
    assert corte["efectivo_esperado"] == Decimal("1250.00")
    assert corte["efectivo_entregado"] == Decimal("1250.00")
