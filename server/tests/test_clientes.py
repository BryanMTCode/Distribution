"""Clientes, alcance por ruta y bloqueo por límite de crédito."""

from __future__ import annotations

import uuid
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _cab_admin(cliente) -> dict:
    r = await cliente.post(
        "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _cab_vendedor(cliente, sesion, semilla) -> dict:
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now()) ON CONFLICT DO NOTHING"),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo_id),
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _crear(cliente, cab, **kw) -> dict:
    cuerpo = {"nombre_comercial": "Abarrotes Doña Mary", **kw}
    r = await cliente.post("/v1/clientes", json=cuerpo, headers=cab)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------

async def test_alta_desde_la_oficina(cliente, semilla):
    c = await _crear(cliente, await _cab_admin(cliente), ruta_id=str(semilla["ruta"]))
    assert c["origen_alta"] == "oficina"
    assert c["estatus"] == "activo"


async def test_alta_en_campo_nace_sin_credito(cliente, semilla, sesion):
    """Un cliente que el vendedor da de alta en la calle no puede salir con
    línea de crédito: esa decisión es de la oficina."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    c = await _crear(
        cliente, cab,
        id=str(uuid.uuid4()),
        lat="19.4326000", lng="-99.1332000", ubicacion_origen="gps",
    )
    assert c["origen_alta"] == "campo"
    assert c["estatus"] == "prospecto"
    assert c["permite_credito"] is False
    assert D(c["limite_credito"]) == D("0.00")


async def test_el_alta_en_campo_es_idempotente(cliente, semilla, sesion):
    """La red puede entregar dos veces; recibirlo dos veces no crea dos
    clientes."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    id_local = str(uuid.uuid4())
    cuerpo = {"id": id_local, "nombre_comercial": "Abarrotes Doña Mary"}

    primera = await cliente.post("/v1/clientes", json=cuerpo, headers=cab)
    segunda = await cliente.post("/v1/clientes", json=cuerpo, headers=cab)
    assert primera.json()["id"] == segunda.json()["id"] == id_local

    total = (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one()
    assert total == 1


async def test_el_vendedor_no_puede_colarse_una_linea_de_credito(cliente, semilla, sesion):
    """Las condiciones comerciales son propiedad del servidor. Mandarlas en el
    alta debe fallar ruidosamente, no ignorarse en silencio."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.post(
        "/v1/clientes",
        json={"nombre_comercial": "X", "limite_credito": "50000.00", "permite_credito": True},
        headers=cab,
    )
    assert r.status_code == 422


async def test_ubicacion_ajustada_a_mano_queda_registrada(cliente, semilla, sesion):
    """Que el vendedor corrija las coordenadas es normal; que no quede
    registrado, no."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    c = await _crear(cliente, cab, id=str(uuid.uuid4()),
                     lat="19.4326000", lng="-99.1332000", ubicacion_origen="manual")
    assert c["ubicacion_origen"] == "manual"


async def test_coordenadas_fuera_de_rango_se_rechazan(cliente, semilla):
    r = await cliente.post(
        "/v1/clientes",
        json={"nombre_comercial": "X", "lat": "91.0", "lng": "0.0"},
        headers=await _cab_admin(cliente),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Alcance
# ---------------------------------------------------------------------------

async def test_el_vendedor_solo_ve_los_clientes_de_su_ruta(cliente, semilla, sesion):
    cab_admin = await _cab_admin(cliente)
    await _crear(cliente, cab_admin, ruta_id=str(semilla["ruta"]))

    otra_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas(id, codigo, nombre) VALUES (:r,'R09','Ruta 9')"),
        {"r": otra_ruta},
    )
    await sesion.commit()
    await _crear(cliente, cab_admin, nombre_comercial="De otra ruta", ruta_id=str(otra_ruta))

    cab_vend = await _cab_vendedor(cliente, sesion, semilla)
    vistos = (await cliente.get("/v1/clientes", headers=cab_vend)).json()
    assert vistos["total"] == 1
    assert vistos["clientes"][0]["nombre_comercial"] == "Abarrotes Doña Mary"

    # El admin sí ve los dos.
    assert (await cliente.get("/v1/clientes", headers=cab_admin)).json()["total"] == 2


