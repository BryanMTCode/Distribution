"""Los artículos agrupados por familia (ADR 0002 §89).

1. **Los 25 del Excel** entran en sus cinco familias, en el orden de la hoja, y
   así se ven en Productos y en el Inventario —con el subtotal de cada una, como
   los totales de la hoja—.
2. **Las familias se administran** en Productos: agregar, renombrar y borrar.
   Borrar una familia no borra sus artículos: quedan «Sin familia».
3. **La app del gerente** recibe la familia de cada existencia.
"""

from __future__ import annotations

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
    assert "Ganador Minino 9,000 $318,101.00" in plano
    assert "Sopa Maruchan 343 $54,880.00" in plano
    assert "$417,237.00" in plano


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
