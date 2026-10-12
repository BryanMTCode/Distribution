"""Una meta a todos los vendedores al mismo tiempo (ADR 0002 §113).

«También quiero en los objetivos poder aplicarle una meta a todos los
vendedores al mismo tiempo.»

En el dashboard, «De quién» trae «Todos los vendedores»: la MISMA meta a cada
ruta activa, cada una medida sola. No es «Toda la empresa», que es una sola
meta para la suma de todas.

1. **El diario**: cada ruta con su objetivo diario.
2. **El del mes**: cada ruta con su objetivo del mes; pisa el que tuviera.
3. **Por periodo**: uno por ruta.
4. Una ruta desactivada no recibe nada.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.tablero import inicio_de_mes
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


async def test_el_del_mes_a_todos_pisa_el_que_tenian(cliente, sesion, semilla, tres_rutas):  # noqa: ARG001
    mes = inicio_de_mes(date.today())
    await sesion.execute(
        text("INSERT INTO objetivos_ruta (ruta_id, periodo, objetivo_venta) VALUES (:r, :p, 1000)"),
        {"r": semilla["ruta"], "p": mes},
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    assert 'id="forma_mes_a_todas"' in r.text
    r = await cliente.post(
        "/panel/objetivos/fijar-todas",
        data={"csrf": csrf_del_panel(cliente, r), "mes": f"{mes:%Y-%m}",
              "objetivo_venta": "90,000", "objetivo_visitas": "300"},
        follow_redirects=True,
    )
    assert "guardado para las 2 rutas: $90,000.00 a cada vendedor." in solo_texto(r)
    filas = (
        await sesion.execute(
            text("SELECT r.codigo, o.objetivo_venta, o.objetivo_visitas FROM objetivos_ruta o "
                 "JOIN rutas r ON r.id = o.ruta_id WHERE o.periodo = :p ORDER BY r.codigo"),
            {"p": mes},
        )
    ).all()
    assert [tuple(f) for f in filas] == [
        ("R04", Decimal("90000.00"), 300), ("R05", Decimal("90000.00"), 300),
    ]

    # En blanco no borra a todos: lo dice.
    r = await cliente.post(
        "/panel/objetivos/fijar-todas",
        data={"csrf": csrf_del_panel(cliente, r), "mes": f"{mes:%Y-%m}", "objetivo_venta": ""},
        follow_redirects=True,
    )
    assert "Escribe cuánto tiene que vender cada vendedor en el mes." in solo_texto(r)
    assert (
        await sesion.execute(text("SELECT count(*) FROM objetivos_ruta"))
    ).scalar_one() == 2


async def test_por_periodo_uno_para_cada_vendedor(cliente, sesion, semilla, tres_rutas):  # noqa: ARG001
    hoy = date.today()
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    r = await cliente.post(
        "/panel/objetivos/periodo",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": "todas", "desde": hoy.isoformat(),
              "hasta": (hoy + timedelta(days=6)).isoformat(), "objetivo_venta": "20000"},
        follow_redirects=True,
    )
    assert "2 objetivos guardados: $20,000.00 a cada vendedor." in solo_texto(r)
    codigos = (
        await sesion.execute(
            text("SELECT r.codigo FROM objetivos o JOIN rutas r ON r.id = o.ruta_id "
                 "ORDER BY r.codigo")
        )
    ).scalars().all()
    assert codigos == ["R04", "R05"]