async def test_pedir_una_ruta_ajena_es_403(cliente, semilla, sesion):
    otra_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas(id, codigo, nombre) VALUES (:r,'R09','Ruta 9')"), {"r": otra_ruta}
    )
    await sesion.commit()
    cab = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.get(f"/v1/clientes?ruta_id={otra_ruta}", headers=cab)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Condiciones comerciales
# ---------------------------------------------------------------------------

async def test_la_oficina_abre_la_linea_de_credito(cliente, semilla):
    cab = await _cab_admin(cliente)
    c = await _crear(cliente, cab, ruta_id=str(semilla["ruta"]))
    r = await cliente.patch(
        f"/v1/clientes/{c['id']}/condiciones",
        json={"permite_credito": True, "limite_credito": "5000.00", "dias_credito": 15},
        headers=cab,
    )
    assert r.status_code == 200
    assert r.json()["permite_credito"] is True
    assert D(r.json()["limite_credito"]) == D("5000.00")


async def test_bloquear_sin_motivo_se_rechaza(cliente, semilla):
    """Un bloqueo sin motivo es una decisión que nadie puede revisar después."""
    cab = await _cab_admin(cliente)
    c = await _crear(cliente, cab)
    r = await cliente.patch(
        f"/v1/clientes/{c['id']}/condiciones", json={"bloqueado": True}, headers=cab
    )
    assert r.status_code == 422


