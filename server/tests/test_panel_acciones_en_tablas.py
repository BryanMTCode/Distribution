"""Editar, Eliminar y Ajustar desde las tablas que ya existían (octubre 2026).

La dirección pidió «control total» sin pantallas nuevas: los botones van en las
tablas de ventas, clientes, productos e inventario, y llevan al formulario del
detalle —con su motivo, su nota o su casilla— en vez de actuar de un clic. Una
cancelación de un clic en una tabla de doscientos renglones es la forma de
cancelar la venta de al lado.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _entrar(cliente) -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


@pytest.fixture
async def sopa(sesion, semilla) -> uuid.UUID:
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'SOPA-70G', 'Sopa de fideo 70 g', 'PZA', 0)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:c, :p, 30), (:b, :p, 100)"
        ),
        {"c": semilla["camion"], "b": semilla["bodega"], "p": producto},
    )
    await sesion.commit()
    return producto


async def test_productos_trae_editar_y_eliminar(cliente, semilla, sopa):
    await _entrar(cliente)
    html = (await cliente.get("/panel/productos")).text
    assert f'href="/panel/productos/{sopa}#datos">Editar' in html
    assert f'href="/panel/productos/{sopa}#eliminar">Eliminar' in html
    # Y las anclas existen en el detalle: un botón que lleva a la página pero no al
    # formulario obliga a buscarlo.
    detalle = (await cliente.get(f"/panel/productos/{sopa}")).text
    assert 'id="datos"' in detalle and 'id="eliminar"' in detalle


@pytest.mark.parametrize("almacen", ["camion", "bodega"])
async def test_inventario_trae_ajustar_en_camion_y_en_bodega(
    cliente, semilla, sopa, almacen
):
    await _entrar(cliente)
    html = (await cliente.get(f"/panel/inventario?almacen={semilla[almacen]}")).text
    assert f'href="/panel/inventario/{semilla[almacen]}/{sopa}#ajustar">Ajustar' in html
    detalle = (await cliente.get(f"/panel/inventario/{semilla[almacen]}/{sopa}")).text
    assert 'id="ajustar"' in detalle


async def test_ventas_trae_editar_y_cancelar(cliente, sesion, semilla, sopa):
    dispositivo, cliente_id, venta = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
            "                      actualizado_en) "
            "VALUES (:c, 'C1', 'La Esquina', :r, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo, subtotal,
                                total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :a, 'contado', 10, 10,
                    now(), CURRENT_DATE)
            """
        ),
        {
            "v": venta, "d": dispositivo, "c": cliente_id,
            "u": semilla["vendedor"], "a": semilla["camion"],
        },
    )
    await sesion.commit()

    await _entrar(cliente)
    html = (await cliente.get("/panel/ventas?solo_revision=0")).text
    assert f'href="/panel/ventas/{venta}#corregir">Editar' in html
    assert f'href="/panel/ventas/{venta}#cancelar">Cancelar' in html
