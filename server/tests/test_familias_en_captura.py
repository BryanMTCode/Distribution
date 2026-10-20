"""Las familias de las tablas donde se captura se pliegan y se despliegan (ADR 0002 §147).

«En todas las listas del dashboard quiero que puedan ser desplegables o no, por
ejemplo en ajustes de inventario.»

1. **Cada familia es un `<details>`** abierto, con su tabla: en Ajustar
   inventario, Mover mercancía y agregar a una entrada o una salida.
2. **«Plegar todas» y «Desplegar todas»** vienen como botones escondidos que el
   ayudante de la página enseña: pliegan sin recargar y lo escrito no se pierde.
   Con una sola familia no hacen falta.
3. **Lo escrito en una familia se guarda** como antes: los renglones siguen
   dentro del formulario.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_ajustar_inventario import _contar
from tests.test_mover_mercancia import articulos  # noqa: F401
from tests.test_operaciones import _existencia
from tests.test_panel_ajuste_camion import _entrar
from tests.test_panel_entradas import _abrir as _abrir_entrada
from tests.test_panel_entradas import _id_de as _id_de_entrada
from tests.test_panel_salidas import _abrir as _abrir_salida
from tests.test_panel_salidas import _id_de as _id_de_salida

pytestmark = pytest.mark.asyncio

BOTON_PLEGAR = '<button type="button" class="boton-chico" data-familias="plegar" hidden>'


async def _dos_familias(sesion, refresco) -> None:
    """La Coca en «Refrescos»; la sopa se queda sin familia."""
    familia = (
        await sesion.execute(
            text("INSERT INTO categorias (codigo, nombre, orden) VALUES ('REF', 'Refrescos', 1) "
                 "RETURNING id")
        )
    ).scalar_one()
    await sesion.execute(
        text("UPDATE productos SET categoria_id = :f WHERE id = :p"), {"f": familia, "p": refresco}
    )
    await sesion.commit()


def _revisar(html: str, *renglones: str) -> None:
    assert html.count('<details class="familia"') == 2
    assert '<details class="familia" id="familia_1" open>' in html
    assert "Refrescos <span" in html and "Sin familia <span" in html
    assert BOTON_PLEGAR in html
    # El ayudante que los enseña viene en la página.
    assert 'querySelectorAll("[data-familias]")' in html
    for renglon in renglones:
        assert renglon in html.split('<details class="familia"', 1)[1], renglon


async def test_las_cuatro_tablas_van_por_familia_plegable(cliente, sesion, semilla, articulos):  # noqa: F811
    await _dos_familias(sesion, articulos["refresco"])
    await _entrar(cliente)

    ajustar = await cliente.get(
        "/panel/inventario/ajustar", params={"almacen": str(semilla["camion"])}
    )
    _revisar(ajustar.text, 'id="contar_SOPA-70G"', 'id="contar_COCA600"')

    mover = await cliente.get(
        "/panel/inventario/mover",
        params={"camion": str(semilla["camion"]), "bodega": str(semilla["bodega"]),
                "sentido": "al_camion"},
    )
    _revisar(mover.text, 'id="mover_SOPA-70G"', 'id="mover_COCA600"')

    entrada = _id_de_entrada(await _abrir_entrada(cliente, semilla))
    _revisar((await cliente.get(f"/panel/entradas/{entrada}")).text,
             'id="recibir_SOPA-70G"', 'id="recibir_COCA600"')

    salida = _id_de_salida(await _abrir_salida(cliente, semilla))
    _revisar((await cliente.get(f"/panel/salidas/{salida}")).text,
             'id="salida_SOPA-70G"', 'id="salida_COCA600"')


async def test_con_una_sola_familia_no_hay_plegar_todas(cliente, sesion, semilla, articulos):  # noqa: F811
    await _entrar(cliente)
    html = (
        await cliente.get("/panel/inventario/ajustar", params={"almacen": str(semilla["camion"])})
    ).text
    assert html.count('<details class="familia"') == 1
    assert BOTON_PLEGAR not in html


async def test_lo_escrito_en_cualquier_familia_se_guarda(cliente, sesion, semilla, articulos):  # noqa: F811
    # Los renglones siguen dentro del formulario: el navegador manda lo de una
    # familia plegada igual que lo de una abierta.
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    await _dos_familias(sesion, refresco)
    await _entrar(cliente)
    r = await _contar(cliente, semilla["camion"],
                      **{f"cajas_{sopa}": "1", f"piezas_{refresco}": "6"})
    assert "Se ajustaron 2 artículo(s)" in r.text
    await sesion.rollback()
    assert await _existencia(sesion, semilla["camion"], sopa) == 24
    assert await _existencia(sesion, semilla["camion"], refresco) == 6
