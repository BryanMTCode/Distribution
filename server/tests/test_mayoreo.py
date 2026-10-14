"""Ventas de mayoreo de la oficina (ADR 0002 §105).

«El administrador puede hacer ventas a mayoreo al precio que él decida, y los
clientes él los maneja.»

1. **Los clientes de mayoreo** los da de alta la oficina; no tienen ruta.
2. **La venta** sale de la bodega, al precio que se escribió (el de la lista se
   guarda al lado), con folio MY-, y deja su asiento en el libro mayor.
3. **No se vende lo que no hay**, y no se le vende a un cliente de ruta.
4. **Cancelar** devuelve la mercancía y pide motivo.
5. **Solo con el permiso**: el admin sí, la gerencia no (a menos que se le dé).
6. **El panel** hace lo mismo sin JavaScript, y un precio en blanco toma la lista.
7. **El mayoreo va aparte** de la suma de los objetivos: no es de ningún
   vendedor (§115).
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel, solo_texto
from tests.test_tablero_api import _cab

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def bodega_surtida(sesion, semilla) -> dict:
    """Atún con caja de 24: 480 piezas en la bodega, $12 la pieza y $270 la caja."""
    producto = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
             "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA', 0)"),
        {"p": producto},
    )
    await sesion.execute(
        text("INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
             "VALUES (:p, 'PZA', 1, true), (:p, 'CAJA', 24, false)"),
        {"p": producto},
    )
    await sesion.execute(
        text("INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version) "
             "VALUES (:l, :p, 'PZA', 12, 1), (:l, :p, 'CAJA', 270, 1)"),
        {"l": semilla["lista_precios"], "p": producto},
    )
    await sesion.execute(
        text("INSERT INTO existencias (almacen_id, producto_id, cantidad) VALUES (:a, :p, 480)"),
        {"a": semilla["bodega"], "p": producto},
    )
    await sesion.commit()
    return {"producto": producto}


async def _existencia(sesion, semilla, producto) -> Decimal:
    return (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"),
            {"a": semilla["bodega"], "p": producto},
        )
    ).scalar_one()


async def _cliente(cliente, cab, nombre="Abarrotes El Mayorista") -> dict:
    r = await cliente.post(
        "/v1/mayoreo/clientes", json={"nombre": nombre, "telefono": "6691234567"}, headers=cab
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_la_venta_sale_de_la_bodega_al_precio_que_se_decidio(
    cliente, sesion, semilla, bodega_surtida
):
    cab = await _cab(cliente)
    nuevo = await _cliente(cliente, cab)
    assert nuevo["codigo"].isdigit() and int(nuevo["codigo"]) >= 1934
    ruta = (
        await sesion.execute(text("SELECT ruta_id, mayoreo FROM clientes WHERE id = :c"),
                             {"c": nuevo["id"]})
    ).one()
    assert (ruta.ruta_id, ruta.mayoreo) == (None, True)

    opciones = (await cliente.get("/v1/mayoreo/opciones", headers=cab)).json()
    assert [c["nombre_comercial"] for c in opciones["clientes"]] == ["Abarrotes El Mayorista"]
    surtido = (
        await cliente.get(f"/v1/mayoreo/surtido/{semilla['bodega']}", headers=cab)
    ).json()
    assert [(p["unidad"], p["precio_lista"]) for p in surtido[0]["presentaciones"]] == [
        ("PZA", "12.00"), ("CAJA", "270.00")
    ]

    r = await cliente.post(
        "/v1/mayoreo",
        json={
            "cliente_id": nuevo["id"], "almacen_id": str(semilla["bodega"]),
            "renglones": [
                {"producto_id": str(bodega_surtida["producto"]), "unidad": "CAJA",
                 "bultos": "10", "precio": "250"},
                {"producto_id": str(bodega_surtida["producto"]), "unidad": "PZA",
                 "bultos": "6", "precio": "11.5"},
            ],
            "forma_pago": "transferencia", "referencia_pago": "SPEI 123",
        },
        headers=cab,
    )
    assert r.status_code == 201, r.text
    venta = r.json()
    # La serie no se reinicia entre pruebas: se revisa la forma, no el número.
    assert re.fullmatch(r"MY-\d{6}", venta["folio"])
    assert venta["total"] == "2569.00"  # 10 × 250 + 6 × 11.50
    partidas = [
        (p["bultos"], p["cantidad"], p["precio"], p["precio_lista"]) for p in venta["partidas"]
    ]
    assert partidas == [
        ("10.000", "240.000", "250.00", "270.00"), ("6.000", "6.000", "11.50", "12.00")
    ]
    assert "*Total: $2,569.00* (transferencia)" in venta["ticket"]
    assert await _existencia(sesion, semilla, bodega_surtida["producto"]) == Decimal("234.000")
    asiento = (
        await sesion.execute(
            text("SELECT tipo, cantidad FROM movimientos_inventario "
                 " WHERE documento_tipo = 'venta_mayoreo'")
        )
    ).one()
    assert (asiento.tipo, asiento.cantidad) == ("venta", Decimal("246.000"))

    del_dia = (await cliente.get("/v1/mayoreo?periodo=hoy", headers=cab)).json()
    assert del_dia["total"] == "2569.00" and del_dia["ventas"][0]["piezas"] == "246.000"


async def test_no_se_vende_lo_que_no_hay_ni_a_un_cliente_de_ruta(
    cliente, sesion, semilla, bodega_surtida
):
    cab = await _cab(cliente)
    nuevo = await _cliente(cliente, cab)
    pedido = {
        "cliente_id": nuevo["id"], "almacen_id": str(semilla["bodega"]),
        "renglones": [{"producto_id": str(bodega_surtida["producto"]), "unidad": "CAJA",
                       "bultos": "21", "precio": "250"}],
    }
    r = await cliente.post("/v1/mayoreo", json=pedido, headers=cab)
    assert r.status_code == 422
    assert "la bodega tiene 480 piezas y la venta pide 504" in r.json()["detail"]
    assert await _existencia(sesion, semilla, bodega_surtida["producto"]) == Decimal("480.000")

    de_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
             "actualizado_en) VALUES (:c, 'C9', 'Tienda de ruta', :r, now(), now())"),
        {"c": de_ruta, "r": semilla["ruta"]},
    )
    await sesion.commit()
    pedido["cliente_id"] = str(de_ruta)
    pedido["renglones"][0]["bultos"] = "1"
    r = await cliente.post("/v1/mayoreo", json=pedido, headers=cab)
    assert r.status_code == 422 and "no es cliente de mayoreo" in r.json()["detail"]
    assert (await sesion.execute(text("SELECT count(*) FROM ventas_mayoreo"))).scalar_one() == 0


async def test_cancelar_devuelve_la_mercancia(cliente, sesion, semilla, bodega_surtida):
    cab = await _cab(cliente)
    nuevo = await _cliente(cliente, cab)
    venta = (
        await cliente.post(
            "/v1/mayoreo",
            json={"cliente_id": nuevo["id"], "almacen_id": str(semilla["bodega"]),
                  "renglones": [{"producto_id": str(bodega_surtida["producto"]), "unidad": "CAJA",
                                 "bultos": "2", "precio": "260"}]},
            headers=cab,
        )
    ).json()
    r = await cliente.post(f"/v1/mayoreo/{venta['id']}/cancelar", json={"motivo": "x"},
                           headers=cab)
    assert r.status_code == 422
    r = await cliente.post(f"/v1/mayoreo/{venta['id']}/cancelar",
                           json={"motivo": "Se equivocó de cliente"}, headers=cab)
    assert r.status_code == 200 and r.json()["estado"] == "cancelada"
    assert await _existencia(sesion, semilla, bodega_surtida["producto"]) == Decimal("480.000")
    r = await cliente.post(f"/v1/mayoreo/{venta['id']}/cancelar",
                           json={"motivo": "Otra vez"}, headers=cab)
    assert r.status_code == 422 and "ya estaba cancelada" in r.json()["detail"]
    del_dia = (await cliente.get("/v1/mayoreo?periodo=hoy", headers=cab)).json()
    assert del_dia["total"] == "0.00"


async def test_solo_con_el_permiso(cliente, sesion, semilla):
    from app.core.seguridad import hashear_password
    from tests.conftest import PASSWORD_VENDEDOR

    await sesion.execute(
        text("INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, rol_codigo, "
             "creado_en, actualizado_en) VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', "
             "now(), now())"),
        {"id": uuid.uuid4(), "s": semilla["sucursal"], "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()
    cab = await _cab(cliente, "GER01")
    assert (await cliente.get("/v1/mayoreo/opciones", headers=cab)).status_code == 403


async def test_el_panel_vende_y_el_precio_en_blanco_toma_la_lista(
    cliente, sesion, semilla, bodega_surtida
):
    from tests.test_panel_objetivos import _entrar

    await _entrar(cliente)
    r = await cliente.get("/panel/mayoreo")
    assert r.status_code == 200 and "Ventas de mayoreo" in r.text
    r = await cliente.post(
        "/panel/mayoreo/clientes",
        data={"csrf": csrf_del_panel(cliente, r), "nombre": "Dulcería Central",
              "volver": "/panel/mayoreo/nueva"},
        follow_redirects=True,
    )
    assert "dado de alta" in solo_texto(r)
    cliente_id = (await sesion.execute(text("SELECT id FROM clientes WHERE mayoreo"))).scalar_one()
    producto = bodega_surtida["producto"]
    r = await cliente.post(
        "/panel/mayoreo/nueva",
        data={
            "csrf": csrf_del_panel(cliente, r), "cliente_id": str(cliente_id),
            "almacen_id": str(semilla["bodega"]), "forma_pago": "efectivo",
            "articulo_0": f"{producto}|CAJA|270.0000", "cantidad_0": "3", "precio_0": "",
            "articulo_1": f"{producto}|PZA|12.0000", "cantidad_1": "4", "precio_1": "10",
        },
        follow_redirects=True,
    )
    plano = solo_texto(r)
    assert re.search(r"Venta MY-\d{6} guardada\.", plano)
    assert "Total: $850.00" in plano  # 3 × 270 + 4 × 10
    assert await _existencia(sesion, semilla, producto) == Decimal("404.000")

    # Un renglón a medias se dice y no se guarda nada.
    r = await cliente.post(
        "/panel/mayoreo/nueva",
        data={"csrf": csrf_del_panel(cliente, r), "cliente_id": str(cliente_id),
              "almacen_id": str(semilla["bodega"]), "articulo_0": f"{producto}|CAJA|270"},
        follow_redirects=True,
    )
    assert "le falta el artículo o la cantidad" in solo_texto(r)

    venta = (await sesion.execute(text("SELECT id FROM ventas_mayoreo"))).scalar_one()
    r = await cliente.post(
        f"/panel/mayoreo/{venta}/cancelar",
        data={"csrf": csrf_del_panel(cliente, r), "motivo": "Devolvió todo"},
        follow_redirects=True,
    )
    assert "volvió a la bodega" in solo_texto(r)
    assert await _existencia(sesion, semilla, producto) == Decimal("480.000")


async def test_el_mayoreo_va_aparte_de_la_suma_de_objetivos(
    cliente, sesion, semilla, bodega_surtida
):
    """Toda la empresa es la suma de los vendedores (§115). El mayoreo no es de
    ningún vendedor: no entra en la suma, y se dice aparte."""
    from tests.test_panel_objetivos import _diario, _entrar

    hoy = date.today()
    await _diario(sesion, semilla["ruta"], "1000", desde=hoy)
    cab = await _cab(cliente)
    nuevo = await _cliente(cliente, cab)
    await cliente.post(
        "/v1/mayoreo",
        json={"cliente_id": nuevo["id"], "almacen_id": str(semilla["bodega"]),
              "renglones": [{"producto_id": str(bodega_surtida["producto"]), "unidad": "CAJA",
                             "bultos": "4", "precio": "250"}]},
        headers=cab,
    )
    await _entrar(cliente)
    plano = solo_texto(await cliente.get("/panel/objetivos?periodo=hoy"))
    assert "Toda la empresa la suma de todos $1,000.00 $0.00" in plano
    assert "Aparte, $1,000.00 de mayoreo en el periodo" in plano
