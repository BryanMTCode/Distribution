"""Deltas: cursor monotónico, acotamiento por ruta y la trampa del snapshot."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _cab_vendedor(cliente, sesion, semilla) -> dict:
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo_id),
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _alta_producto(sesion, sku: str) -> None:
    await sesion.execute(
        text("INSERT INTO productos(id, sku, nombre, unidad_base, creado_en, actualizado_en) "
             "VALUES (gen_random_uuid(), :sku, :sku, 'PZA', now(), now())"),
        {"sku": sku},
    )
    await sesion.commit()


async def test_el_catalogo_llega_por_el_delta(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "FRIJOL-1KG")

    r = await cliente.get("/v1/sync/pull?cursor=0", headers=cab)
    assert r.status_code == 200
    entidades = {c["entidad"] for c in r.json()["cambios"]}
    assert "producto" in entidades


async def test_el_cursor_avanza_y_no_repite(cliente, semilla, sesion):
    """Traer dos veces desde el mismo cursor devuelve lo mismo; traer desde el
    cursor entregado no devuelve nada nuevo."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "A")

    primera = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()
    assert primera["cambios"]

    segunda = (
        await cliente.get(f"/v1/sync/pull?cursor={primera['cursor']}", headers=cab)
    ).json()
    assert segunda["cambios"] == []
    assert segunda["cursor"] == primera["cursor"]


async def test_un_cambio_posterior_aparece_en_el_siguiente_delta(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "A")
    cursor = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()["cursor"]

    await _alta_producto(sesion, "B")
    delta = (await cliente.get(f"/v1/sync/pull?cursor={cursor}", headers=cab)).json()
    assert len(delta["cambios"]) == 1
    assert delta["cambios"][0]["payload"]["sku"] == "B"


async def test_la_paginacion_avisa_que_hay_mas(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    for i in range(5):
        await _alta_producto(sesion, f"P{i}")

    pagina = (await cliente.get("/v1/sync/pull?cursor=0&limite=2", headers=cab)).json()
    assert len(pagina["cambios"]) == 2
    assert pagina["hay_mas"] is True

    # Recorriendo el cursor se llega al final sin saltarse nada.
    vistos, cursor, hay_mas = [], 0, True
    while hay_mas:
        d = (await cliente.get(f"/v1/sync/pull?cursor={cursor}&limite=2", headers=cab)).json()
        vistos += d["cambios"]
        cursor, hay_mas = d["cursor"], d["hay_mas"]
    assert len({c["cursor"] for c in vistos}) == len(vistos), "el recorrido repitió cambios"


async def test_el_vendedor_no_recibe_clientes_de_otras_rutas(cliente, semilla, sesion):
    """El acotamiento va en el servidor: el dato no sale si no corresponde."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    otra_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas(id, codigo, nombre) VALUES (:r,'R09','Ruta 9')"), {"r": otra_ruta}
    )
    for ruta, nombre in ((semilla["ruta"], "De mi ruta"), (otra_ruta, "De ruta ajena")):
        await sesion.execute(
            text("INSERT INTO clientes(id, nombre_comercial, ruta_id, creado_en, actualizado_en) "
                 "VALUES (gen_random_uuid(), :n, :r, now(), now())"),
            {"n": nombre, "r": ruta},
        )
    await sesion.commit()

    delta = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()
    nombres = {
        c["payload"]["nombre_comercial"]
        for c in delta["cambios"] if c["entidad"] == "cliente"
    }
    assert nombres == {"De mi ruta"}


async def test_el_delta_no_entrega_transacciones_en_vuelo(cliente, semilla, sesion, motor):
    """La trampa que hace inservible un cursor ingenuo.

    La secuencia del BIGSERIAL avanza al INSERT, no al COMMIT. Si el pull
    entregara el cursor 120 mientras el 119 sigue en una transacción abierta,
    al confirmarse el 119 quedaría por debajo de la marca de agua del
    dispositivo **para siempre**: ese precio nuevo no llegaría nunca y el
    vendedor seguiría vendiendo al viejo.

    El filtro `xid < pg_snapshot_xmin(pg_current_snapshot())` lo evita.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "CONFIRMADO-1")

    fabrica = async_sessionmaker(motor, expire_on_commit=False)
    async with fabrica() as en_vuelo:
        # Transacción abierta y SIN confirmar: ocupa un cursor.
        await en_vuelo.execute(
            text("INSERT INTO productos(id, sku, nombre, unidad_base, creado_en, actualizado_en) "
                 "VALUES (gen_random_uuid(), 'EN-VUELO', 'En vuelo', 'PZA', now(), now())")
        )
        await en_vuelo.flush()

        # Otra escritura que SÍ confirma, con un cursor mayor.
        await _alta_producto(sesion, "CONFIRMADO-2")

        delta = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()
        skus = {c["payload"]["sku"] for c in delta["cambios"] if c["entidad"] == "producto"}
        assert "EN-VUELO" not in skus, "se entregó una fila sin confirmar"
        # Y tampoco se salta al de más allá: la marca de agua se detiene antes.
        assert "CONFIRMADO-2" not in skus, "el cursor rebasó una transacción en vuelo"
        assert skus == {"CONFIRMADO-1"}
        cursor_seguro = delta["cursor"]

        await en_vuelo.commit()

    # Ya confirmada, aparece — y el de más allá con ella.
    delta = (await cliente.get(f"/v1/sync/pull?cursor={cursor_seguro}", headers=cab)).json()
    skus = {c["payload"]["sku"] for c in delta["cambios"] if c["entidad"] == "producto"}
    assert skus == {"EN-VUELO", "CONFIRMADO-2"}


async def test_el_estado_de_sync_reporta_el_rezago(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "A")
    await _alta_producto(sesion, "B")

    antes = (await cliente.get("/v1/sync/estado", headers=cab)).json()
    assert antes["ultimo_cursor_pull"] == 0
    assert antes["cambios_pendientes"] >= 2

    await cliente.get("/v1/sync/pull?cursor=0", headers=cab)
    despues = (await cliente.get("/v1/sync/estado", headers=cab)).json()
    assert despues["cambios_pendientes"] == 0
    assert despues["ultima_sync_pull_en"] is not None


async def test_el_cursor_del_dispositivo_solo_avanza(cliente, semilla, sesion):
    """Un pull viejo que llegue tarde no debe retroceder la marca de agua."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "A")
    await _alta_producto(sesion, "B")

    alto = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()["cursor"]
    await cliente.get("/v1/sync/pull?cursor=0&limite=1", headers=cab)

    estado = (await cliente.get("/v1/sync/estado", headers=cab)).json()
    assert estado["ultimo_cursor_pull"] == alto
