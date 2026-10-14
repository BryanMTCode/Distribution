"""Los clientes del panel, por ruta y por día; y el código de las nuevas (ADR 0002 §122).

«Que las tiendas nuevas sigan en 1934; además, en el dashboard que las tiendas
estén agrupadas por el día y la ruta a la que pertenecen, no todas en general, y
que se pueda filtrar por eso.»

1. **«Todos» abre con la tabla de rutas por día**, cada cifra con su enlace, y no
   con la lista de todas.
2. **Con una ruta, sus tiendas por día** en el orden del recorrido; la que toca
   dos días sale en los dos; la que no tiene día, aparte.
3. **Con un día, ese día de cada ruta.**
4. **La tienda nueva lleva el número que sigue**, y si ya está ocupado, el
   siguiente.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import solo_texto
from tests.test_panel_objetivos import _entrar

pytestmark = pytest.mark.asyncio


async def _tienda(sesion, ruta, codigo, nombre, dias, orden=None) -> uuid.UUID:
    cliente = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, colonia, ruta_id, secuencia, "
            "  estatus, creado_en, actualizado_en) "
            "VALUES (:id, :c, :n, 'PLAYA SUR', :r, :o, 'activo', now(), now())"
        ),
        {"id": cliente, "c": codigo, "n": nombre, "r": ruta, "o": orden},
    )
    for d in dias:
        await sesion.execute(
            text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana) VALUES (:c, :d)"),
            {"c": cliente, "d": d},
        )
    return cliente


@pytest.fixture
async def dos_rutas(sesion, semilla) -> dict:
    """R04 (la de Juan): tres el lunes —una también el jueves— y una sin día.
    R09: una el martes. Y una tienda sin ruta."""
    r09 = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R09', 'Ruta 9')"), {"r": r09}
    )
    r04 = semilla["ruta"]
    await _tienda(sesion, r04, "100", "SUPER ATENAS", [1], orden=2)
    await _tienda(sesion, r04, "101", "OLAS NEGRAS", [1], orden=1)
    await _tienda(sesion, r04, "102", "EL FARO", [1, 4], orden=3)
    await _tienda(sesion, r04, "103", "SIN DIA", [])
    await _tienda(sesion, r09, "200", "LA PASADITA", [2], orden=1)
    await _tienda(sesion, None, "300", "SUELTA", [])
    await sesion.commit()
    return {"r04": r04, "r09": r09}


async def test_todos_abre_con_la_tabla_de_rutas_por_dia(cliente, semilla, dos_rutas):
    await _entrar(cliente)
    # Sin nada por confirmar, abre en «Todos».
    r = await cliente.get("/panel/clientes")
    assert 'id="estado_todos"' in r.text and 'aria-current="true">Todos (6)' in r.text
    assert 'id="rutas_por_dia"' in r.text and 'id="elige_ruta_o_dia"' in r.text
    # No todas en general: la tabla, no la lista.
    assert "SUPER ATENAS" not in solo_texto(r)

    fila_r04 = r.text.split('id="ruta_R04"')[1].split("</tr>")[0]
    assert f'href="/panel/clientes?filtro=todos&ruta={dos_rutas["r04"]}&dia=1"' in fila_r04
    assert 'id="celda_R04_1">3<' in fila_r04
    assert 'id="celda_R04_4">1<' in fila_r04
    assert "<strong>4</strong>" in fila_r04  # cuatro tiendas, aunque el lunes y el jueves sumen 4
    assert 'id="celda_R09_2">1<' in r.text
    assert 'id="ruta_sin"' in r.text  # la tienda sin ruta, en su renglón


async def test_una_ruta_trae_sus_tiendas_por_dia_en_orden(cliente, semilla, dos_rutas):
    await _entrar(cliente)
    r = await cliente.get(f"/panel/clientes?filtro=todos&ruta={dos_rutas['r04']}")
    assert 'id="grupo_ruta_R04"' in r.text and 'id="grupo_ruta_R09"' not in r.text
    lunes = r.text.split('id="grupo_R04_1"')[1].split("</details>")[0]
    # En el orden del recorrido.
    assert lunes.index("OLAS NEGRAS") < lunes.index("SUPER ATENAS") < lunes.index("EL FARO")
    assert "también Jue" in lunes
    jueves = r.text.split('id="grupo_R04_4"')[1].split("</details>")[0]
    assert "EL FARO" in jueves and "SUPER ATENAS" not in jueves
    sin_dia = r.text.split('id="grupo_R04_sin"')[1].split("</details>")[0]
    assert "SIN DIA" in sin_dia
    assert 'href="/panel/plan-visita?ruta=' in r.text
    # El filtro de ruta queda elegido.
    assert f'<option value="{dos_rutas["r04"]}" selected' in r.text


async def test_un_dia_trae_ese_dia_de_cada_ruta(cliente, semilla, dos_rutas):
    await _entrar(cliente)
    r = await cliente.get("/panel/clientes?filtro=todos&dia=1")
    assert 'id="grupo_R04_1"' in r.text and 'id="grupo_R04_4"' not in r.text
    assert "LA PASADITA" not in solo_texto(r)  # la R09 no trabaja el lunes
    r = await cliente.get("/panel/clientes?filtro=todos&dia=sin")
    plano = solo_texto(r)
    assert "SIN DIA" in plano and "SUELTA" in plano and "OLAS NEGRAS" not in plano
    # Y la búsqueda se agrupa igual.
    r = await cliente.get("/panel/clientes?filtro=todos&q=pasadita")
    assert 'id="grupo_R09_2"' in r.text


async def test_la_tienda_nueva_lleva_el_numero_que_sigue(sesion, semilla):
    from app.infra.codigo_de_cliente import siguiente_codigo

    await sesion.execute(text("SELECT setval('seq_codigo_cliente', 5000, false)"))
    # El 5000 ya lo tiene alguien: se salta al 5001 en vez de chocar.
    await _tienda(sesion, semilla["ruta"], "5000", "ESCRITO A MANO", [])
    assert await siguiente_codigo(sesion) == "5001"
    assert await siguiente_codigo(sesion) == "5002"
    await sesion.rollback()


# ---------------------------------------------------------------------------
# Lo mismo en la app de la oficina: /v1/oficina/clientes?agrupar=1
# ---------------------------------------------------------------------------


async def test_la_app_recibe_las_rutas_por_dia_y_las_tiendas_de_una(cliente, semilla, dos_rutas):
    from tests.test_vendedores_api import _cab

    cab = await _cab(cliente)
    # Sin ruta ni día: solo el resumen de rutas, no todas en general.
    cuerpo = (await cliente.get("/v1/oficina/clientes?agrupar=1", headers=cab)).json()
    assert cuerpo["listado"] is False and cuerpo["clientes"] == []
    r04 = next(r for r in cuerpo["rutas"] if r["codigo"] == "R04")
    assert r04["clave"] == str(dos_rutas["r04"]) and r04["total"] == 4
    assert [(d["dia"], d["tiendas"]) for d in r04["por_dia"]] == [(1, 3), (4, 1), (None, 1)]
    assert cuerpo["rutas"][-1]["clave"] == "sin"

    # Una ruta: sus tiendas en el orden del recorrido, con sus días.
    cuerpo = (await cliente.get(
        f"/v1/oficina/clientes?agrupar=1&ruta={dos_rutas['r04']}", headers=cab
    )).json()
    assert cuerpo["listado"] is True and cuerpo["ruta"] == str(dos_rutas["r04"])
    assert [(c["codigo"], c["dias"], c["orden"]) for c in cuerpo["clientes"]] == [
        ("101", [1], 1), ("100", [1], 2), ("102", [1, 4], 3), ("103", [], None),
    ]
    # Un día de todas las rutas.
    cuerpo = (await cliente.get("/v1/oficina/clientes?agrupar=1&dia=2", headers=cab)).json()
    assert [c["codigo"] for c in cuerpo["clientes"]] == ["200"]

    # La app anterior, sin `agrupar`, sigue recibiendo la lista de siempre.
    cuerpo = (await cliente.get("/v1/oficina/clientes", headers=cab)).json()
    assert len(cuerpo["clientes"]) == 6 and cuerpo["rutas"] == []
