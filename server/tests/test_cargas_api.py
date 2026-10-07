"""`/v1/cargas`: cargar el camión desde la app, solo los puestos de arriba.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
1. **Quién.** Admin, supervisor y gerente cargan; el vendedor no, ni con su
   token y `curl`. La app esconde el botón; el servidor lo prohíbe.
2. **Las mismas reglas que el panel.** Bultos enteros convertidos a unidad
   base, la regla del §2.3 que se fuerza solo con motivo, y confirmar dos
   veces no mueve el inventario dos veces.
3. **Lo que importa al final:** la carga confirmada mueve la bodega y el camión
   y publica el delta que el teléfono del vendedor va a recibir.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _cab(cliente, codigo: str = "ADMIN01") -> dict:
    r = await cliente.post(
        "/v1/auth/login", json={"codigo": codigo, "password": PASSWORD_VENDEDOR}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _usuario(sesion, semilla, codigo: str, rol: str) -> None:
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, :c, :n, :h, :r, now(), now())"
        ),
        {
            "u": uuid.uuid4(),
            "s": semilla["sucursal"],
            "c": codigo,
            "n": f"Prueba {rol}",
            "h": hashear_password(PASSWORD_VENDEDOR),
            "r": rol,
        },
    )
    await sesion.commit()


@pytest.fixture
async def catalogo(sesion, semilla) -> dict:
    """Un producto con caja de 24, y 20 cajas en la bodega."""
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA', 0.0000)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true), (:p, 'CAJA', 24, false)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text("INSERT INTO existencias (almacen_id, producto_id, cantidad) VALUES (:a, :p, 480)"),
        {"a": semilla["bodega"], "p": producto},
    )
    await sesion.commit()
    return {"producto": producto}


async def _abrir(cliente, cab, semilla) -> dict:
    r = await cliente.post(
        "/v1/cargas",
        json={"vendedor_id": str(semilla["vendedor"]), "almacen_origen_id": str(semilla["bodega"])},
        headers=cab,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _existencia(sesion, almacen, producto) -> Decimal:
    return (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"),
            {"a": almacen, "p": producto},
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Quién
# ---------------------------------------------------------------------------
async def test_el_vendedor_no_carga_ni_con_su_token(cliente, sesion, semilla):
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}

    for respuesta in (
        await cliente.get("/v1/cargas/opciones", headers=cab),
        await cliente.get("/v1/cargas", headers=cab),
        await cliente.post(
            "/v1/cargas",
            json={"vendedor_id": str(semilla["vendedor"]),
                  "almacen_origen_id": str(semilla["bodega"])},
            headers=cab,
        ),
    ):
        assert respuesta.status_code == 403
        assert "inventario.cargar" in respuesta.json()["detail"]


async def test_sin_token_no_hay_cargas(cliente, semilla):
    assert (await cliente.get("/v1/cargas")).status_code == 401


@pytest.mark.parametrize("rol", ["gerente", "supervisor"])
async def test_los_puestos_de_arriba_si_cargan(cliente, sesion, semilla, catalogo, rol):
    """El gerente, desde la migración 0044; el supervisor, desde siempre."""
    await _usuario(sesion, semilla, "ARRIBA01", rol)
    cab = await _cab(cliente, "ARRIBA01")

    opciones = (await cliente.get("/v1/cargas/opciones", headers=cab)).json()
    assert [v["codigo"] for v in opciones["vendedores"]] == ["VEND01"]
    assert opciones["vendedores"][0]["camion"] == "Camión 01"
    assert [b["codigo"] for b in opciones["bodegas"]] == ["BODEGA_PRINCIPAL"]

    carga = await _abrir(cliente, cab, semilla)
    assert carga["estado"] == "borrador"
    assert carga["folio"].startswith("CG-")


# ---------------------------------------------------------------------------
# Capturar
# ---------------------------------------------------------------------------
async def test_la_bodega_se_ofrece_con_la_presentacion_mas_grande(
    cliente, semilla, catalogo
):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)

    [atun] = carga["surtido"]
    assert atun["nombre"] == "Atún en agua 140 g"
    assert atun["en_bodega"] == "480.000"
    assert atun["por_omision"] == "CAJA"
    assert [p["unidad"] for p in atun["presentaciones"]] == ["CAJA", "PZA"]


async def test_cajas_se_guardan_en_piezas_y_el_mismo_producto_se_suma(
    cliente, semilla, catalogo
):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)
    pedido = {"producto_id": str(catalogo["producto"]), "unidad": "CAJA", "cantidad": "2"}

    await cliente.post(f"/v1/cargas/{carga['id']}/renglones",
                       json={"renglones": [pedido]}, headers=cab)
    r = await cliente.post(f"/v1/cargas/{carga['id']}/renglones",
                           json={"renglones": [pedido]}, headers=cab)

    cuerpo = r.json()
    [renglon] = cuerpo["renglones"]
    assert renglon["cantidad"] == "96.000"  # 4 cajas × 24
    assert "Atún en agua 140 g 2 CAJA" in cuerpo["mensaje"]


async def test_se_guarda_lo_bueno_y_se_nombra_lo_malo(cliente, semilla, catalogo):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)

    r = await cliente.post(
        f"/v1/cargas/{carga['id']}/renglones",
        json={"renglones": [
            {"producto_id": str(catalogo["producto"]), "unidad": "CAJA", "cantidad": "2.5"},
            {"producto_id": str(catalogo["producto"]), "unidad": "PZA", "cantidad": "6"},
        ]},
        headers=cab,
    )

    cuerpo = r.json()
    assert [x["cantidad"] for x in cuerpo["renglones"]] == ["6.000"]
    assert "NO entraron" in cuerpo["mensaje"]
    assert "media caja" in cuerpo["mensaje"]


async def test_quitar_un_renglon(cliente, semilla, catalogo):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)
    r = await cliente.post(
        f"/v1/cargas/{carga['id']}/renglones",
        json={"renglones": [
            {"producto_id": str(catalogo["producto"]), "unidad": "CAJA", "cantidad": "1"},
        ]},
        headers=cab,
    )
    renglon = r.json()["renglones"][0]["id"]

    r = await cliente.post(f"/v1/cargas/{carga['id']}/renglones/{renglon}/quitar", headers=cab)
    assert r.json()["renglones"] == []


async def test_abrir_dos_veces_el_mismo_dia_devuelve_la_que_ya_existe(
    cliente, semilla, catalogo
):
    cab = await _cab(cliente)
    primera = await _abrir(cliente, cab, semilla)
    segunda = await _abrir(cliente, cab, semilla)
    assert segunda["id"] == primera["id"]
    assert "ya trae la carga" in segunda["mensaje"]


# ---------------------------------------------------------------------------
# Confirmar
# ---------------------------------------------------------------------------
async def test_confirmar_mueve_bodega_y_camion_y_avisa_al_telefono(
    cliente, sesion, semilla, catalogo
):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)
    await cliente.post(
        f"/v1/cargas/{carga['id']}/renglones",
        json={"renglones": [
            {"producto_id": str(catalogo["producto"]), "unidad": "CAJA", "cantidad": "10"},
        ]},
        headers=cab,
    )

    r = await cliente.post(f"/v1/cargas/{carga['id']}/confirmar", json={}, headers=cab)

    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "confirmada"
    assert cuerpo["editable"] is False
    assert "confirmada" in cuerpo["mensaje"]
    assert await _existencia(sesion, semilla["bodega"], catalogo["producto"]) == 240
    assert await _existencia(sesion, semilla["camion"], catalogo["producto"]) == 240
    # El delta que el teléfono del vendedor va a bajar.
    delta = (
        await sesion.execute(
            text("SELECT vendedor_id FROM change_log WHERE entidad = 'carga' "
                 "  AND entidad_id = :c"),
            {"c": uuid.UUID(carga["id"])},
        )
    ).scalars().all()
    assert delta and set(delta) == {semilla["vendedor"]}


async def test_confirmar_dos_veces_no_duplica(cliente, sesion, semilla, catalogo):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)
    await cliente.post(
        f"/v1/cargas/{carga['id']}/renglones",
        json={"renglones": [
            {"producto_id": str(catalogo["producto"]), "unidad": "CAJA", "cantidad": "1"},
        ]},
        headers=cab,
    )
    await cliente.post(f"/v1/cargas/{carga['id']}/confirmar", json={}, headers=cab)
    r = await cliente.post(f"/v1/cargas/{carga['id']}/confirmar", json={}, headers=cab)

    assert r.status_code == 409
    assert await _existencia(sesion, semilla["camion"], catalogo["producto"]) == 24


async def test_sin_renglones_no_se_confirma(cliente, semilla, catalogo):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)
    r = await cliente.post(f"/v1/cargas/{carga['id']}/confirmar", json={}, headers=cab)
    assert r.status_code == 422


async def test_con_ventas_sin_subir_no_se_confirma_sin_motivo(
    cliente, sesion, semilla, catalogo
):
    """La regla del §2.3, igual que en el panel: se ve, y se fuerza diciendo por qué."""
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en, "
             "                          cola_pendiente) "
             "VALUES (:d,:u,'Moto G54','activo',now(), 3)"),
        {"d": uuid.uuid4(), "u": semilla["vendedor"]},
    )
    await sesion.commit()
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)
    assert any("3 operación(es)" in b for b in carga["bloqueos"])
    await cliente.post(
        f"/v1/cargas/{carga['id']}/renglones",
        json={"renglones": [
            {"producto_id": str(catalogo["producto"]), "unidad": "CAJA", "cantidad": "1"},
        ]},
        headers=cab,
    )

    r = await cliente.post(f"/v1/cargas/{carga['id']}/confirmar", json={}, headers=cab)
    assert r.status_code == 409
    assert "No se puede confirmar" in r.json()["detail"]

    r = await cliente.post(
        f"/v1/cargas/{carga['id']}/confirmar",
        json={"motivo_forzado": "El teléfono de Juan se quedó sin batería"},
        headers=cab,
    )
    assert r.status_code == 200, r.text
    motivo = (
        await sesion.execute(
            text("SELECT motivo FROM auditoria WHERE entidad = 'carga' "
                 "  AND accion = 'carga_forzada' AND entidad_id = :c"),
            {"c": uuid.UUID(carga["id"])},
        )
    ).scalar_one()
    assert motivo == "El teléfono de Juan se quedó sin batería"


async def test_cancelar_un_borrador(cliente, semilla, catalogo):
    cab = await _cab(cliente)
    carga = await _abrir(cliente, cab, semilla)
    r = await cliente.post(f"/v1/cargas/{carga['id']}/cancelar", headers=cab)
    assert r.json()["estado"] == "cancelada"

    # Y ya no aparece entre las abiertas.
    abiertas = (await cliente.get("/v1/cargas", headers=cab)).json()
    assert carga["id"] not in [c["id"] for c in abiertas]
