"""Ingesta de lotes: idempotencia, aislamiento de fallos y cuarentena.

Estas son las pruebas que deciden si el sistema sobrevive a una red mala.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.ayudas_sync import a_json, operacion_cliente, sobre
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


async def _contar(sesion, tabla: str) -> int:
    return (await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))).scalar_one()


# ---------------------------------------------------------------------------
# El camino feliz
# ---------------------------------------------------------------------------

async def test_un_sobre_se_aplica(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    s = sobre(operacion_cliente("Abarrotes Doña Mary"))

    r = await cliente.post("/v1/sync/push", json=a_json([s]), headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["aceptadas"] == 1
    assert await _contar(sesion, "clientes") == 1


async def test_un_sobre_agrupa_varias_operaciones(cliente, semilla, sesion):
    """Todo lo de una visita entra junto o no entra nada."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    s = sobre(operacion_cliente("Primero"), operacion_cliente("Segundo"),
              visita_id=uuid.uuid4())

    r = await cliente.post("/v1/sync/push", json=a_json([s]), headers=cab)
    assert r.json()["aceptadas"] == 1
    assert len(r.json()["resultados"][0]["entidades"]) == 2
    assert await _contar(sesion, "clientes") == 2


# ---------------------------------------------------------------------------
# Idempotencia: el corazón del diseño
# ---------------------------------------------------------------------------

async def test_reenviar_el_mismo_lote_no_duplica_nada(cliente, semilla, sesion):
    """La red cortó justo después de aplicar y el teléfono reenvió el lote
    completo. Es el caso más común, y no debe cambiar nada."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    cuerpo = a_json([sobre(secuencia=1), sobre(secuencia=2)])

    primera = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert primera.json()["aceptadas"] == 2

    for _ in range(3):
        repetida = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
        assert repetida.json()["aceptadas"] == 0
        assert repetida.json()["duplicadas"] == 2

    assert await _contar(sesion, "clientes") == 2


async def test_el_mismo_sobre_en_otro_lote_sigue_siendo_duplicado(cliente, semilla, sesion):
    """El teléfono reorganizó su cola y mandó el sobre en un lote distinto. La
    llave de idempotencia es el sobre, no el lote."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    s = sobre()

    await cliente.post("/v1/sync/push", json=a_json([s]), headers=cab)
    otra_vez = await cliente.post(
        "/v1/sync/push", json=a_json([s], lote_id=uuid.uuid4()), headers=cab
    )
    assert otra_vez.json()["duplicadas"] == 1
    assert await _contar(sesion, "clientes") == 1