async def test_el_vendedor_no_puede_tocar_las_condiciones(cliente, semilla, sesion):
    cab_admin = await _cab_admin(cliente)
    c = await _crear(cliente, cab_admin, ruta_id=str(semilla["ruta"]))
    cab_vend = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.patch(
        f"/v1/clientes/{c['id']}/condiciones", json={"limite_credito": "99999.00"},
        headers=cab_vend,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Crédito
# ---------------------------------------------------------------------------

async def _cliente_con_credito(cliente, cab, semilla, limite="5000.00") -> str:
    c = await _crear(cliente, cab, ruta_id=str(semilla["ruta"]))
    await cliente.patch(
        f"/v1/clientes/{c['id']}/condiciones",
        json={"permite_credito": True, "limite_credito": limite, "dias_credito": 15},
        headers=cab,
    )
    return c["id"]


async def test_cartera_de_un_cliente_sin_deuda(cliente, semilla):
    cab = await _cab_admin(cliente)
    cid = await _cliente_con_credito(cliente, cab, semilla)
    r = await cliente.get(f"/v1/clientes/{cid}/cartera", headers=cab)
    assert r.status_code == 200
    cartera = r.json()
    assert D(cartera["saldo"]) == D("0.00")
    assert D(cartera["disponible"]) == D("5000.00")
    assert cartera["credito_agotado"] is False


async def test_evaluar_credito_dentro_del_limite(cliente, semilla):
    cab = await _cab_admin(cliente)
    cid = await _cliente_con_credito(cliente, cab, semilla)
    r = await cliente.post(
        f"/v1/clientes/{cid}/credito/evaluar",
        json={"total": "1200.00", "a_credito": True},
        headers=cab,
    )
    assert r.json()["permitida"] is True


async def test_el_bloqueo_por_limite_cuenta_la_cola_del_dispositivo(cliente, semilla):
    """El agujero del offline, extremo a extremo: dos ventas de 3000 con
    límite de 5000 solo se detectan si la evaluación cuenta lo que el teléfono
    trae sin sincronizar."""
    cab = await _cab_admin(cliente)
    cid = await _cliente_con_credito(cliente, cab, semilla)

    primera = await cliente.post(
        f"/v1/clientes/{cid}/credito/evaluar",
        json={"total": "3000.00", "a_credito": True}, headers=cab,
    )
    assert primera.json()["permitida"] is True

    segunda = await cliente.post(
        f"/v1/clientes/{cid}/credito/evaluar",
        json={"total": "3000.00", "a_credito": True, "cargos_pendientes": "3000.00"},
        headers=cab,
    )
    cuerpo = segunda.json()
    assert cuerpo["permitida"] is False
    assert cuerpo["motivo"] == "excede_limite"
    assert D(cuerpo["excedente"]) == D("1000.00")


async def test_el_mismo_cliente_bloqueado_sigue_comprando_de_contado(cliente, semilla):
    """La regla del negocio: se bloquea el crédito, no la venta."""
    cab = await _cab_admin(cliente)
    cid = await _cliente_con_credito(cliente, cab, semilla, limite="100.00")
    r = await cliente.post(
        f"/v1/clientes/{cid}/credito/evaluar",
        json={"total": "3000.00", "a_credito": False}, headers=cab,
    )
    assert r.json()["permitida"] is True
    assert r.json()["motivo"] == "contado_siempre_permitido"


async def test_un_abono_pendiente_libera_linea(cliente, semilla):
    cab = await _cab_admin(cliente)
    cid = await _cliente_con_credito(cliente, cab, semilla)
    r = await cliente.post(
        f"/v1/clientes/{cid}/credito/evaluar",
        json={"total": "3000.00", "a_credito": True,
              "cargos_pendientes": "3000.00", "abonos_pendientes": "2000.00"},
        headers=cab,
    )
    assert r.json()["permitida"] is True


async def test_cliente_sin_linea_no_compra_a_credito(cliente, semilla):
    cab = await _cab_admin(cliente)
    c = await _crear(cliente, cab, ruta_id=str(semilla["ruta"]))
    r = await cliente.post(
        f"/v1/clientes/{c['id']}/credito/evaluar",
        json={"total": "10.00", "a_credito": True}, headers=cab,
    )
    assert r.json()["permitida"] is False
    assert r.json()["motivo"] == "sin_linea_de_credito"


async def test_la_cartera_refleja_una_venta_a_credito_real(cliente, semilla, sesion):
    """Recorre la vista con datos de verdad: una factura abierta debe bajar el
    disponible y, al agotarlo, marcar credito_agotado."""
    cab = await _cab_admin(cliente)
    cid = await _cliente_con_credito(cliente, cab, semilla, limite="1000.00")

    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    venta_id = uuid.uuid4()
    await sesion.execute(
        text("""
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :a, 'credito',
                    800.00, 800.00, now(), CURRENT_DATE)
        """),
        {"v": venta_id, "d": dispositivo_id, "c": uuid.UUID(cid),
         "u": semilla["vendedor"], "a": semilla["camion"]},
    )
    await sesion.execute(
        text("""
            INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                            importe_pagado, fecha_emision, fecha_vencimiento,
                                            estado, actualizado_en)
            VALUES (:v, :c, 800.00, 0, CURRENT_DATE, CURRENT_DATE + 15, 'abierta', now())
        """),
        {"v": venta_id, "c": uuid.UUID(cid)},
    )
    await sesion.commit()

    cartera = (await cliente.get(f"/v1/clientes/{cid}/cartera", headers=cab)).json()
    assert D(cartera["saldo"]) == D("800.00")
    assert D(cartera["disponible"]) == D("200.00")
    assert cartera["facturas_abiertas"] == 1

    # Con 200 disponibles, una venta de 500 a crédito no pasa.
    r = await cliente.post(
        f"/v1/clientes/{cid}/credito/evaluar",
        json={"total": "500.00", "a_credito": True}, headers=cab,
    )
    assert r.json()["permitida"] is False
    assert D(r.json()["excedente"]) == D("300.00")
