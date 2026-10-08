"""La compra que el gerente recibe desde el teléfono, aunque no haya señal (§83).

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
1. **Reintentar no suma dos veces.** El teléfono la manda cada vez que recupera
   la señal; el id que le dio al capturarla es la llave.
2. **El costo es opcional, renglón por renglón.** Con costo: mueve el promedio
   y entra a la cuenta por pagar. Sin costo: solo suma inventario.
3. **Entra a la bodega principal**, como una entrada de compra con su folio.
4. **Lo que se cortó a la mitad se rehace**, y lo mal capturado no deja nada
   sumado.
5. **Quién**: `inventario.ajustar`; el vendedor no.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR
from tests.test_almacen_api import _existencia
from tests.test_compras import _id_proveedor, _proveedor
from tests.test_oficina_clientes_api import _cab
from tests.test_panel_entradas import _entrar, _producto

pytestmark = pytest.mark.asyncio


def _compra(compra_id, proveedor=None, *renglones, fecha: str = "") -> dict:
    return {
        "id": str(compra_id),
        "proveedor_id": str(proveedor) if proveedor else None,
        "proveedor": "" if proveedor else "Central de abastos",
        "referencia": "R-77",
        "nota": "La recibió el gerente en la calle",
        "fecha": fecha,
        "renglones": list(renglones),
    }


def _renglon(producto, cajas: str, costo: str = "") -> dict:
    return {"producto_id": str(producto), "unidad": "CAJA", "cantidad": cajas, "costo": costo}


async def _costo(sesion, producto):
    return (
        await sesion.execute(
            text("SELECT costo_promedio FROM producto_costos WHERE producto_id = :p"),
            {"p": producto},
        )
    ).scalar_one_or_none()


async def _cuentas(sesion) -> list:
    return (
        await sesion.execute(text("SELECT importe_original FROM cuentas_por_pagar"))
    ).scalars().all()


async def test_con_costo_mueve_el_promedio_y_deja_la_deuda(cliente, sesion, semilla):
    await _entrar(cliente)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)
    coca = await _producto(sesion)
    r = await cliente.post(
        "/v1/almacen/compras",
        json=_compra(uuid.uuid4(), proveedor, _renglon(coca, "10", "296.00")),
        headers=await _cab(cliente),
    )
    assert r.status_code == 200, r.text
    entrada = r.json()
    assert entrada["estado"] == "confirmada"
    assert entrada["motivo"] == "compra"
    assert entrada["bodega"] == "Bodega"
    assert entrada["cuenta"]["saldo"] == "2960.00"
    assert await _existencia(sesion, semilla["bodega"], coca) == 240
    assert await _costo(sesion, coca) is not None


async def test_sin_costo_solo_suma_inventario(cliente, sesion, semilla):
    await _entrar(cliente)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)
    coca = await _producto(sesion)
    r = await cliente.post(
        "/v1/almacen/compras",
        json=_compra(uuid.uuid4(), proveedor, _renglon(coca, "5")),
        headers=await _cab(cliente),
    )
    assert r.status_code == 200, r.text
    assert r.json()["cuenta"] is None
    assert "sin costo" in r.json()["mensaje"]
    assert await _existencia(sesion, semilla["bodega"], coca) == 120
    assert await _costo(sesion, coca) is None
    assert await _cuentas(sesion) == []


async def test_mezclada_la_deuda_es_solo_lo_que_trae_costo(cliente, sesion, semilla):
    await _entrar(cliente)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)
    coca = await _producto(sesion)
    pepsi = await _producto(sesion, sku="PEPSI600")
    r = await cliente.post(
        "/v1/almacen/compras",
        json=_compra(uuid.uuid4(), proveedor, _renglon(coca, "2", "300"), _renglon(pepsi, "3")),
        headers=await _cab(cliente),
    )
    assert r.status_code == 200, r.text
    assert await _cuentas(sesion) == [Decimal("600.00")]
    assert await _existencia(sesion, semilla["bodega"], pepsi) == 72
    assert await _costo(sesion, pepsi) is None


async def test_reintentar_no_suma_dos_veces(cliente, sesion, semilla):
    await _entrar(cliente)
    coca = await _producto(sesion)
    cab = await _cab(cliente)
    compra = _compra(uuid.uuid4(), None, _renglon(coca, "1"))
    primera = await cliente.post("/v1/almacen/compras", json=compra, headers=cab)
    segunda = await cliente.post("/v1/almacen/compras", json=compra, headers=cab)
    assert primera.status_code == segunda.status_code == 200
    assert primera.json()["folio"] == segunda.json()["folio"]
    assert "ya se había recibido" in segunda.json()["mensaje"]
    assert await _existencia(sesion, semilla["bodega"], coca) == 24


async def test_lo_mal_capturado_no_suma_y_al_corregirlo_entra(cliente, sesion, semilla):
    await _entrar(cliente)
    coca = await _producto(sesion)
    cab = await _cab(cliente)
    compra_id = uuid.uuid4()
    r = await cliente.post(
        "/v1/almacen/compras",
        json=_compra(compra_id, None, _renglon(coca, "2"), _renglon(coca, "1.5")),
        headers=cab,
    )
    assert r.status_code == 409
    assert "media caja" in r.json()["detail"].lower() or "completos" in r.json()["detail"]
    assert await _existencia(sesion, semilla["bodega"], coca) == 0

    # El mismo id, ya corregido: el borrador que quedó se rehace, no se duplica.
    r = await cliente.post(
        "/v1/almacen/compras", json=_compra(compra_id, None, _renglon(coca, "2")), headers=cab
    )
    assert r.status_code == 200, r.text
    assert await _existencia(sesion, semilla["bodega"], coca) == 48
    cuantas = (
        await sesion.execute(text("SELECT count(*) FROM entradas WHERE id = :e"), {"e": compra_id})
    ).scalar_one()
    assert cuantas == 1


async def test_la_fecha_es_la_del_dia_en_que_llego(cliente, sesion, semilla):
    await _entrar(cliente)
    coca = await _producto(sesion)
    r = await cliente.post(
        "/v1/almacen/compras",
        json=_compra(uuid.uuid4(), None, _renglon(coca, "1"), fecha="2026-10-01"),
        headers=await _cab(cliente),
    )
    assert r.status_code == 200, r.text
    assert r.json()["fecha_operativa"] == "2026-10-01"


async def test_el_vendedor_no_recibe_compras(cliente, sesion, semilla):
    coca = await _producto(sesion)
    equipo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado) "
             "VALUES (:d, :u, 'POCO', 'activo')"),
        {"d": equipo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    entrada = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(equipo),
    })
    r = await cliente.post(
        "/v1/almacen/compras",
        json=_compra(uuid.uuid4(), None, _renglon(coca, "1")),
        headers={"Authorization": f"Bearer {entrada.json()['access_token']}"},
    )
    assert r.status_code == 403
