"""Clientes: alta, alcance por ruta y condiciones.

La operación es de contado (ADR 0002 §81): la API ya no abre crédito, ni da
cartera, ni evalúa ventas a crédito.
"""

from __future__ import annotations

import uuid

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


async def test_alta_en_campo_nace_prospecto(cliente, semilla, sesion):
    """Un cliente que el vendedor da de alta en la calle espera a que la oficina
    le dé código y lista de precios."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    c = await _crear(
        cliente, cab,
        id=str(uuid.uuid4()),
        lat="19.4326000", lng="-99.1332000", ubicacion_origen="gps",
    )
    assert c["origen_alta"] == "campo"
    assert c["estatus"] == "prospecto"
    assert "permite_credito" not in c


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


async def test_el_alta_no_acepta_credito(cliente, semilla, sesion):
    """Ya no hay crédito, y mandarlo debe fallar ruidosamente, no ignorarse en
    silencio: un teléfono que lo mande está sin actualizar."""
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

async def test_la_oficina_fija_ruta_y_estatus(cliente, semilla):
    cab = await _cab_admin(cliente)
    c = await _crear(cliente, cab)
    r = await cliente.patch(
        f"/v1/clientes/{c['id']}/condiciones",
        json={"ruta_id": str(semilla["ruta"]), "estatus": "inactivo"},
        headers=cab,
    )
    assert r.status_code == 200
    assert r.json()["estatus"] == "inactivo"
    assert r.json()["ruta_id"] == str(semilla["ruta"])


async def test_el_credito_ya_no_se_puede_abrir(cliente, semilla):
    """Todo es de contado: un campo de crédito en las condiciones es un 422."""
    cab = await _cab_admin(cliente)
    c = await _crear(cliente, cab)
    for cuerpo in ({"permite_credito": True}, {"limite_credito": "5000.00"},
                   {"bloqueado": True}):
        r = await cliente.patch(
            f"/v1/clientes/{c['id']}/condiciones", json=cuerpo, headers=cab
        )
        assert r.status_code == 422, cuerpo
    for ruta in ("cartera", "credito/evaluar"):
        r = await cliente.get(f"/v1/clientes/{c['id']}/{ruta}", headers=cab)
        assert r.status_code in (404, 405), ruta


async def test_el_vendedor_no_puede_tocar_las_condiciones(cliente, semilla, sesion):
    cab_admin = await _cab_admin(cliente)
    c = await _crear(cliente, cab_admin, ruta_id=str(semilla["ruta"]))
    cab_vend = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.patch(
        f"/v1/clientes/{c['id']}/condiciones", json={"estatus": "activo"},
        headers=cab_vend,
    )
    assert r.status_code == 403
