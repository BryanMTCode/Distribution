"""Catálogo: productos, presentaciones y precios."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _token(cliente, codigo: str) -> str:
    r = await cliente.post("/v1/auth/login", json={"codigo": codigo, "password": PASSWORD_VENDEDOR})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _cab(cliente, codigo="ADMIN01") -> dict:
    return {"Authorization": f"Bearer {await _token(cliente, codigo)}"}


PRODUCTO = {
    "sku": "frijol-1kg",
    "nombre": "Frijol negro 1 kg",
    "unidad_base": "PZA",
    "unidades": [
        {"codigo": "PZA", "factor": "1.000", "es_default": True},
        {"codigo": "CAJA", "factor": "24.000"},
    ],
}


async def test_las_unidades_sembradas_son_pieza_y_caja(cliente, semilla):
    """ADR 0002: solo PZA y CAJA, sin granel."""
    r = await cliente.get("/v1/catalogo/unidades", headers=await _cab(cliente))
    assert r.status_code == 200
    codigos = {u["codigo"] for u in r.json()}
    assert codigos == {"PZA", "CAJA"}
    assert all(not u["fraccionable"] for u in r.json())


async def test_crear_producto_con_sus_presentaciones(cliente, semilla):
    r = await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=await _cab(cliente))
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["sku"] == "FRIJOL-1KG"          # normalizado
    assert {u["unidad_codigo"] for u in cuerpo["unidades"]} == {"PZA", "CAJA"}


async def test_sku_duplicado_es_conflicto(cliente, semilla):
    cab = await _cab(cliente)
    await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=cab)
    r = await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=cab)
    assert r.status_code == 409


async def test_el_sku_se_normaliza_para_evitar_duplicados_invisibles(cliente, semilla):
    """'frijol-1kg' y ' Frijol-1KG ' son el mismo producto. Sin normalizar,
    conviven dos SKU y el inventario deja de cuadrar sin que nadie lo vea."""
    cab = await _cab(cliente)
    await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=cab)
    r = await cliente.post(
        "/v1/catalogo/productos", json={**PRODUCTO, "sku": "  Frijol-1KG  "}, headers=cab
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    ("caso", "unidades", "base"),
    [
        ("la unidad base no está entre las presentaciones",
         [{"codigo": "CAJA", "factor": "24.000", "es_default": True}], "PZA"),
        ("la unidad base no tiene factor 1",
         [{"codigo": "PZA", "factor": "2.000", "es_default": True}], "PZA"),
        ("sin presentación por defecto",
         [{"codigo": "PZA", "factor": "1.000"}], "PZA"),
        ("dos presentaciones por defecto",
         [{"codigo": "PZA", "factor": "1.000", "es_default": True},
          {"codigo": "CAJA", "factor": "24.000", "es_default": True}], "PZA"),
        ("dos presentaciones con el mismo factor",
         [{"codigo": "PZA", "factor": "1.000", "es_default": True},
          {"codigo": "CAJA", "factor": "1.000"}], "PZA"),
    ],
)
async def test_configuraciones_que_descuadrarian_el_inventario(
    cliente, semilla, caso, unidades, base
):
    r = await cliente.post(
        "/v1/catalogo/productos",
        json={**PRODUCTO, "unidad_base": base, "unidades": unidades},
        headers=await _cab(cliente),
    )
    assert r.status_code == 422, f"no se detectó: {caso}"


async def test_unidad_inexistente_se_rechaza(cliente, semilla):
    r = await cliente.post(
        "/v1/catalogo/productos",
        json={**PRODUCTO, "unidades": [
            {"codigo": "PZA", "factor": "1.000", "es_default": True},
            {"codigo": "KG", "factor": "1000.000"},
        ]},
        headers=await _cab(cliente),
    )
    assert r.status_code == 422
    assert "KG" in r.json()["detail"]


async def test_el_vendedor_ve_el_catalogo_pero_no_lo_edita(cliente, semilla, sesion):
    """La UI oculta, el servidor prohíbe."""
    from sqlalchemy import text
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    token = (
        await cliente.post("/v1/auth/login", json={
            "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(dispositivo_id),
        })
    ).json()["access_token"]
    cab = {"Authorization": f"Bearer {token}"}

    assert (await cliente.get("/v1/catalogo/productos", headers=cab)).status_code == 200
    creacion = await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=cab)
    assert creacion.status_code == 403


async def test_buscar_por_nombre_y_por_sku(cliente, semilla):
    cab = await _cab(cliente)
    await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=cab)
    await cliente.post(
        "/v1/catalogo/productos",
        json={**PRODUCTO, "sku": "AZUCAR-1KG", "nombre": "Azúcar estándar 1 kg"},
        headers=cab,
    )
    por_nombre = await cliente.get("/v1/catalogo/productos?busqueda=frijol", headers=cab)
    assert por_nombre.json()["total"] == 1
    por_sku = await cliente.get("/v1/catalogo/productos?busqueda=AZUCAR", headers=cab)
    assert por_sku.json()["total"] == 1


async def test_no_se_puede_cambiar_la_unidad_base(cliente, semilla):
    """Cambiarla reinterpretaría todos los movimientos ya registrados."""
    cab = await _cab(cliente)
    creado = (await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=cab)).json()
    r = await cliente.patch(
        f"/v1/catalogo/productos/{creado['id']}", json={"unidad_base": "CAJA"}, headers=cab
    )
    assert r.status_code == 422      # extra="forbid" en el esquema


# ---------------------------------------------------------------------------
# Precios
# ---------------------------------------------------------------------------

async def _producto(cliente, cab) -> dict:
    return (await cliente.post("/v1/catalogo/productos", json=PRODUCTO, headers=cab)).json()


async def test_fijar_precios_y_versionarlos(cliente, semilla):
    """La versión es lo que después permite detectar una venta hecha con lista
    vieja sin comparar importes partida por partida."""
    cab = await _cab(cliente)
    producto = await _producto(cliente, cab)
    lista = semilla["lista_precios"]

    primera = await cliente.put(
        f"/v1/catalogo/listas-precios/{lista}/precios",
        json=[{"producto_id": producto["id"], "unidad_codigo": "PZA", "precio": "25.5000"}],
        headers=cab,
    )
    assert primera.status_code == 200
    assert primera.json()[0]["version"] == 1

    segunda = await cliente.put(
        f"/v1/catalogo/listas-precios/{lista}/precios",
        json=[{"producto_id": producto["id"], "unidad_codigo": "PZA", "precio": "27.0000"}],
        headers=cab,
    )
    assert segunda.json()[0]["version"] == 2


async def test_reenviar_el_mismo_precio_no_sube_la_version(cliente, semilla):
    """Si la versión subiera sin cambio real, cada sincronización marcaría todas
    las ventas del día como hechas con lista obsoleta."""
    cab = await _cab(cliente)
    producto = await _producto(cliente, cab)
    lista = semilla["lista_precios"]
    cuerpo = [{"producto_id": producto["id"], "unidad_codigo": "PZA", "precio": "25.5000"}]

    await cliente.put(f"/v1/catalogo/listas-precios/{lista}/precios", json=cuerpo, headers=cab)
    r = await cliente.put(f"/v1/catalogo/listas-precios/{lista}/precios", json=cuerpo, headers=cab)
    assert r.json()[0]["version"] == 1


async def test_precio_sobre_una_presentacion_que_el_producto_no_vende(cliente, semilla, sesion):
    from sqlalchemy import text
    cab = await _cab(cliente)
    producto = await _producto(cliente, cab)
    await sesion.execute(
        text("DELETE FROM producto_unidades WHERE producto_id = :p AND unidad_codigo = 'CAJA'"),
        {"p": uuid.UUID(producto["id"])},
    )
    await sesion.commit()

    r = await cliente.put(
        f"/v1/catalogo/listas-precios/{semilla['lista_precios']}/precios",
        json=[{"producto_id": producto["id"], "unidad_codigo": "CAJA", "precio": "600.0000"}],
        headers=cab,
    )
    assert r.status_code == 422


async def test_precio_minimo_mayor_al_precio_se_rechaza(cliente, semilla):
    cab = await _cab(cliente)
    producto = await _producto(cliente, cab)
    r = await cliente.put(
        f"/v1/catalogo/listas-precios/{semilla['lista_precios']}/precios",
        json=[{"producto_id": producto["id"], "unidad_codigo": "PZA",
               "precio": "20.0000", "precio_minimo": "25.0000"}],
        headers=cab,
    )
    assert r.status_code == 422


async def test_el_lote_no_puede_traer_la_misma_clave_dos_veces(cliente, semilla):
    """Dos renglones para el mismo producto y unidad: cuál gana sería
    arbitrario, así que se rechaza el lote completo."""
    cab = await _cab(cliente)
    producto = await _producto(cliente, cab)
    r = await cliente.put(
        f"/v1/catalogo/listas-precios/{semilla['lista_precios']}/precios",
        json=[
            {"producto_id": producto["id"], "unidad_codigo": "PZA", "precio": "20.0000"},
            {"producto_id": producto["id"], "unidad_codigo": "PZA", "precio": "30.0000"},
        ],
        headers=cab,
    )
    assert r.status_code == 422
