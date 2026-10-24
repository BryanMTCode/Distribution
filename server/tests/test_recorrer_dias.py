"""Recorrer el plan de visita un día para atrás, sin tocar lo trabajado (ADR 0002 §175).

«Lo que iban a visitar el martes que lo visiten el lunes, lo del miércoles el
martes, y así. Que todo lo que han trabajado estos días siga como tal; solo que a
partir del lunes trabajen con las rutas actualizadas.»

1. **El recorrido**: el martes al lunes … el sábado al viernes, y el lunes al
   sábado; desde el lunes, con el plan de antes cerrado el domingo.
2. **Lo trabajado no cambia**: Efectividad, la venta «fuera de su día» y el mapa
   de la ruta miden los días de antes con el plan de antes.
3. **El teléfono y el panel** ven el plan nuevo, y a cada tienda le llega un solo
   cambio.
4. **No se recorre dos veces**, ni empezando hoy o antes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

# Un martes, un jueves, un sábado y un lunes, con días de dos en dos para que el
# martes nuevo choque con el martes de antes si se escribiera antes de cerrarlo.
PLANES = {"martes": [2, 3], "jueves": [4], "sabado": [6], "lunes": [1]}


def _proximo_lunes() -> date:
    hoy = date.today()
    return hoy + timedelta(days=7 - hoy.weekday())


async def _tiendas(sesion, semilla) -> dict[str, uuid.UUID]:
    ids = {}
    for nombre, dias in PLANES.items():
        ids[nombre] = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
                "                      actualizado_en) "
                "VALUES (:id, :c, :n, :r, now(), now())"
            ),
            {"id": ids[nombre], "c": f"RD-{nombre}", "n": nombre.upper(), "r": semilla["ruta"]},
        )
        for d in dias:
            # Un plan de hace un mes: hay historia que cuidar.
            await sesion.execute(
                text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana, desde) "
                     "VALUES (:c, :d, CURRENT_DATE - 30)"),
                {"c": ids[nombre], "d": d},
            )
    await sesion.commit()
    return ids


async def _vigente(sesion, cliente) -> list[int]:
    return list(
        (
            await sesion.execute(
                text("SELECT dia_semana FROM clientes_frecuencia "
                     " WHERE cliente_id = :c AND hasta IS NULL ORDER BY dia_semana"),
                {"c": cliente},
            )
        ).scalars()
    )


async def _recorrer(sesion, semilla, desde: date):
    from app.infra.recorrer_dias import ATRAS, aplicar

    return await aplicar(sesion, mapa=ATRAS, desde=desde, hoy=date.today(), una_vez=True,
                         quien=semilla["admin"])


async def _planear(sesion, desde: date):
    from app.infra.recorrer_dias import ATRAS, planear

    return await planear(sesion, mapa=ATRAS, desde=desde, hoy=date.today(), una_vez=True)


async def test_recorre_un_dia_para_atras_desde_el_lunes(sesion, semilla):
    ids = await _tiendas(sesion, semilla)
    lunes = _proximo_lunes()

    plan = await _planear(sesion, lunes)
    assert plan.se_puede and (plan.tiendas, plan.renglones) == (4, 5)
    antes, despues = plan.rutas["R04"]
    assert dict(antes) == {1: 1, 2: 1, 3: 1, 4: 1, 6: 1}
    assert dict(despues) == {6: 1, 1: 1, 2: 1, 3: 1, 5: 1}
    # El resumen no toca nada.
    assert await _vigente(sesion, ids["martes"]) == [2, 3]

    desde_cursor = (
        await sesion.execute(text("SELECT COALESCE(max(cursor), 0) FROM change_log"))
    ).scalar_one()
    resultado = await _recorrer(sesion, semilla, lunes)

    assert (resultado.tiendas, resultado.renglones) == (4, 5)
    assert await _vigente(sesion, ids["martes"]) == [1, 2]
    assert await _vigente(sesion, ids["jueves"]) == [3]
    assert await _vigente(sesion, ids["sabado"]) == [5]
    assert await _vigente(sesion, ids["lunes"]) == [6], "el lunes pasa al sábado"
    # Nada se borra: lo de antes queda cerrado el domingo.
    cerrado = (
        await sesion.execute(
            text("SELECT dia_semana, hasta FROM clientes_frecuencia "
                 " WHERE cliente_id = :c AND hasta IS NOT NULL ORDER BY dia_semana"),
            {"c": ids["martes"]},
        )
    ).all()
    assert cerrado == [(2, lunes - timedelta(days=1)), (3, lunes - timedelta(days=1))]

    # Al teléfono: el plan nuevo, y UN cambio por tienda.
    plan_visita = (
        await sesion.execute(text("SELECT plan_visita FROM clientes WHERE id = :c"),
                             {"c": ids["martes"]})
    ).scalar_one()
    assert [d["dia"] for d in plan_visita] == [1, 2]
    cambios = (
        await sesion.execute(
            text("SELECT entidad_id, count(*) FROM change_log "
                 " WHERE cursor > :d AND entidad = 'cliente' GROUP BY entidad_id"),
            {"d": desde_cursor},
        )
    ).all()
    assert {(c, n) for c, n in cambios} == {(i, 1) for i in ids.values()}

    # Queda en la auditoría.
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM auditoria WHERE accion = 'cambiar_dias'")
        )
    ).scalar_one() == 1


async def test_no_se_recorre_dos_veces_ni_desde_hoy(sesion, semilla):
    ids = await _tiendas(sesion, semilla)
    lunes = _proximo_lunes()

    hoy = await _planear(sesion, date.today())
    assert not hoy.se_puede and "tiene que empezar después de hoy" in hoy.problemas[0]

    await _recorrer(sesion, semilla, lunes)
    otra_vez = await _planear(sesion, lunes)
    assert not otra_vez.se_puede
    assert "¿ya se recorrió? No se recorre dos veces" in otra_vez.problemas[0]
    with pytest.raises(ValueError):
        await _recorrer(sesion, semilla, lunes)
    await sesion.rollback()
    assert await _vigente(sesion, ids["martes"]) == [1, 2]


async def test_lo_trabajado_se_sigue_midiendo_con_el_plan_de_antes(sesion, semilla):
    """La venta del martes pasado era de su día; la del lunes que viene, con el
    plan nuevo, también. Y Efectividad cuenta lo mismo que antes de recorrer."""
    from app.api.admin.efectividad import SQL_PLAN

    ids = await _tiendas(sesion, semilla)
    lunes = _proximo_lunes()
    martes_pasado = lunes - timedelta(days=6)
    lunes_pasado = lunes - timedelta(days=7)

    async def le_toca(tienda, dia):
        return (
            await sesion.execute(text("SELECT fn_le_toca_visita(:c, :d)"),
                                 {"c": ids[tienda], "d": dia})
        ).scalar_one()

    async def planeadas():
        desde = date.today() - timedelta(days=20)
        return sorted(
            (
                await sesion.execute(
                    text(SQL_PLAN + " SELECT fecha, cliente_id FROM cruzado"),
                    {"desde": desde, "hasta": date.today() - timedelta(days=1), "ruta": ""},
                )
            ).all()
        )

    antes = await planeadas()
    assert antes, "hay historia que medir"
    await _recorrer(sesion, semilla, lunes)

    assert await planeadas() == antes, "Efectividad cambió lo ya trabajado"
    # El martes pasado le tocaba a la del martes, y el lunes pasado no.
    assert await le_toca("martes", martes_pasado) is True
    assert await le_toca("martes", lunes_pasado) is False
    # Desde el lunes, con el plan nuevo: lunes sí, miércoles ya no.
    assert await le_toca("martes", lunes) is True
    assert await le_toca("martes", lunes + timedelta(days=2)) is False
    assert await le_toca("lunes", lunes) is False
    assert await le_toca("lunes", lunes + timedelta(days=5)) is True


async def test_el_mapa_de_la_ruta_y_el_panel(cliente, sesion, semilla):
    """El sábado antes del lunes, el mapa de gerencia enseña el sábado de siempre y
    el lunes ya recorrido; el panel edita el plan nuevo."""
    from app.api.admin.plan_visita import plan_de
    from tests.test_vendedores_api import _cab

    ids = await _tiendas(sesion, semilla)
    lunes = _proximo_lunes()
    await _recorrer(sesion, semilla, lunes)
    sabado = lunes - timedelta(days=2)
    cab = await _cab(cliente)

    async def codigos(dia):
        r = await cliente.get(
            f"/v1/vendedores/{semilla['vendedor']}/ruta?dia={dia}&fecha={sabado}", headers=cab
        )
        assert r.status_code == 200, r.text
        return [t["codigo"] for t in r.json()["tiendas"]], r.json()["dias"]

    hoy_sabado, dias = await codigos(6)
    assert hoy_sabado == ["RD-sabado"], "el sábado de hoy es el de siempre"
    el_lunes, _ = await codigos(1)
    assert el_lunes == ["RD-martes"], "el lunes ya es lo del martes"
    # Cada día, el que vale la próxima vez que toque: hoy sábado el de siempre; el
    # viernes que viene, ya lo del sábado; el jueves, lo del viernes (nada).
    assert {d["etiqueta"]: d["tiendas"] for d in dias} == {
        "Lun": 1, "Mar": 1, "Mié": 1, "Jue": 0, "Vie": 1, "Sáb": 1,
    }

    plan = await plan_de(sesion, [ids["lunes"], ids["martes"]])
    assert plan[ids["lunes"]] == {(6, None)}
    assert plan[ids["martes"]] == {(1, None), (2, None)}


# ---------------------------------------------------------------------------
# Desde el panel: mover, intercambiar o recorrer, en una ruta o en todas
# ---------------------------------------------------------------------------


async def _entrar_al_panel(cliente):
    from tests.test_plan_visita import _entrar

    await _entrar(cliente)


def _manana():
    from app.infra.recorrer_dias import hoy_en_mazatlan

    return hoy_en_mazatlan() + timedelta(days=1)


async def _ver(cliente, semilla, **campos):
    from tests.conftest import csrf_del_panel

    pantalla = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    datos = {"csrf": csrf_del_panel(cliente, pantalla), "alcance": "ruta",
             "desde": _manana().isoformat(), **campos}
    return await cliente.post(f"/panel/plan-visita/{semilla['ruta']}/dias", data=datos,
                              follow_redirects=False)


def _formulario_de_confirmar(respuesta) -> dict:
    import re

    forma = respuesta.text.split('id="forma_confirmar_dias"', 1)[1].split("</form>", 1)[0]
    return dict(re.findall(r'name="([a-z]+)" value="([^"]*)"', forma))


async def test_el_panel_mueve_todas_las_de_un_dia_a_otro(cliente, sesion, semilla):
    """«Una opción de mover todas las de un día para otro día.» Primero se ve cómo
    queda; al confirmar se cambia, y el doble clic no lo aplica dos veces."""
    ids = await _tiendas(sesion, semilla)
    await _entrar_al_panel(cliente)

    pantalla = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    assert 'id="cambiar_dias"' in pantalla.text and "Mover todas las tiendas" in pantalla.text

    # El martes al miércoles: la del martes y miércoles se queda con uno.
    previa = await _ver(cliente, semilla, operacion="mover", de="2", a="3")
    assert previa.status_code == 200, previa.text[-2000:]
    assert 'id="tabla_cambio_dias"' in previa.text
    assert "1 tienda(s) ya tenían los dos días: se quedan con uno solo." in previa.text
    assert "el orden de visita se mezcla" in previa.text
    assert await _vigente(sesion, ids["martes"]) == [2, 3], "ver no cambia nada"

    datos = _formulario_de_confirmar(previa)
    r = await cliente.post(f"/panel/plan-visita/{semilla['ruta']}/dias/confirmar",
                           data=datos, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert "guardado=" in r.headers["location"]
    await sesion.rollback()
    assert await _vigente(sesion, ids["martes"]) == [3]
    assert await _vigente(sesion, ids["lunes"]) == [1], "los otros días no se tocan"

    # El mismo formulario otra vez —el doble clic—: el plan ya no es el que se vio.
    otra = await cliente.post(f"/panel/plan-visita/{semilla['ruta']}/dias/confirmar",
                              data=datos, follow_redirects=False)
    from urllib.parse import unquote_plus

    assert "El plan cambió desde que lo viste" in unquote_plus(otra.headers["location"])
    await sesion.rollback()
    assert await _vigente(sesion, ids["martes"]) == [3]


async def test_el_panel_intercambia_en_todas_las_rutas_y_recorre(cliente, sesion, semilla):
    ids = await _tiendas(sesion, semilla)
    otra_ruta = uuid.uuid4()
    otra_tienda = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R09', 'Ruta 9')"),
        {"r": otra_ruta},
    )
    await sesion.execute(
        text("INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
             "                      actualizado_en) "
             "VALUES (:c, 'RD-otra', 'OTRA', :r, now(), now())"),
        {"c": otra_tienda, "r": otra_ruta},
    )
    await sesion.execute(
        text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana) VALUES (:c, 1)"),
        {"c": otra_tienda},
    )
    await sesion.commit()
    await _entrar_al_panel(cliente)

    # Solo esta ruta: la otra no se toca.
    previa = await _ver(cliente, semilla, operacion="intercambiar", de="1", a="4")
    assert "R09" not in previa.text
    await cliente.post(f"/panel/plan-visita/{semilla['ruta']}/dias/confirmar",
                       data=_formulario_de_confirmar(previa))
    await sesion.rollback()
    assert await _vigente(sesion, ids["lunes"]) == [4]
    assert await _vigente(sesion, ids["jueves"]) == [1]
    assert await _vigente(sesion, otra_tienda) == [1]

    # Todas las rutas, un día para atrás, para el mismo día: lo que todavía no
    # empezaba se reacomoda sin dejar historia.
    previa = await _ver(cliente, semilla, operacion="atras", alcance="todas")
    assert "R09" in previa.text
    await cliente.post(f"/panel/plan-visita/{semilla['ruta']}/dias/confirmar",
                       data=_formulario_de_confirmar(previa))
    await sesion.rollback()
    assert await _vigente(sesion, ids["lunes"]) == [3]
    assert await _vigente(sesion, ids["jueves"]) == [6]
    assert await _vigente(sesion, otra_tienda) == [6]
    historia = (
        await sesion.execute(
            text("SELECT count(*) FROM clientes_frecuencia WHERE cliente_id = :c"),
            {"c": ids["lunes"]},
        )
    ).scalar_one()
    assert historia == 2, "el lunes de antes, cerrado, y el miércoles de ahora"


async def test_el_panel_dice_lo_que_no_se_puede(cliente, sesion, semilla):
    await _tiendas(sesion, semilla)
    await _entrar_al_panel(cliente)

    r = await _ver(cliente, semilla, operacion="mover", de="2", a="2")
    assert r.status_code == 303 and "error=" in r.headers["location"]

    hoy = await _ver(cliente, semilla, operacion="atras",
                     desde=(_manana() - timedelta(days=1)).isoformat())
    assert "No se puede: el cambio empezaría" in hoy.text
    assert 'id="forma_confirmar_dias"' not in hoy.text


async def test_el_comando_dice_como_queda_y_pide_la_palabra(
    motor, sesion, semilla, monkeypatch, capsys
):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import recorrer_dias

    ids = await _tiendas(sesion, semilla)
    lunes = _proximo_lunes()

    assert await recorrer_dias(["--resumen", "--desde", lunes.isoformat()]) == 0
    salida = capsys.readouterr().out
    assert f"desde el lunes {lunes:%d/%m/%Y}" in salida
    assert "lo del martes pasa al lunes" in salida.lower()
    assert "R04" in salida and "hoy:       Lun 1 · Mar 1 · Mié 1 · Jue 1 · Sáb 1" in salida
    assert "recorrido: Lun 1 · Mar 1 · Mié 1 · Vie 1 · Sáb 1" in salida

    monkeypatch.setattr("builtins.input", lambda _="": "no")
    assert await recorrer_dias(["--desde", lunes.isoformat()]) == 1
    await sesion.rollback()
    assert await _vigente(sesion, ids["martes"]) == [2, 3]

    monkeypatch.setattr("builtins.input", lambda _="": "recorrer")
    assert await recorrer_dias(["--desde", lunes.isoformat()]) == 0
    assert "Listo. 4 tiendas recorridas" in capsys.readouterr().out
    await sesion.rollback()
    assert await _vigente(sesion, ids["martes"]) == [1, 2]

    # Otra vez: no recorre dos veces.
    assert await recorrer_dias(["--desde", lunes.isoformat(), "--confirmado"]) == 1
    assert "No se recorre dos veces" in capsys.readouterr().out
