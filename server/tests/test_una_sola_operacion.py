"""Una captura de varios artículos es UNA operación (ADR 0002 §146).

«Cuando se ajuste el inventario en el dashboard, quiero que sea una sola
operación: en Operaciones se genera una por cada artículo del ajuste.» Y: «en
general las operaciones son en conjunto: si hago una entrada, no quiero que haya
una por cada artículo».

1. **El conteo** de varios artículos sale en Operaciones como un solo renglón,
   con un solo folio; cada artículo lleva ese número con el de su renglón
   (AC-000001-2…), y al teléfono le llega uno por artículo, como siempre.
2. **Anularlo** deshace todos los artículos; los ajustes de la anulación también
   van juntos.
3. **Mover mercancía** de varios artículos, igual: una operación, y anularla
   regresa todo.
4. **En los movimientos del vendedor**, el conteo de su camión es un renglón.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_ajustar_inventario import _contar
from tests.test_mover_mercancia import _deltas_al_telefono, _mover, articulos  # noqa: F401
from tests.test_operaciones import _anular, _existencia
from tests.test_panel_ajuste_camion import _entrar

pytestmark = pytest.mark.asyncio


async def _ajustes(sesion) -> list:
    return (
        await sesion.execute(
            text("SELECT id, folio, tipo, pareja_id, delta FROM ajustes_camion "
                 " ORDER BY creado_en, folio")
        )
    ).all()


async def test_el_conteo_es_una_sola_operacion(cliente, sesion, semilla, articulos):  # noqa: F811
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    await _entrar(cliente)

    r = await _contar(cliente, semilla["camion"],
                      **{f"cajas_{sopa}": "1", f"piezas_{refresco}": "6"})
    assert "en una sola operación (AC-" in r.text
    await sesion.rollback()

    cabeza, otro = await _ajustes(sesion)
    assert cabeza.pareja_id is None and otro.pareja_id == cabeza.id
    assert otro.folio == f"{cabeza.folio}-2"
    assert cabeza.folio in r.text

    lista = await cliente.get("/panel/operaciones", params={"tipo": "ajuste"})
    assert lista.text.count('<tr id="op_') == 1
    assert cabeza.folio in lista.text and f"{cabeza.folio}-2" not in lista.text
    assert "2 artículos, 6 pza de más, 6 de menos" in lista.text

    # Al teléfono le llega uno por artículo, con su folio.
    deltas = await _deltas_al_telefono(sesion, semilla["vendedor"])
    assert sorted(d["folio"] for d in deltas) == sorted([cabeza.folio, otro.folio])


async def test_anularlo_deshace_todos_los_articulos(cliente, sesion, semilla, articulos):  # noqa: F811
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    await _entrar(cliente)
    await _contar(cliente, semilla["camion"],
                  **{f"cajas_{sopa}": "1", f"piezas_{refresco}": "6"})
    await sesion.rollback()
    cabeza = (await _ajustes(sesion))[0]

    revision = await cliente.get(f"/panel/operaciones/ajuste/{cabeza.id}")
    assert "de los 2 artículos" in revision.text
    # El aviso del teléfono, una vez por camión y no una por artículo.
    assert revision.text.count("El teléfono de Juan Pérez lo recibe") == 1

    r = await _anular(cliente, "ajuste", cabeza.id)
    assert r.status_code == 200
    assert "con 2 artículos" in r.text
    await sesion.rollback()
    assert await _existencia(sesion, semilla["camion"], sopa) == 30
    assert await _existencia(sesion, semilla["camion"], refresco) == 0

    # La anulación también es una: su cabeza y el otro con ella.
    anulacion = [a for a in await _ajustes(sesion) if a.tipo == "anulacion"]
    assert len(anulacion) == 2
    primera, segunda = anulacion
    assert primera.pareja_id is None and segunda.pareja_id == primera.id
    assert segunda.folio == f"{primera.folio}-2"
    deltas = await _deltas_al_telefono(sesion, semilla["vendedor"])
    assert sorted(d["delta"] for d in deltas if d["tipo"] == "anulacion") == [
        "-6.000", "6.000"
    ]


async def test_mover_varios_articulos_es_una_operacion(cliente, sesion, semilla, articulos):  # noqa: F811
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    await _entrar(cliente)
    r = await _mover(
        cliente, semilla,
        **{f"cantidad_{sopa}": "2", f"unidad_{sopa}": "CAJA",
           f"cantidad_{refresco}": "12", f"unidad_{refresco}": "PZA"},
    )
    assert "en una sola operación (AB-" in r.text
    await sesion.rollback()
    ajustes = await _ajustes(sesion)
    assert len(ajustes) == 4
    cabeza = ajustes[0]
    assert cabeza.delta < 0 and all(a.pareja_id == cabeza.id for a in ajustes[1:])

    lista = await cliente.get("/panel/operaciones", params={"tipo": "ajuste"})
    assert lista.text.count('<tr id="op_') == 1
    assert "2 artículos, 60 pza" in lista.text

    revision = await cliente.get(f"/panel/operaciones/ajuste/{cabeza.id}")
    assert "regresa de Camión 01 a Bodega" in revision.text
    assert (await _anular(cliente, "ajuste", cabeza.id)).status_code == 200
    await sesion.rollback()
    assert await _existencia(sesion, semilla["bodega"], sopa) == 100
    assert await _existencia(sesion, semilla["bodega"], refresco) == 48
    assert await _existencia(sesion, semilla["camion"], sopa) == 30
    assert await _existencia(sesion, semilla["camion"], refresco) == 0


async def test_en_los_movimientos_del_vendedor_es_un_renglon(cliente, sesion, semilla, articulos):  # noqa: F811
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    await _entrar(cliente)
    await _contar(cliente, semilla["camion"],
                  **{f"cajas_{sopa}": "1", f"piezas_{refresco}": "6"})
    await sesion.rollback()
    cabeza = (await _ajustes(sesion))[0]

    html = (await cliente.get(f"/panel/vendedores/{semilla['vendedor']}?periodo=mes")).text
    assert cabeza.folio in html and f"{cabeza.folio}-2" not in html
    assert "2 artículos, 6 pza de más, 6 de menos" in html
    assert f"/panel/operaciones/ajuste/{cabeza.id}" in html
