"""Una meta a todos los vendedores al mismo tiempo (ADR 0002 §113).

«También quiero en los objetivos poder aplicarle una meta a todos los
vendedores al mismo tiempo.»

En el dashboard, «A quién» trae «Todos los vendedores»: el MISMO objetivo
diario a cada ruta activa, cada una medida sola; una desactivada no recibe nada.
Desde §115 es el único objetivo que se captura: la semana, el mes y la empresa
se suman de él.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.objetivos_diarios import objetivos_diarios
from tests.conftest import csrf_del_panel, solo_texto
from tests.test_panel_objetivos import _entrar

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def tres_rutas(sesion, semilla):
    """R04 (la de la semilla), R05 activa y R06 desactivada."""
    for codigo, activa in (("R05", True), ("R06", False)):
        await sesion.execute(
            text("INSERT INTO rutas (id, codigo, nombre, vendedor_id, activo) "
                 "VALUES (:r, :c, :n, :v, :a)"),
            {"r": uuid.uuid4(), "c": codigo, "n": f"Ruta {codigo[1:]}",
             "v": semilla["vendedor"], "a": activa},
        )
    await sesion.commit()


async def test_el_diario_a_todos_los_vendedores(cliente, sesion, semilla, tres_rutas):  # noqa: ARG001
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    assert 'value="todas"' in r.text
    r = await cliente.post(
        "/panel/objetivos/diario",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": "todas", "objetivo_venta": "3500",
              "dias": ["1", "2", "3", "4", "5", "6", "7"], "desde": date.today().isoformat()},
        follow_redirects=True,
    )
    assert "Objetivo diario guardado para los 2 vendedores: $3,500.00 cada día" in solo_texto(r)
    diarios = await objetivos_diarios(sesion)
    assert [(o.ruta, o.vigente.objetivo_venta) for o in diarios] == [
        ("R04 · Ruta 4", Decimal("3500")), ("R05 · Ruta 05", Decimal("3500")),
    ]