@pytest.mark.parametrize("manipulacion", ["contenido", "hash"])
async def test_mismo_id_con_contenido_distinto_es_alarma(
    cliente, semilla, sesion, manipulacion
):
    """Bug del cliente o manipulación.

    Dos variantes, y las dos tienen que dar señal: alterar el contenido
    conservando el hash, o declarar otro hash. En un reenvío no se aplica nada
    de todos modos, pero un equipo manipulado es algo que la oficina tiene que
    ver.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)
    original = sobre(operacion_cliente("El nombre verdadero"))
    await cliente.post("/v1/sync/push", json=a_json([original]), headers=cab)

    impostor = a_json([original])
    if manipulacion == "contenido":
        impostor["sobres"][0]["operaciones"][0]["datos"]["nombre_comercial"] = "Alterado"
    else:
        impostor["sobres"][0]["hash_payload"] = "f" * 64

    r = await cliente.post("/v1/sync/push", json=impostor, headers=cab)
    assert r.json()["rechazadas"] == 1
    assert r.json()["resultados"][0]["error_codigo"] == "hash_no_coincide"

    # El nombre original no se tocó y el intento quedó registrado.
    nombre = (
        await sesion.execute(text("SELECT nombre_comercial FROM clientes"))
    ).scalar_one()
    assert nombre == "El nombre verdadero"
    assert await _contar(sesion, "sync_cuarentena") == 1


async def test_un_hash_que_no_corresponde_al_contenido_se_rechaza(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    cuerpo = a_json([sobre()])
    cuerpo["sobres"][0]["hash_payload"] = "0" * 64

    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.json()["rechazadas"] == 1
    assert await _contar(sesion, "clientes") == 0
    assert await _contar(sesion, "sync_cuarentena") == 1


# ---------------------------------------------------------------------------
# Aislamiento de fallos: LA prueba de la Fase 2
# ---------------------------------------------------------------------------

async def test_un_sobre_malo_no_tira_el_resto_del_lote(cliente, semilla, sesion):
    """Cuando PostgreSQL lanza un IntegrityError la transacción completa queda
    abortada. Sin un SAVEPOINT por sobre, el número 3 de este lote se llevaría
    por delante a los otros cuatro aunque fueran válidos.

    Ése es el fallo que deja al vendedor con la cola atorada y sin poder
    vender.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)
    buenos = [sobre(secuencia=i) for i in (1, 2, 4, 5)]
    malo = sobre(
        # Sin nombre_comercial: el manejador lo rechaza.
        operacion_cliente(),
        secuencia=3,
    )
    malo.operaciones[0].datos.pop("nombre_comercial")
    malo_con_hash = sobre(malo.operaciones[0], secuencia=3)

    r = await cliente.post(
        "/v1/sync/push", json=a_json([*buenos, malo_con_hash]), headers=cab
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["aceptadas"] == 4, "el sobre malo se llevó a los buenos"
    assert cuerpo["rechazadas"] == 1
    assert await _contar(sesion, "clientes") == 4
    assert await _contar(sesion, "sync_cuarentena") == 1


async def test_el_payload_rechazado_se_conserva_integro(cliente, semilla, sesion):
    """Un payload perdido es dinero perdido: la cuarentena guarda el sobre tal
    como llegó para que la oficina pueda repararlo."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    op = operacion_cliente()
    op.datos.pop("nombre_comercial")
    op.datos["telefono"] = "5512345678"

    await cliente.post("/v1/sync/push", json=a_json([sobre(op)]), headers=cab)

    fila = (
        await sesion.execute(
            text("SELECT payload, error_codigo, estado FROM sync_cuarentena")
        )
    ).mappings().one()
    assert fila["estado"] == "pendiente"
    assert fila["error_codigo"] == "payload_invalido"
    assert fila["payload"]["operaciones"][0]["datos"]["telefono"] == "5512345678"


async def test_un_tipo_desconocido_va_a_cuarentena(cliente, semilla, sesion):
    """Una app más nueva que el servidor. No se pierde el dato: se guarda."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    from app.domain.sync.sobres import Operacion
    futuro = sobre(Operacion("venta.telepatica", uuid.uuid4(), {"x": "1"}))

    r = await cliente.post("/v1/sync/push", json=a_json([futuro]), headers=cab)
    assert r.json()["resultados"][0]["error_codigo"] == "tipo_desconocido"
    assert await _contar(sesion, "sync_cuarentena") == 1


async def test_reenviar_un_sobre_rechazado_no_lo_reintenta(cliente, semilla, sesion):
    """Se responde lo mismo que la primera vez. Reintentar a ciegas un sobre
    que ya falló solo llenaría la cuarentena de copias."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    op = operacion_cliente()
    op.datos.pop("nombre_comercial")
    cuerpo = a_json([sobre(op)])

    primera = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    segunda = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)

    assert primera.json()["rechazadas"] == 1
    assert segunda.json()["rechazadas"] == 1
    assert segunda.json()["resultados"][0]["error_codigo"] == "payload_invalido"
    assert await _contar(sesion, "sync_cuarentena") == 1, "se duplicó la cuarentena"


# ---------------------------------------------------------------------------
# Reglas de negocio en el ingreso
# ---------------------------------------------------------------------------

async def test_un_cliente_de_campo_nunca_llega_con_credito(cliente, semilla, sesion):
    """Aunque el payload lo pida: aceptarlo dejaría que el vendedor se
    autorizara su propia cartera."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    op = operacion_cliente("Con crédito", limite_credito="50000.00", permite_credito=True)

    r = await cliente.post("/v1/sync/push", json=a_json([sobre(op)]), headers=cab)
    assert r.json()["aceptadas"] == 1

    fila = (
        await sesion.execute(
            text("SELECT permite_credito, limite_credito, origen_alta, estatus FROM clientes")
        )
    ).mappings().one()
    assert fila["permite_credito"] is False
    assert fila["limite_credito"] == 0
    assert fila["origen_alta"] == "campo"
    assert fila["estatus"] == "prospecto"


async def test_no_se_puede_escribir_en_la_ruta_de_otro(cliente, semilla, sesion):
    otra_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas(id, codigo, nombre) VALUES (:r,'R09','Ruta 9')"), {"r": otra_ruta}
    )
    await sesion.commit()
    cab = await _cab_vendedor(cliente, sesion, semilla)

    op = operacion_cliente("De ruta ajena", ruta_id=str(otra_ruta))
    r = await cliente.post("/v1/sync/push", json=a_json([sobre(op)]), headers=cab)
    assert r.json()["rechazadas"] == 1
    assert r.json()["resultados"][0]["error_codigo"] == "conflicto_de_datos"


async def test_un_numero_json_donde_va_un_string_se_rechaza(cliente, semilla, sesion):
    """El contrato manda dinero y coordenadas como string. Convertir el número
    en silencio aceptaría justo el float que el contrato prohíbe."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    # Se arma un sobre válido y después se mete el float en el JSON: el propio
    # ayudante no podría calcular el hash de un payload con float, que es la
    # señal de que el dispositivo tampoco habría podido mandarlo bien.
    valido = sobre(operacion_cliente("Con lat numérica", lng="-99.1332"))
    cuerpo = a_json([valido])
    cuerpo["sobres"][0]["operaciones"][0]["datos"]["lat"] = 19.4326

    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.json()["rechazadas"] == 1
    assert r.json()["resultados"][0]["error_codigo"] == "hash_no_coincide"


# ---------------------------------------------------------------------------
# Transporte
# ---------------------------------------------------------------------------

async def test_un_lote_mal_formado_no_procesa_nada(cliente, semilla, sesion):
    """Si el contenedor está roto no se puede confiar en nada de lo que venga
    dentro."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    uno = sobre(secuencia=1)
    cuerpo = a_json([uno, uno])      # operacion_id repetido

    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 422
    assert await _contar(sesion, "clientes") == 0


async def test_sin_dispositivo_no_se_sincroniza(cliente, semilla):
    """Gerencia entra sin equipo registrado, y no debe poder empujar cola."""
    r = await cliente.post(
        "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
    )
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    empuje = await cliente.post("/v1/sync/push", json=a_json([sobre()]), headers=cab)
    assert empuje.status_code == 403


async def test_el_lote_queda_registrado_con_su_desglose(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    lote_id = uuid.uuid4()
    malo = operacion_cliente()
    malo.datos.pop("nombre_comercial")
    cuerpo = a_json([sobre(secuencia=1), sobre(malo, secuencia=2)], lote_id=lote_id)

    await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)

    fila = (
        await sesion.execute(
            text("SELECT total_operaciones, aceptadas, rechazadas, procesado_en "
                 "FROM sync_lotes WHERE id = :id"),
            {"id": lote_id},
        )
    ).mappings().one()
    assert fila["total_operaciones"] == 2
    assert fila["aceptadas"] == 1
    assert fila["rechazadas"] == 1
    assert fila["procesado_en"] is not None
