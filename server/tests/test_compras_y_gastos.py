"""Dinero → Compras y gastos (ADR 0002 §193).

«Que en el dashboard se pueda llevar el control de los gastos y compras a
proveedores, pero con una vista buena: no quiero tener que meterme a cada
compra.»

Dos compras de hoy a Abarrotes del Centro: 10 cajas de Coca ($2,960), de la que
se pagaron $1,000 con la tarjeta, y 5 cajas de Pepsi ($600), sin pagar. Y $120 de
gasolina.
"""

from __future__ import annotations

import re

import pytest

from tests.test_compras import _comprar, _csrf, _entrar, _id_proveedor, _producto, _proveedor
from tests.test_panel_objetivos import _crear_usuario
from tests.test_tarjeta import _plano, _post

pytestmark = pytest.mark.asyncio


def _cifra(html: str, ident: str) -> str:
    m = re.search(rf'id="{ident}">([^<]*)<', html)
    assert m, f"no está {ident}"
    return m.group(1)


async def _compras_y_gasolina(cliente, sesion, semilla):
    await _producto(sesion)
    await _producto(sesion, sku="PEPSI600", factor=12)
    await _entrar(cliente)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)
    await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="5000")
    coca, _ = await _comprar(cliente, semilla, sesion, proveedor=proveedor, referencia="F-1")
    await _comprar(cliente, semilla, sesion, sku="PEPSI600", bultos="5", costo="120.00",
                   proveedor=proveedor, referencia="F-2")
    r = await cliente.post(
        f"/panel/compras/cuenta/{coca}/pago",
        data={"csrf": _csrf(cliente), "importe": "1000", "forma_pago": "tarjeta",
              "referencia": "", "fecha": "", "nota": ""},
        follow_redirects=False,
    )
    assert "error" not in r.headers["location"]
    await _post(cliente, "/panel/dinero/tarjeta/movimiento", tipo="salida", importe="120",
                categoria="gasolina", concepto="Gasolina del camión 1", fecha="")
    return coca


async def test_las_compras_y_los_gastos_sin_abrir_ninguna_compra(cliente, sesion, semilla):
    coca = await _compras_y_gasolina(cliente, sesion, semilla)
    html = (await cliente.get("/panel/dinero/compras-y-gastos?periodo=hoy")).text

    assert _cifra(html, "total_compras") == "$3,560.00"
    assert _cifra(html, "total_pagado") == "$1,000.00"
    assert _cifra(html, "total_debe") == "$2,560.00"
    assert _cifra(html, "total_gastos") == "$120.00"
    assert _cifra(html, "total_salio") == "$3,680.00"

    plano = _plano(html)
    # Lo que trajo cada compra, a la vista, con lo pagado y cómo.
    assert "Producto COCA600 · 240 pzas 240 $2,960.00 $1,000.00 tarjeta de la empresa" in plano
    assert "Producto PEPSI600 · 60 pzas 60 $600.00 $0.00 $600.00" in plano
    assert f'href="/panel/compras/cuenta/{coca}"' in html
    # Por proveedor, por artículo y los gastos.
    assert "Abarrotes del Centro 2 $3,560.00 $1,000.00 $2,560.00" in plano
    assert "Producto COCA600 COCA600 240 $2,960.00 $12.33" in plano
    assert "Gasolina $120.00 Total $120.00" in plano

    # Fuera del periodo no hay nada.
    html = (await cliente.get("/panel/dinero/compras-y-gastos?periodo=ayer")).text
    assert _cifra(html, "total_compras") == "$0.00"
    assert "No hubo compras a proveedores" in html

    # Y desde Compras se llega.
    assert 'id="ir_a_compras_y_gastos"' in (await cliente.get("/panel/compras")).text


async def test_solo_el_administrador(cliente, sesion, semilla):
    await _crear_usuario(sesion, semilla, "GER09", "gerente")
    await _entrar(cliente, "GER09")
    assert (await cliente.get("/panel/dinero/compras-y-gastos")).status_code == 403
    assert 'id="ir_a_compras_y_gastos"' not in (await cliente.get("/panel/compras")).text
