"""Lo que vale el inventario: existencia × precio de venta, por artículo.

1. **El precio** es el de la lista por omisión, por pieza: el de la pieza si lo
   tiene; si solo tiene el de la caja, la caja entre su factor.
2. **Por artículo y en total**, en el Inventario del panel y en las Existencias
   de la app. Lo negativo resta en su renglón y no suma al total.
3. **Sin precio no vale cero**: se dice «sin precio» y no entra al total, que
   avisa cuántos le faltan.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import solo_texto
from tests.test_oficina_clientes_api import _cab
from tests.test_panel_inventario import _entrar
from tests.test_panel_liquidacion import sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def inventario(sesion, semilla) -> dict:
    """El atún con caja de 24: 760 piezas en bodega; el camión, en −10."""
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(
        text("UPDATE existencias SET cantidad = -10 WHERE almacen_id = :c AND producto_id = :p"),
        {"c": semilla["camion"], "p": dia["producto"]},
    )
    await sesion.commit()
    return dia


async def _precio(sesion, semilla, producto, unidad: str, precio: str) -> None:
    await sesion.execute(
        text("INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio) "
             "VALUES (:l, :p, :u, :precio) "
             "ON CONFLICT (lista_id, producto_id, unidad_codigo) "
             "DO UPDATE SET precio = excluded.precio"),
        {"l": semilla["lista_precios"], "p": producto, "u": unidad, "precio": precio},
    )
    await sesion.commit()


async def _sin_precio(sesion, semilla) -> None:
    """Un artículo con 5 piezas en bodega y ningún precio."""
    producto = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO productos (id, sku, nombre, unidad_base) "
             "VALUES (:p, 'SIN-PRECIO', 'Galletas sin precio', 'PZA')"),
        {"p": producto},
    )
    await sesion.execute(
        text("INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
             "VALUES (:p, 'PZA', 1, true)"),
        {"p": producto},
    )
    await sesion.execute(
        text("INSERT INTO existencias (almacen_id, producto_id, cantidad) VALUES (:a, :p, 5)"),
        {"a": semilla["bodega"], "p": producto},
    )
    await sesion.commit()


async def test_el_panel_dice_cuanto_vale_cada_articulo_y_el_almacen(
    cliente, sesion, semilla, inventario
):
    # Solo precio por caja: la pieza vale la caja entre 24.
    await _precio(sesion, semilla, inventario["producto"], "CAJA", "240.00")
    await _sin_precio(sesion, semilla)
    await _entrar(cliente)
    r = await cliente.get("/panel/inventario")
    plano = solo_texto(r)

    # 760 en bodega × $10 la pieza.
    assert "$10.00" in plano
    assert "$7,600.00" in plano
    assert "sin precio" in plano
    assert "1 sin precio no suman" in plano
    total = r.text.split('id="total_valor"')[1].split("</td>")[0]
    assert "$7,600.00" in total

    # El camión: −10 piezas restan en su renglón y no en el total.
    r = await cliente.get(f"/panel/inventario?almacen={semilla['camion']}&filtro=todos")
    renglon = r.text.split('id="valor_ATUN-140"')[1].split("</td>")[0]
    assert "$-100.00" in renglon
    total = r.text.split('id="total_valor"')[1].split("</td>")[0]
    assert "$0.00" in total


async def test_el_precio_de_la_pieza_manda_sobre_el_de_la_caja(
    cliente, sesion, semilla, inventario
):
    await _precio(sesion, semilla, inventario["producto"], "CAJA", "240.00")
    await _precio(sesion, semilla, inventario["producto"], "PZA", "12.50")
    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/inventario"))
    assert "$12.50" in plano
    assert "$9,500.00" in plano  # 760 × 12.50


async def test_la_app_trae_precio_y_valor(cliente, sesion, semilla, inventario):
    await _precio(sesion, semilla, inventario["producto"], "CAJA", "240.00")
    await _sin_precio(sesion, semilla)
    cab = await _cab(cliente)

    bodega = next(
        a for a in (await cliente.get("/v1/almacen", headers=cab)).json()
        if a["tipo"] == "bodega"
    )
    assert bodega["valor"] == "7600.00"

    cuerpo = (
        await cliente.get(f"/v1/almacen/{semilla['bodega']}/existencias", headers=cab)
    ).json()
    por_sku = {e["sku"]: e for e in cuerpo["existencias"]}
    assert por_sku["ATUN-140"]["precio"] == "10.00"
    assert por_sku["ATUN-140"]["valor"] == "7600.00"
    assert por_sku["SIN-PRECIO"]["precio"] is None
    assert por_sku["SIN-PRECIO"]["valor"] is None
