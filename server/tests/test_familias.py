"""Los artículos agrupados por familia (ADR 0002 §89).

1. **Los 25 del Excel** entran en sus cinco familias, en el orden de la hoja, y
   así se ven en Productos y en el Inventario —con el subtotal de cada una, como
   los totales de la hoja—.
2. **Las familias se administran** en Productos: agregar, renombrar y borrar.
   Borrar una familia no borra sus artículos: quedan «Sin familia».
3. **La app del gerente** recibe la familia de cada existencia.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel, solo_texto
from tests.test_oficina_clientes_api import _cab
from tests.test_panel_catalogo import _entrar

pytestmark = pytest.mark.asyncio


async def _en_blanco(sesion):
    # Importado aquí y no arriba: ver `_bb` en test_base_en_blanco.py.
    from app.infra import base_en_blanco as bb

    await bb.poner_en_blanco(sesion, bb.leer_articulos(), quien=await bb.quien_registra(sesion))


async def test_los_del_excel_se_ven_por_familia_y_en_su_orden(cliente, sesion, semilla):
    await _en_blanco(sesion)
    await _entrar(cliente)
    r = await cliente.get("/panel/productos")
    plano = solo_texto(r)
    orden = [plano.index(f) for f in ("Ganador Minino · 15 artículo(s)",
                                      "Botanas Javi · 3 artículo(s)",
                                      "Sopa Maruchan · 3 artículo(s)",
                                      "Mega Alimentos · 2 artículo(s)",
                                      "Dulcería Valdez · 2 artículo(s)")]
    assert orden == sorted(orden)
    # Dentro de la familia, como en la hoja: por clave.
    assert plano.index("S-100") < plano.index("S-114") < plano.index("E-100")

    # Una sola familia.
    familia = (
        await sesion.execute(text("SELECT id FROM categorias WHERE nombre = 'Sopa Maruchan'"))
    ).scalar_one()
    plano = solo_texto(await cliente.get(f"/panel/productos?familia={familia}"))
    assert "Sopa Maruchan habanero" in plano and "Costal Minino" not in plano

    # El inventario, con el subtotal de cada familia: el de la hoja.
    plano = solo_texto(await cliente.get("/panel/inventario"))
    assert "Ganador Minino · 15 artículo(s) 9,000 piezas · $318,101.00" in plano
    assert "Sopa Maruchan · 3 artículo(s) 343 piezas · $54,880.00" in plano
    assert "$417,237.00" in plano


async def test_cada_familia_se_pliega_y_se_pueden_plegar_todas(cliente, sesion, semilla):
    """«Que las agrupaciones se puedan desplegar o no» (ADR 0002 §92): cada
    familia es un `<details>` —se abre y se cierra sin JavaScript— y «Plegar
    todas» es un enlace que vuelve con todas cerradas."""
    await _en_blanco(sesion)
    await _entrar(cliente)
    # En el inventario, Dulcería Valdez no se ve: no tiene existencia.
    for ruta, familias in (("/panel/productos", 5), ("/panel/inventario", 4)):
        html = (await cliente.get(ruta)).text
        assert html.count('<details class="familia"') == familias, ruta
        assert html.count('<details class="familia" id="familia_1" open>') == 1, ruta
        enlace = re.search(r'href="([^"]+)" id="plegar_todas"', html).group(1)
        respuesta = await cliente.get(enlace.replace("&amp;", "&"))
        plegado = respuesta.text
        assert "familias=plegadas" in enlace
        assert plegado.count('<details class="familia"') == familias, ruta
        assert " open>" not in plegado.split('id="familia_1"')[1].split(">")[0] + ">", ruta
        assert 'id="desplegar_todas"' in plegado, ruta
        # Plegadas, los subtotales siguen a la vista.
        if ruta == "/panel/inventario":
            assert "$318,101.00" in solo_texto(respuesta)
    # Al buscar, todo abierto: lo que se busca no se esconde.
    html = (await cliente.get("/panel/productos?q=Minino&familias=plegadas")).text
    assert '<details class="familia" id="familia_1" open>' in html


async def test_agregar_renombrar_y_borrar_una_familia(cliente, sesion, semilla):
    await _en_blanco(sesion)
    await _entrar(cliente)
    r = await cliente.get("/panel/productos")
    r = await cliente.post(
        "/panel/productos/familias",
        data={"csrf": csrf_del_panel(cliente, r), "nombre": "  Refrescos  "},
        follow_redirects=True,
    )
    assert "Familia «Refrescos» creada" in solo_texto(r)
    orden = (
        await sesion.execute(text("SELECT orden FROM categorias WHERE nombre = 'Refrescos'"))
    ).scalar_one()
    assert orden == 6  # al final, después de las de la hoja

    r = await cliente.post(
        "/panel/productos/familias",
        data={"csrf": csrf_del_panel(cliente, r), "nombre": "refrescos"},
        follow_redirects=True,
    )
    assert "Ya hay una familia «refrescos»" in solo_texto(r)

    javi = (
        await sesion.execute(text("SELECT id FROM categorias WHERE nombre = 'Botanas Javi'"))
    ).scalar_one()
    r = await cliente.post(
        f"/panel/productos/familias/{javi}",
        data={"csrf": csrf_del_panel(cliente, r), "nombre": "Botanas"},
        follow_redirects=True,
    )
    assert "ahora se llama «Botanas»" in solo_texto(r)

    r = await cliente.post(
        f"/panel/productos/familias/{javi}/eliminar",
        data={"csrf": csrf_del_panel(cliente, r)},
        follow_redirects=True,
    )
    assert "Sus 3 artículo(s) quedaron «Sin familia»" in solo_texto(r)
    assert "Sin familia · 3 artículo(s)" in solo_texto(r)
    sueltos = (
        await sesion.execute(text("SELECT count(*) FROM productos WHERE categoria_id IS NULL"))
    ).scalar_one()
    assert sueltos == 3


async def test_la_app_recibe_la_familia(cliente, sesion, semilla):
    await _en_blanco(sesion)
    cab = await _cab(cliente)
    bodega = next(
        a for a in (await cliente.get("/v1/almacen", headers=cab)).json()
        if a["codigo"] == "BODEGA_PRINCIPAL"
    )
    cuerpo = (await cliente.get(f"/v1/almacen/{bodega['id']}/existencias", headers=cab)).json()
    primeras = [(e["sku"], e["familia"]) for e in cuerpo["existencias"][:2]]
    assert primeras == [("S-100", "Ganador Minino"), ("S-101", "Ganador Minino")]


async def test_la_app_lista_los_articulos_por_familia(cliente, sesion, semilla):
    """Lo que abre «artículos activos» en Empresa de la app (ADR 0002 §97)."""
    await _en_blanco(sesion)
    articulos = (
        await cliente.get("/v1/almacen/articulos", headers=await _cab(cliente))
    ).json()
    assert len(articulos) == 25
    primero = articulos[0]
    assert (primero["sku"], primero["familia"], primero["precio"]) == (
        "S-100", "Ganador Minino", "10.00"
    )
    assert primero["en_bodegas"] == "1056.000" and primero["en_camiones"] == "0.000"
    familias = list(dict.fromkeys(a["familia"] for a in articulos))
    assert familias[0] == "Ganador Minino" and len(familias) == 5
