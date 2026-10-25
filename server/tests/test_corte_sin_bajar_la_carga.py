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


async def _vende_el_sabado(
    sesion, semilla, datos, *, carga, el_dia=None, piezas=100, folio=2
) -> None:
    """100 piezas, $1,250, con la carga que el teléfono conocía."""
    venta = uuid.uuid4()
    importe = Decimal(piezas) * Decimal("12.50")
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, carga_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, :folio, :folio_local, :c, :u, :a, :carga, 'contado',
                    :importe, :importe, now(), :dia)
            """
        ),
        {"v": venta, "d": datos["dispositivo"], "c": datos["cliente"], "u": semilla["vendedor"],
         "a": semilla["camion"], "carga": carga, "dia": el_dia or datos["dia"],
         "folio": folio, "folio_local": f"VEND01-{folio:06d}", "importe": importe},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, :n, :n, 12.5000, :importe)
            """
        ),
        {"id": uuid.uuid4(), "v": venta, "p": datos["producto"], "n": piezas,
         "importe": importe},
    )
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario "
            "  (tipo, almacen_origen_id, producto_id, cantidad, documento_tipo, documento_id) "
            "VALUES ('venta', :c, :p, :n, 'venta', :doc)"
        ),
        {"c": semilla["camion"], "p": datos["producto"], "doc": venta, "n": piezas},
    )
    await sesion.execute(
        text("UPDATE existencias SET cantidad = cantidad - :n "
             " WHERE almacen_id = :c AND producto_id = :p"),
        {"c": semilla["camion"], "p": datos["producto"], "n": piezas},
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
    return await _como_quedo(sesion, corte)


async def _como_quedo(sesion, corte) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    """
                    SELECT k.id AS corte_id, k.estado, l.id AS liquidacion_id, l.carga_id,
                           l.efectivo_esperado, l.efectivo_entregado,
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


# ---------------------------------------------------------------------------
# Rehacer el corte que se cerró con 0, con la carga del lunes ya en el teléfono
# ---------------------------------------------------------------------------
# «Si hago la modificación del corte, ¿no le afectará para la operación del
# lunes? Él se conectará hasta la tarde, que termine el día.»

async def _el_sabado_salio_en_cero(cliente, sesion, semilla, datos) -> tuple[dict, uuid.UUID]:
    """El sábado bajó su carga en la mañana, vendió 100 y su corte se cerró como
    lo cerraba el código de antes: esperado 0, vendido 0. La carga del lunes ya
    se aceptó. Devuelve el corte y la carga del lunes."""
    _, sabado = await _el_viernes(sesion, cliente, semilla, datos)
    await _vende_el_sabado(sesion, semilla, datos, carga=sabado)
    corte = await _corte_del_sabado(cliente, sesion, semilla, datos, carga=sabado)
    await sesion.execute(
        text("UPDATE liquidaciones SET efectivo_esperado = 0 WHERE id = :l"),
        {"l": corte["liquidacion_id"]},
    )
    await sesion.execute(
        text("UPDATE liquidacion_detalle SET cant_vendida = 0 WHERE liquidacion_id = :l"),
        {"l": corte["liquidacion_id"]},
    )
    await sesion.commit()
    lunes = (
        await sesion.execute(
            text("SELECT id FROM cargas WHERE vendedor_id = :v AND fecha_operativa = :d"),
            {"v": semilla["vendedor"], "d": datos["dia"] + timedelta(days=1)},
        )
    ).scalar_one()
    return corte, lunes


async def _gerente(sesion) -> uuid.UUID:
    return (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'GER01'"))
    ).scalar_one()


async def _eliminar(sesion, liquidacion_id) -> None:
    from app.infra.operaciones import anular

    await anular(sesion, "corte", liquidacion_id,
                 motivo="El corte salió con lo esperado en cero",
                 quien=await _gerente(sesion))
    await sesion.commit()


async def _volver_a_cerrar(sesion, corte_id) -> dict:
    from app.infra.cierre_del_vendedor import cerrar_corte_del_vendedor

    await cerrar_corte_del_vendedor(
        sesion, corte_id, quien=await _gerente(sesion), efectivo_recibido="1250.00"
    )
    await sesion.commit()
    return await _como_quedo(sesion, corte_id)


async def _el_lunes(sesion, semilla, datos, lunes) -> dict:
    """Vendió 40 ($500) sin señal; en la tarde se conecta, sube todo y su corte
    se cierra."""
    from app.infra.cierre_del_vendedor import cerrar_corte_del_vendedor

    el_lunes = datos["dia"] + timedelta(days=1)
    await _vende_el_sabado(
        sesion, semilla, datos, carga=lunes, el_dia=el_lunes, piezas=40, folio=3
    )
    await sesion.execute(
        text("UPDATE dispositivos SET ultima_sync_push_en = now() + interval '1 day' "
             " WHERE id = :d"),
        {"d": datos["dispositivo"]},
    )
    await sesion.commit()
    corte = uuid.uuid4()
    await obtener_manejador("corte.crear")(
        sesion, _ctx(datos, semilla), corte,
        {"fecha_operativa": el_lunes.isoformat(), "carga_id": str(lunes),
         "efectivo_declarado": "500.00", "observaciones": None,
         "fecha_dispositivo": MOMENTO},
    )
    await sesion.commit()
    await cerrar_corte_del_vendedor(sesion, corte, quien=await _gerente(sesion))
    await sesion.commit()
    return await _como_quedo(sesion, corte)


async def test_rehacer_el_corte_no_le_mueve_el_lunes(cliente, sesion, semilla, dia):  # noqa: F811
    corte, lunes = await _el_sabado_salio_en_cero(cliente, sesion, semilla, dia)
    desde = (await sesion.execute(text("SELECT max(cursor) FROM change_log"))).scalar_one()

    await _eliminar(sesion, corte["liquidacion_id"])
    rehecho = await _volver_a_cerrar(sesion, corte["corte_id"])

    assert rehecho["efectivo_esperado"] == Decimal("1250.00")
    assert rehecho["vendida"] == Decimal("100.000")
    assert rehecho["efectivo_entregado"] == Decimal("1250.00")

    # Al teléfono, que lo baja todo junto el lunes en la tarde: la última carga
    # viva que le llega es la del lunes, y con ella siguen sus ventas.
    vivas = (
        await sesion.execute(
            text("SELECT entidad_id FROM change_log "
                 " WHERE entidad = 'carga' AND cursor > :c "
                 "   AND payload->>'estado' IN ('confirmada', 'en_ruta') ORDER BY cursor"),
            {"c": desde},
        )
    ).scalars().all()
    assert vivas[-1] == lunes

    # Y el corte del lunes cuenta solo lo del lunes.
    del_lunes = await _el_lunes(sesion, semilla, dia, lunes)
    assert del_lunes["carga_id"] == lunes
    assert del_lunes["vendida"] == Decimal("40.000")
    assert del_lunes["efectivo_esperado"] == Decimal("500.00")


async def test_si_se_elimina_y_se_cierra_hasta_despues_del_lunes(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """Eliminado el sábado y sin volver a cerrar, el corte del lunes no se lleva
    lo del sábado: esos días siguen siendo de su carga, que espera su corte."""
    corte, lunes = await _el_sabado_salio_en_cero(cliente, sesion, semilla, dia)
    await _eliminar(sesion, corte["liquidacion_id"])

    del_lunes = await _el_lunes(sesion, semilla, dia, lunes)
    assert del_lunes["vendida"] == Decimal("40.000")
    assert del_lunes["efectivo_esperado"] == Decimal("500.00")

    rehecho = await _volver_a_cerrar(sesion, corte["corte_id"])
    assert rehecho["vendida"] == Decimal("100.000")
    assert rehecho["efectivo_esperado"] == Decimal("1250.00")
