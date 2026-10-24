"""El corte de una carga cuenta desde su día hasta la siguiente carga (ADR 0002 §176).

«En la app, en el corte me aparece que vendió 0 de todo», «el sistema espera 0, y
entregó 2,992: diferencia 2,992», «y en el Excel también aparece como que sobró
ese dinero». El corte contaba solo lo del día EXACTO de su carga, y la carga se
hace con la fecha de mañana (§82): la que se acepta el sábado queda del domingo,
el lunes se vende con fecha del lunes, y el corte del lunes no encontraba nada.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_panel_liquidacion import (
    _abrir,
    _entrar,
    dia_de_trabajo,  # noqa: F401 — fixture
)

pytestmark = pytest.mark.asyncio


async def _corte(sesion, liquidacion) -> tuple[Decimal, Decimal]:
    vendida = (
        await sesion.execute(
            text("SELECT cant_vendida FROM liquidacion_detalle WHERE liquidacion_id = :l"),
            {"l": uuid.UUID(liquidacion)},
        )
    ).scalar_one()
    esperado = (
        await sesion.execute(
            text("SELECT efectivo_esperado FROM liquidaciones WHERE id = :l"),
            {"l": uuid.UUID(liquidacion)},
        )
    ).scalar_one()
    return vendida, esperado


async def test_la_carga_del_domingo_cuenta_lo_vendido_el_lunes(
    cliente, semilla, dia_de_trabajo, sesion  # noqa: F811
):
    # La carga se aceptó el sábado para «mañana», el domingo; la venta es del lunes.
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": dia_de_trabajo["dia"] - timedelta(days=1), "c": dia_de_trabajo["carga"]},
    )
    await sesion.commit()
    await _entrar(cliente)

    liquidacion = await _abrir(cliente, dia_de_trabajo["carga"])

    assert await _corte(sesion, liquidacion) == (Decimal("180.000"), Decimal("2250.00"))


async def test_lo_de_la_siguiente_carga_no_se_cuenta_dos_veces(
    cliente, semilla, dia_de_trabajo, sesion  # noqa: F811
):
    """La venta de hoy es de la carga de hoy, no de la de ayer."""
    ayer = dia_de_trabajo["dia"] - timedelta(days=1)
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": ayer, "c": dia_de_trabajo["carga"]},
    )
    la_de_hoy = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, ruta_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (:id, 'CG-HOY', :b, :c, :v, :r, :d, 'confirmada', now())"
        ),
        {"id": la_de_hoy, "b": semilla["bodega"], "c": semilla["camion"],
         "v": semilla["vendedor"], "r": semilla["ruta"], "d": dia_de_trabajo["dia"]},
    )
    await sesion.commit()
    await _entrar(cliente)

    la_de_ayer = await _abrir(cliente, dia_de_trabajo["carga"])
    assert await _corte(sesion, la_de_ayer) == (Decimal("0.000"), Decimal("0.00"))

    lista = await cliente.get("/panel/liquidaciones")
    assert "CG-HOY" in lista.text


async def test_la_carga_de_mañana_cuenta_lo_de_hoy_si_la_anterior_ya_se_corto(
    cliente, semilla, dia_de_trabajo, sesion  # noqa: F811
):
    """El vendedor hizo el corte de ayer hoy en la mañana: su carga quedó «para
    mañana» y lo que vendió hoy quedaba antes de su fecha."""
    hoy = dia_de_trabajo["dia"]
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": hoy + timedelta(days=1), "c": dia_de_trabajo["carga"]},
    )
    # La de ayer, ya cortada.
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, ruta_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (gen_random_uuid(), 'CG-AYER', :b, :c, :v, :r, :d, 'liquidada', now())"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "v": semilla["vendedor"],
         "r": semilla["ruta"], "d": hoy - timedelta(days=1)},
    )
    await sesion.commit()
    await _entrar(cliente)

    liquidacion = await _abrir(cliente, dia_de_trabajo["carga"])

    assert await _corte(sesion, liquidacion) == (Decimal("180.000"), Decimal("2250.00"))


async def test_en_el_excel_la_carga_sale_el_dia_en_que_se_vendio(
    cliente, semilla, dia_de_trabajo, sesion  # noqa: F811
):
    """«Que en el mismo día venga la carga que hizo, para llevar el inventario día
    a día» (§177): la del domingo se vendió el lunes, y sale el lunes."""
    from app.infra.reporte_excel import _con_dia
    from tests.test_reporte_excel import _bajar, _con

    hoy = dia_de_trabajo["dia"]
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": hoy - timedelta(days=1), "c": dia_de_trabajo["carga"]},
    )
    await sesion.commit()
    await _entrar(cliente)

    _, libro = await _bajar(cliente, "semana")

    dia_a_dia = libro["Día por día"]
    titulos = [f[0] for f in dia_a_dia.iter_rows(values_only=True) if f and f[0]]
    assert f"{_con_dia(hoy)} · Juan Pérez" in titulos
    assert f"{_con_dia(hoy - timedelta(days=1))} · Juan Pérez" not in titulos
    assert _con(dia_a_dia, "ATUN-140")[2:7] == ["Atún en agua 140 g", 240, 180, 0, 2250]
