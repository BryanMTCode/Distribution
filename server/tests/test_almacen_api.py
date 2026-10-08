"""`/v1/almacen`: existencias, entradas y traspasos desde la app de la oficina.

1. **Quién.** La oficina ve todos los almacenes; el vendedor no (su camión lo
   tiene en el teléfono). Escribir exige `inventario.ajustar`.
2. **Entradas con las mismas reglas que el panel**: la compra exige costo y
   deja la cuenta por pagar; el inventario inicial exige nota; confirmar dos
   veces no mete dos veces; cancelar pide motivo; a un camión no se recibe.
3. **Traspasos solo entre bodegas**, en un paso, sin dejar el origen en
   negativo y sin publicar nada a ningún teléfono.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR
from tests.test_compras import _id_proveedor, _proveedor
from tests.test_oficina_clientes_api import _cab, _usuario
from tests.test_panel_entradas import _entrar, _producto
from tests.test_panel_liquidacion import sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


async def _existencia(sesion, almacen, producto) -> Decimal:
    valor = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"),
            {"a": almacen, "p": producto},
        )
    ).scalar()
    return Decimal(valor) if valor is not None else Decimal(0)


async def _otra_bodega(sesion, codigo: str = "BODEGA_NORTE", nombre: str = "Bodega Norte"):
    bodega = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO almacenes (id, codigo, nombre, tipo) VALUES (:b, :c, :n, 'bodega')"),
        {"b": bodega, "c": codigo, "n": nombre},
    )
    await sesion.commit()
    return bodega


async def _producto_del_dia(sesion) -> uuid.UUID:
    return (
        await sesion.execute(text("SELECT id FROM productos WHERE sku = 'ATUN-140'"))
    ).scalar_one()


# ---------------------------------------------------------------------------
# Quién
# ---------------------------------------------------------------------------
async def test_el_vendedor_no_ve_los_almacenes(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
        "dispositivo_id": str(dia["dispositivo"]),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await cliente.get("/v1/almacen", headers=cab)).status_code == 403
    r = await cliente.post(
        "/v1/almacen/traspasos",
        json={"origen_id": str(semilla["bodega"]), "destino_id": str(uuid.uuid4()),
              "renglones": [{"producto_id": str(uuid.uuid4()), "unidad": "PZA",
                             "cantidad": "1"}]},
        headers=cab,
    )
    assert r.status_code == 403


async def test_el_gerente_ve_y_mueve_mercancia(cliente, sesion, semilla):
    """El gerente tiene `inventario.ajustar` (migración 0031): recibe y traspasa."""
    await sembrar_dia_de_trabajo(sesion, semilla)
    norte = await _otra_bodega(sesion)
    atun = await _producto_del_dia(sesion)
    await _usuario(sesion, semilla, "GER01", "gerente")
    cab = await _cab(cliente, "GER01")

    assert (await cliente.get("/v1/almacen", headers=cab)).status_code == 200
    r = await cliente.post(
        "/v1/almacen/traspasos",
        json={"origen_id": str(semilla["bodega"]), "destino_id": str(norte),
              "renglones": [{"producto_id": str(atun), "unidad": "PZA", "cantidad": "10"}]},
        headers=cab,
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Existencias
# ---------------------------------------------------------------------------
async def test_las_bodegas_primero_y_lo_que_tiene_cada_almacen(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)

    lista = (await cliente.get("/v1/almacen", headers=cab)).json()
    assert [a["tipo"] for a in lista] == ["bodega", "camion"]
    bodega, camion = lista
    assert bodega["piezas"] == "760.000"
    assert bodega["productos"] == 1
    assert camion["responsable"] == "Juan Pérez"
    assert camion["negativos"] == 0

    cuerpo = (
        await cliente.get(f"/v1/almacen/{semilla['bodega']}/existencias", headers=cab)
    ).json()
    assert cuerpo["almacen"]["nombre"] == "Bodega"
    [atun] = cuerpo["existencias"]
    assert atun["sku"] == "ATUN-140"
    assert atun["cantidad"] == "760.000"
    # La presentación grande primero: es la que se cuenta en el anaquel.
    assert [p["unidad"] for p in atun["presentaciones"]] == ["CAJA", "PZA"]

    vacio = (
        await cliente.get(
            f"/v1/almacen/{semilla['bodega']}/existencias?q=refresco", headers=cab
        )
    ).json()
    assert vacio["existencias"] == []


async def test_los_negativos_se_ven(cliente, sesion, semilla):
    """Un camión en negativo es un problema que se cuenta, no que se esconde."""
    await sembrar_dia_de_trabajo(sesion, semilla)
    atun = await _producto_del_dia(sesion)
    await sesion.execute(
        text("UPDATE existencias SET cantidad = -3 WHERE almacen_id = :c AND producto_id = :p"),
        {"c": semilla["camion"], "p": atun},
    )
    await sesion.commit()
    cab = await _cab(cliente)

    camion = [a for a in (await cliente.get("/v1/almacen", headers=cab)).json()
              if a["tipo"] == "camion"][0]
    assert camion["negativos"] == 1
    cuerpo = (
        await cliente.get(f"/v1/almacen/{semilla['camion']}/existencias", headers=cab)
    ).json()
    assert cuerpo["existencias"][0]["cantidad"] == "-3.000"


async def test_un_almacen_que_no_existe_es_404(cliente, semilla):
    cab = await _cab(cliente)
    r = await cliente.get(f"/v1/almacen/{uuid.uuid4()}/existencias", headers=cab)
    assert r.status_code == 404


async def test_los_productos_para_traspasar_son_los_que_hay(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    await _producto(sesion)  # COCA600, sin existencia en ningún lado.
    cab = await _cab(cliente)
    ruta = f"/v1/almacen/productos?almacen_id={semilla['bodega']}"

    todos = (await cliente.get(ruta, headers=cab)).json()
    assert {p["sku"] for p in todos} == {"ATUN-140", "COCA600"}
    con = (await cliente.get(ruta + "&solo_con_existencia=true", headers=cab)).json()
    assert [(p["sku"], p["existencia"]) for p in con] == [("ATUN-140", "760.000")]


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------
async def test_una_compra_completa_desde_la_app(cliente, sesion, semilla):
    """Abrir, capturar, confirmar: la mercancía entra y queda la deuda."""
    await _entrar(cliente)
    await _proveedor(cliente)
    proveedor = await _id_proveedor(sesion)
    coca = await _producto(sesion)
    cab = await _cab(cliente)

    catalogo = (await cliente.get("/v1/almacen/entradas", headers=cab)).json()
    assert [b["nombre"] for b in catalogo["bodegas"]] == ["Bodega"]
    assert [p["nombre"] for p in catalogo["proveedores"]] == ["Abarrotes del Centro"]
    assert {m["clave"] for m in catalogo["motivos"]} == {"compra", "inicial", "ajuste"}

    r = await cliente.post(
        "/v1/almacen/entradas",
        json={"almacen_destino_id": str(semilla["bodega"]), "motivo": "compra",
              "proveedor_id": str(proveedor), "referencia": "F-100"},
        headers=cab,
    )
    assert r.status_code == 200, r.text
    entrada = r.json()
    assert entrada["editable"] is True
    assert entrada["exige_costo"] is True
    assert entrada["proveedor"] == "Abarrotes del Centro"
    ruta = f"/v1/almacen/entradas/{entrada['id']}"

    # Sin costo, una compra no pasa: sería una deuda invisible.
    r = await cliente.post(ruta + "/renglones",
                           json={"sku": "COCA600", "unidad": "CAJA", "cantidad": "10"},
                           headers=cab)
    assert r.status_code == 409
    assert "obligatorio" in r.json()["detail"]
    # Media caja tampoco.
    r = await cliente.post(ruta + "/renglones",
                           json={"sku": "COCA600", "unidad": "CAJA", "cantidad": "1.5",
                                 "costo": "296"},
                           headers=cab)
    assert r.status_code == 409

    r = await cliente.post(ruta + "/renglones",
                           json={"sku": "COCA600", "unidad": "CAJA", "cantidad": "10",
                                 "costo": "296.00"},
                           headers=cab)
    assert r.status_code == 200, r.text
    [renglon] = r.json()["renglones"]
    assert renglon["cantidad"] == "240.000"
    assert renglon["proyectado"] == "240.000"
    assert r.json()["importe_capturado"] == "2960.00"
    assert "240" in r.json()["mensaje"]
    # El borrador no mueve inventario.
    assert await _existencia(sesion, semilla["bodega"], coca) == 0

    r = await cliente.post(ruta + "/confirmar", headers=cab)
    assert r.status_code == 200, r.text
    confirmada = r.json()
    assert confirmada["estado"] == "confirmada"
    assert confirmada["editable"] is False
    assert confirmada["cuenta"]["proveedor"] == "Abarrotes del Centro"
    assert confirmada["cuenta"]["saldo"] == "2960.00"
    assert await _existencia(sesion, semilla["bodega"], coca) == 240

    # Confirmar otra vez no la mete dos veces.
    r = await cliente.post(ruta + "/confirmar", headers=cab)
    assert r.status_code == 409
    assert await _existencia(sesion, semilla["bodega"], coca) == 240

    lista = (await cliente.get("/v1/almacen/entradas", headers=cab)).json()["entradas"]
    assert [(e["folio"], e["estado"], e["piezas"]) for e in lista] == [
        (confirmada["folio"], "confirmada", "240.000")
    ]


async def test_el_inventario_inicial_pide_nota(cliente, sesion, semilla):
    cab = await _cab(cliente)
    cuerpo = {"almacen_destino_id": str(semilla["bodega"]), "motivo": "inicial"}
    r = await cliente.post("/v1/almacen/entradas", json=cuerpo, headers=cab)
    assert r.status_code == 409
    assert "nota" in r.json()["detail"]

    r = await cliente.post("/v1/almacen/entradas",
                           json={**cuerpo, "nota": "Conteo del arranque"}, headers=cab)
    assert r.status_code == 200
    assert r.json()["exige_costo"] is False


async def test_a_un_camion_no_se_recibe(cliente, sesion, semilla):
    cab = await _cab(cliente)
    r = await cliente.post(
        "/v1/almacen/entradas",
        json={"almacen_destino_id": str(semilla["camion"]), "motivo": "ajuste"},
        headers=cab,
    )
    assert r.status_code == 409
    assert "carga" in r.json()["detail"]


async def test_quitar_y_cancelar_un_borrador(cliente, sesion, semilla):
    await _producto(sesion)
    cab = await _cab(cliente)
    entrada = (
        await cliente.post(
            "/v1/almacen/entradas",
            json={"almacen_destino_id": str(semilla["bodega"]), "motivo": "ajuste"},
            headers=cab,
        )
    ).json()
    ruta = f"/v1/almacen/entradas/{entrada['id']}"
    con = (
        await cliente.post(ruta + "/renglones",
                           json={"sku": "COCA600", "unidad": "PZA", "cantidad": "7"},
                           headers=cab)
    ).json()
    [renglon] = con["renglones"]

    sin = (await cliente.post(ruta + f"/renglones/{renglon['id']}/quitar", headers=cab)).json()
    assert sin["renglones"] == []

    # Sin renglones no se confirma.
    assert (await cliente.post(ruta + "/confirmar", headers=cab)).status_code == 409

    r = await cliente.post(ruta + "/cancelar", json={"motivo": "  "}, headers=cab)
    assert r.status_code == 409
    assert "por qué" in r.json()["detail"]
    r = await cliente.post(ruta + "/cancelar", json={"motivo": "Llegó mal"}, headers=cab)
    assert r.json()["estado"] == "cancelada"
    # Y cancelada ya no recibe renglones.
    r = await cliente.post(ruta + "/renglones",
                           json={"sku": "COCA600", "unidad": "PZA", "cantidad": "7"},
                           headers=cab)
    assert r.status_code == 409


async def test_una_entrada_que_no_existe_es_404(cliente, semilla):
    cab = await _cab(cliente)
    ruta = f"/v1/almacen/entradas/{uuid.uuid4()}"
    assert (await cliente.get(ruta, headers=cab)).status_code == 404
    assert (await cliente.post(ruta + "/confirmar", headers=cab)).status_code == 404


# ---------------------------------------------------------------------------
# Traspasos
# ---------------------------------------------------------------------------
async def test_un_traspaso_mueve_los_dos_lados_y_no_publica(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    norte = await _otra_bodega(sesion)
    atun = await _producto_del_dia(sesion)
    cab = await _cab(cliente)
    antes = (await sesion.execute(text("SELECT max(cursor) FROM change_log"))).scalar()

    r = await cliente.post(
        "/v1/almacen/traspasos",
        json={"origen_id": str(semilla["bodega"]), "destino_id": str(norte),
              # Dos renglones del mismo producto: un solo movimiento de 60.
              "renglones": [{"producto_id": str(atun), "unidad": "CAJA", "cantidad": "2"},
                            {"producto_id": str(atun), "unidad": "PZA", "cantidad": "12"}],
              "nota": "Para surtir el norte"},
        headers=cab,
    )
    assert r.status_code == 200, r.text
    hecho = r.json()
    assert hecho["folio"].startswith("TR-")
    assert "60 piezas de Bodega a Bodega Norte" in hecho["mensaje"]

    assert await _existencia(sesion, semilla["bodega"], atun) == 700
    assert await _existencia(sesion, norte, atun) == 60

    [mov] = (
        await sesion.execute(
            text("SELECT tipo, almacen_origen_id, almacen_destino_id, cantidad "
                 "  FROM movimientos_inventario WHERE documento_id = :t"),
            {"t": hecho["id"]},
        )
    ).all()
    assert (mov.tipo, mov.almacen_origen_id, mov.almacen_destino_id, mov.cantidad) == (
        "traspaso", semilla["bodega"], norte, Decimal(60)
    )
    estado = (
        await sesion.execute(text("SELECT estado FROM traspasos WHERE id = :t"),
                             {"t": hecho["id"]})
    ).scalar_one()
    assert estado == "aceptado"
    # Ningún teléfono se entera: no es asunto de ningún camión.
    despues = (await sesion.execute(text("SELECT max(cursor) FROM change_log"))).scalar()
    assert despues == antes

    lista = (await cliente.get("/v1/almacen/traspasos", headers=cab)).json()
    [t] = lista["traspasos"]
    assert (t["folio"], t["origen"], t["destino"], t["piezas"], t["observaciones"]) == (
        hecho["folio"], "Bodega", "Bodega Norte", "60.000", "Para surtir el norte"
    )
    assert {b["nombre"] for b in lista["bodegas"]} == {"Bodega", "Bodega Norte"}


async def test_no_se_traspasa_lo_que_no_hay(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    norte = await _otra_bodega(sesion)
    atun = await _producto_del_dia(sesion)
    cab = await _cab(cliente)

    r = await cliente.post(
        "/v1/almacen/traspasos",
        json={"origen_id": str(semilla["bodega"]), "destino_id": str(norte),
              "renglones": [{"producto_id": str(atun), "unidad": "CAJA", "cantidad": "40"}]},
        headers=cab,
    )
    assert r.status_code == 409
    assert "No alcanza en Bodega" in r.json()["detail"]
    assert "hay 760, pides 960" in r.json()["detail"]
    # Todo o nada: nada se movió.
    assert await _existencia(sesion, semilla["bodega"], atun) == 760
    assert await _existencia(sesion, norte, atun) == 0
    assert (
        await sesion.execute(text("SELECT count(*) FROM traspasos"))
    ).scalar_one() == 0


async def test_un_camion_no_entra_a_un_traspaso(cliente, sesion, semilla):
    """§0.2: al camión se le sube con una carga y baja con la devolución."""
    await sembrar_dia_de_trabajo(sesion, semilla)
    atun = await _producto_del_dia(sesion)
    cab = await _cab(cliente)
    renglones = [{"producto_id": str(atun), "unidad": "PZA", "cantidad": "1"}]
    en_el_camion = await _existencia(sesion, semilla["camion"], atun)

    for origen, destino in ((semilla["bodega"], semilla["camion"]),
                            (semilla["camion"], semilla["bodega"])):
        r = await cliente.post(
            "/v1/almacen/traspasos",
            json={"origen_id": str(origen), "destino_id": str(destino),
                  "renglones": renglones},
            headers=cab,
        )
        assert r.status_code == 409
        assert "no una bodega" in r.json()["detail"]
    assert await _existencia(sesion, semilla["camion"], atun) == en_el_camion


async def test_ni_a_la_misma_bodega_ni_media_caja(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    norte = await _otra_bodega(sesion)
    atun = await _producto_del_dia(sesion)
    cab = await _cab(cliente)

    r = await cliente.post(
        "/v1/almacen/traspasos",
        json={"origen_id": str(semilla["bodega"]), "destino_id": str(semilla["bodega"]),
              "renglones": [{"producto_id": str(atun), "unidad": "PZA", "cantidad": "1"}]},
        headers=cab,
    )
    assert r.status_code == 409
    assert "misma bodega" in r.json()["detail"]

    r = await cliente.post(
        "/v1/almacen/traspasos",
        json={"origen_id": str(semilla["bodega"]), "destino_id": str(norte),
              "renglones": [{"producto_id": str(atun), "unidad": "CAJA",
                             "cantidad": "0.5"}]},
        headers=cab,
    )
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Atún en agua 140 g:")

    r = await cliente.post(
        "/v1/almacen/traspasos",
        json={"origen_id": str(semilla["bodega"]), "destino_id": str(norte),
              "renglones": [{"producto_id": str(atun), "unidad": "PAQ", "cantidad": "1"}]},
        headers=cab,
    )
    assert r.status_code == 409
    assert "presentación PAQ" in r.json()["detail"]
