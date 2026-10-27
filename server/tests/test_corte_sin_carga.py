"""El corte sin carga también se cuadra contra lo vendido (ADR 0002 §182).

«También puede pasar que no soliciten carga.» El vendedor corta, no pide carga,
y al día siguiente sale a vender con lo que le quedó. Ese día no tenía carga que
cortar: su corte se quedaba de constancia, sin cuadrar, y el corte de su
siguiente carga contaba ese día sin el efectivo que ya había entregado. Lo mismo
el primer día de un vendedor nuevo, que sale con el conteo de arranque.

Ahora el sistema le hace la carga de ese día sin mercancía, y el corte la cierra
como a cualquiera.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import obtener_manejador
from tests.test_cierre_del_vendedor import MOMENTO, _cerrar, _ctx, dia  # noqa: F401
from tests.test_corte_sin_bajar_la_carga import _como_quedo, _vende_el_sabado
from tests.test_cortes_api import _cab
from tests.test_el_dia_de_en_medio import _carga_del, _cargado_a_su_cuenta, _corte

pytestmark = pytest.mark.asyncio


async def _corte_sin_pedir_carga(cliente, sesion, semilla, datos, *, el_dia, carga, entrega):
    """El corte de ese día, y el vendedor no pide carga: el gerente lo cierra solo."""
    corte = uuid.uuid4()
    await obtener_manejador("corte.crear")(
        sesion, _ctx(datos, semilla), corte,
        {"fecha_operativa": el_dia.isoformat(), "carga_id": str(carga),
         "efectivo_declarado": entrega, "observaciones": None,
         "fecha_dispositivo": MOMENTO},
    )
    await sesion.commit()
    cierre = await _cerrar(cliente, corte, await _cab(cliente, "GER01"))
    await sesion.rollback()
    return cierre, await _como_quedo(sesion, corte)


async def _cargas(sesion, semilla) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    "SELECT c.folio, c.fecha_operativa, c.estado, "
                    "       (SELECT count(*) FROM carga_detalle d WHERE d.carga_id = c.id) "
                    "         AS renglones "
                    "  FROM cargas c WHERE c.vendedor_id = :v ORDER BY c.fecha_operativa"
                ),
                {"v": semilla["vendedor"]},
            )
        ).mappings()
    ]


async def test_corta_sin_pedir_carga_y_al_otro_dia_vende_lo_que_le_quedo(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """Lunes corta y no pide carga. Martes vende todo lo que le quedó (60 piezas,
    $750), corta y pide la del miércoles. Miércoles vende y corta. Cada corte
    espera lo de su día, y no se le carga nada."""
    miercoles = dia["dia"]
    martes, lunes = miercoles - timedelta(days=1), miercoles - timedelta(days=2)
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": lunes, "c": dia["carga"]},
    )
    await sesion.execute(
        text("UPDATE ventas SET fecha_operativa = :d WHERE id = :v"),
        {"d": lunes, "v": dia["venta"]},
    )
    await sesion.commit()

    _, del_lunes = await _corte_sin_pedir_carga(cliente, sesion, semilla, dia, el_dia=lunes,
                                                carga=dia["carga"], entrega="2250.00")
    assert del_lunes["efectivo_esperado"] == Decimal("2250.00")

    # El teléfono sigue con la carga del lunes: es la última que conoce.
    await _vende_el_sabado(sesion, semilla, dia, carga=dia["carga"], el_dia=martes, piezas=60)
    del_martes = await _corte(cliente, sesion, semilla, dia, el_dia=martes,
                              carga=dia["carga"], entrega="750.00", para=miercoles)
    assert del_martes["carga_id"] != dia["carga"]
    assert (del_martes["efectivo_esperado"], del_martes["efectivo_entregado"]) == (
        Decimal("750.00"), Decimal("750.00")
    )
    # Se le acabó: el artículo sale en el corte aunque ya no quede.
    assert del_martes["vendida"] == Decimal("60.000")

    la_del_miercoles = await _carga_del(sesion, semilla, miercoles)
    await _vende_el_sabado(sesion, semilla, dia, carga=la_del_miercoles, el_dia=miercoles,
                           piezas=10, folio=3)
    del_miercoles = await _corte(cliente, sesion, semilla, dia, el_dia=miercoles,
                                 carga=la_del_miercoles, entrega="125.00",
                                 para=miercoles + timedelta(days=1))
    assert del_miercoles["efectivo_esperado"] == Decimal("125.00")
    assert await _cargado_a_su_cuenta(sesion, semilla) == Decimal("0")

    # El martes tiene su carga, sin mercancía y ya cortada.
    cargas = await _cargas(sesion, semilla)
    assert [(c["fecha_operativa"], c["estado"], c["renglones"]) for c in cargas] == [
        (lunes, "liquidada", 1),
        (martes, "liquidada", 0),
        (miercoles, "liquidada", 1),
        (miercoles + timedelta(days=1), "confirmada", 1),
    ]


async def test_el_primer_dia_sin_carga_se_cuadra_contra_lo_vendido(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """El vendedor nuevo sale con el conteo de arranque, sin carga. Vendió $2,250
    y entregó $2,200: faltan $50, y van a su cuenta, como en cualquier corte."""
    await sesion.execute(
        text("UPDATE cargas SET estado = 'cancelada' WHERE id = :c"), {"c": dia["carga"]}
    )
    await sesion.commit()

    cierre, quedo = await _corte_sin_pedir_carga(cliente, sesion, semilla, dia,
                                                 el_dia=dia["dia"], carga=dia["carga"],
                                                 entrega="2200.00")

    assert quedo["estado"] == "cerrado"
    assert (quedo["efectivo_esperado"], quedo["efectivo_entregado"]) == (
        Decimal("2250.00"), Decimal("2200.00")
    )
    assert await _cargado_a_su_cuenta(sesion, semilla) == Decimal("50.00")
    assert "sin mercancía" in str(cierre)


async def test_si_la_oficina_ya_corto_ese_dia_se_queda_ese_corte(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """La oficina cortó la carga de hoy a mano antes de que llegara el corte del
    vendedor: ese cierre manda, y no se hace otra carga."""
    from app.api.admin.liquidaciones import abrir_corte, cerrar_corte, guardar_arqueo

    lq = await abrir_corte(sesion, dia["carga"])
    await guardar_arqueo(sesion, lq, "2250", "")
    await cerrar_corte(sesion, lq, quien=semilla["admin"], confirmo_sincronizado=True,
                       conteo_automatico=True)

    cierre, quedo = await _corte_sin_pedir_carga(cliente, sesion, semilla, dia,
                                                 el_dia=dia["dia"], carga=dia["carga"],
                                                 entrega="2250.00")

    assert quedo["liquidacion_id"] == lq
    assert "ya estaba cortada" in str(cierre)
    assert len(await _cargas(sesion, semilla)) == 1
