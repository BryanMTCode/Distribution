"""Las ventas a tiendas que no tocaban ese día, marcadas (ADR 0002 §167).

«Quiero que las ventas a tiendas que no tocan ese día se marquen de forma de
identificarlas; solo entrarían ventas normales las de ese día y las que son de
otros días pero se reagendaron esa semana para ese día.»

1. La de su día del plan entra limpia.
2. La de otro día entra igual (§0.1) y marcada «fuera de su día».
3. La que se reagendó para ese día («Ven mañana») cuenta como de su día.
4. La tienda sin plan —la que se acaba de dar de alta en la calle— no se marca.
5. El panel las junta en su pestaña, «Fuera de su día».

El día de la venta del escenario es el martes 29 de septiembre de 2026.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import MOTIVO_FUERA_DE_SU_DIA, obtener_manejador
from tests.test_plan_visita import _entrar
from tests.test_venta_ingesta import (  # noqa: F401 — `escenario` es fixture
    RELOJ_DISPOSITIVO,
    aplicar,
    contexto,
    escenario,
    leer_venta,
    payload,
)

pytestmark = pytest.mark.asyncio

MARTES, MIERCOLES = 2, 3


async def _plan(sesion, escenario, *dias: int) -> None:  # noqa: F811
    for dia in dias:
        await sesion.execute(
            text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana) VALUES (:c, :d)"),
            {"c": escenario["cliente"], "d": dia},
        )
    await sesion.commit()


async def _motivos(sesion, escenario) -> list[str]:  # noqa: F811
    venta = await aplicar(sesion, escenario, payload(escenario))
    await sesion.commit()
    return list((await leer_venta(sesion, venta))["revision_motivos"])


async def test_la_de_su_dia_entra_limpia(sesion, escenario):  # noqa: F811
    await _plan(sesion, escenario, MARTES)
    assert MOTIVO_FUERA_DE_SU_DIA not in await _motivos(sesion, escenario)


async def test_la_de_otro_dia_entra_marcada(sesion, escenario):  # noqa: F811
    await _plan(sesion, escenario, MIERCOLES)
    assert MOTIVO_FUERA_DE_SU_DIA in await _motivos(sesion, escenario)


async def test_la_reagendada_para_ese_dia_cuenta_como_de_su_dia(sesion, escenario):  # noqa: F811
    """El lunes no compró y pidió «ven mañana»: el martes es su día, aunque su día
    fijo sea el miércoles."""
    await _plan(sesion, escenario, MIERCOLES)
    await obtener_manejador("no_drop.crear")(
        sesion,
        contexto(escenario),
        uuid.uuid4(),
        {
            "folio_consecutivo": 1,
            "cliente_id": str(escenario["cliente"]),
            "motivo_codigo": "CERRADO",
            "fecha_dispositivo": RELOJ_DISPOSITIVO,
            "fecha_operativa": "2026-09-28",
            "reprogramada_para": "2026-09-29",
        },
    )
    await sesion.commit()

    assert MOTIVO_FUERA_DE_SU_DIA not in await _motivos(sesion, escenario)


async def test_la_tienda_sin_plan_no_se_marca(sesion, escenario):  # noqa: F811
    assert MOTIVO_FUERA_DE_SU_DIA not in await _motivos(sesion, escenario)


async def test_el_panel_las_junta_en_su_pestana(cliente, sesion, escenario):  # noqa: F811
    await _plan(sesion, escenario, MIERCOLES)
    await _motivos(sesion, escenario)
    await _entrar(cliente)

    r = await cliente.get("/panel/ventas?ver=fuera_de_dia")

    assert "Ventas fuera de su día" in r.text
    assert "VEND01-000124" in r.text
    assert "Fuera de su día (1)" in r.text
    assert 'class="etiqueta-motivo">Fuera de su día' in r.text
