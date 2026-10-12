"""El objetivo diario fijo, solo en el panel (ADR 0002 §110).

«Es muy impráctico agregar los objetivos así; agrega que pueda poner por día
3,500 hasta que yo lo modifique.» Y: «todo esto en el dashboard nada más».

1. **Se pone una vez y vale hasta que se cambie**: cada día se mide contra él,
   y el tablero lo pone contra lo vendido hoy.
2. **Cambiarlo no reescribe la historia**: lo de ayer se queda medido contra el
   que había; un cambio a futuro se ve y se cancela.
3. **Quitarlo** es desde hoy; los días pasados se quedan.
4. **Los días que no aplica** son descanso, no fallados.
5. **Lo imposible se dice**, y un supervisor lo ve pero no lo fija.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.objetivos_diarios import objetivos_diarios
from tests.conftest import csrf_del_panel, solo_texto
from tests.test_objetivos_por_periodo import _venta
from tests.test_panel_objetivos import _crear_usuario, _entrar

pytestmark = pytest.mark.asyncio

TODOS = ["1", "2", "3", "4", "5", "6", "7"]


async def _fijar(cliente, r, **datos):
    return await cliente.post(
        "/panel/objetivos/diario",
        data={"csrf": csrf_del_panel(cliente, r), **datos},
        follow_redirects=True,
    )


async def test_por_dia_3500_hasta_que_lo_cambie(cliente, sesion, semilla):
    hoy = date.today()
    ruta = str(semilla["ruta"])
    await _venta(sesion, semilla, total="4000.00", dia=hoy - timedelta(days=1))
    await _venta(sesion, semilla, total="2000.00", dia=hoy)
    await _entrar(cliente)

    r = await cliente.get("/panel/objetivos")
    assert 'id="sin_diario"' in r.text and 'id="forma_diario"' in r.text
    # Hace una semana: «3,500 cada día», todos los días.
    r = await _fijar(cliente, r, ruta_id=ruta, objetivo_venta="3,500", dias=TODOS,
                     desde=(hoy - timedelta(days=6)).isoformat())
    assert (
        "Objetivo diario guardado: $3,500.00 cada día, todos los días, "
        f"desde el {hoy - timedelta(days=6):%d/%m/%Y}. Vale hasta que lo cambies."
    ) in solo_texto(r)

    [o] = await objetivos_diarios(sesion)
    assert o.ruta == "R04 · Ruta 4" and o.vigente.objetivo_venta == Decimal("3500")
    ayer, de_hoy = o.dias[-2], o.hoy
    assert (ayer.venta, ayer.cumplido) == (Decimal("4000.00"), True)
    assert (de_hoy.venta, de_hoy.logrado, de_hoy.cumplido) == (
        Decimal("2000.00"), Decimal("57.1"), False
    )
    # Seis días terminados con objetivo; solo ayer se cumplió.
    assert (o.medidos, o.cumplidos) == (6, 1)
    assert "1 de 6 día(s) cumplido(s)" in solo_texto(r)

    # El tablero lo pone contra lo vendido hoy.
    tablero = await cliente.get("/panel")
    assert 'id="tabla_objetivo_hoy"' in tablero.text
    assert "R04 · Ruta 4 $3,500.00 $2,000.00 57.1% · faltan $1,500.00" in solo_texto(tablero)

    # Lo cambia a 4,000 desde hoy: ayer se queda medido contra 3,500.
    r = await _fijar(cliente, r, ruta_id=ruta, objetivo_venta="4000", dias=TODOS,
                     desde=hoy.isoformat())
    assert "desde hoy" in solo_texto(r)
    [o] = await objetivos_diarios(sesion)
    assert o.vigente.objetivo_venta == Decimal("4000")
    assert (o.dias[-2].objetivo, o.hoy.objetivo) == (Decimal("3500"), Decimal("4000"))
    # Guardar otra vez el mismo día lo corrige: no se duplica.
    r = await _fijar(cliente, r, ruta_id=ruta, objetivo_venta="4100", dias=TODOS,
                     desde=hoy.isoformat())
    cambios = (
        await sesion.execute(text("SELECT count(*) FROM objetivos_diarios"))
    ).scalar_one()
    assert cambios == 2

    # Un cambio a futuro se ve, y se cancela.
    r = await _fijar(cliente, r, ruta_id=ruta, objetivo_venta="5000", dias=TODOS,
                     desde=(hoy + timedelta(days=2)).isoformat())
    [o] = await objetivos_diarios(sesion)
    [futuro] = o.proximos
    assert o.vigente.objetivo_venta == Decimal("4100")
    assert f'id="cambio_{futuro.id}"' in r.text
    r = await cliente.post(
        f"/panel/objetivos/diario/{futuro.id}/cancelar",
        data={"csrf": csrf_del_panel(cliente, r)},
        follow_redirects=True,
    )
    assert "Cambio programado cancelado." in solo_texto(r)
    [o] = await objetivos_diarios(sesion)
    assert o.proximos == []

    # Quitarlo es desde hoy: la semana pasada se sigue viendo.
    r = await cliente.post(
        "/panel/objetivos/diario/quitar",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": ruta},
        follow_redirects=True,
    )
    assert "Objetivo diario quitado desde hoy." in solo_texto(r)
    [o] = await objetivos_diarios(sesion)
    assert o.vigente is None and o.hoy.objetivo is None
    assert o.dias[-2].objetivo == Decimal("3500")
    assert 'id="tabla_objetivo_hoy"' not in (await cliente.get("/panel")).text


async def test_los_dias_que_no_aplica_son_descanso(cliente, sesion, semilla):
    """El de toda la empresa, todos los días menos el de hoy: hoy descansa."""
    hoy = date.today()
    await _venta(sesion, semilla, total="1200.00", dia=hoy - timedelta(days=1))
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    otros = [d for d in TODOS if d != str(hoy.isoweekday())]
    r = await _fijar(cliente, r, ruta_id="", objetivo_venta="1000", dias=otros,
                     desde=(hoy - timedelta(days=1)).isoformat())
    assert "Objetivo diario guardado" in solo_texto(r)

    [o] = await objetivos_diarios(sesion)
    assert o.ruta is None
    assert (o.dias[-2].venta, o.dias[-2].cumplido) == (Decimal("1200.00"), True)
    assert (o.hoy.objetivo, o.hoy.descanso) == (None, True)
    assert "Toda la empresa" in solo_texto(r) and "descanso" in solo_texto(r)
    assert "Hoy no aplica" in solo_texto(await cliente.get("/panel"))

    # La empresa también se corrige el mismo día sin duplicarse.
    await _fijar(cliente, r, ruta_id="", objetivo_venta="1100", dias=otros,
                 desde=(hoy - timedelta(days=1)).isoformat())
    cambios = (
        await sesion.execute(text("SELECT count(*) FROM objetivos_diarios"))
    ).scalar_one()
    assert cambios == 1


async def test_lo_imposible_se_dice(cliente, sesion, semilla):
    hoy = date.today()
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    for datos, error in (
        ({"objetivo_venta": "3500"}, "Escoge al menos un día de la semana en que aplica."),
        ({"objetivo_venta": "", "dias": TODOS}, "Escribe cuánto se tiene que vender cada día."),
        ({"objetivo_venta": "3500", "dias": TODOS,
          "desde": (hoy - timedelta(days=40)).isoformat()}, "31 días atrás"),
    ):
        r = await _fijar(cliente, r, ruta_id="", **datos)
        assert error in solo_texto(r), datos
    assert (
        await sesion.execute(text("SELECT count(*) FROM objetivos_diarios"))
    ).scalar_one() == 0


async def test_un_supervisor_lo_ve_pero_no_lo_fija(cliente, sesion, semilla):
    await _crear_usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    r = await cliente.get("/panel/objetivos")
    assert r.status_code == 200 and 'id="forma_diario"' not in r.text
    prohibido = await cliente.post(
        "/panel/objetivos/diario",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": "", "objetivo_venta": "3500",
              "dias": TODOS},
        follow_redirects=False,
    )
    assert prohibido.status_code == 403
