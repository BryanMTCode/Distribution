"""Pruebas de caos del motor de sincronización.

No prueban el camino feliz: reproducen lo que una red mala hace de verdad
—entregar dos veces, entregar a medias, entregar fuera de orden— y verifican
que las invariantes se sostengan en todos los casos.

Los escenarios se generan con una semilla fija, así que un fallo es siempre
reproducible: el número de semilla aparece en el nombre del caso.

**El escenario que casi nadie prueba** es `test_restaurar_un_respaldo_viejo`:
reinstalar la app o restaurar un respaldo del teléfono reproduce el outbox
completo desde el principio. Es la fuente de duplicados más realista que
existe en una operación DSD, y es exactamente para lo que existen las llaves
de idempotencia.
"""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from tests.ayudas_sync import a_json, operacion_cliente, sobre
from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio

SEMILLAS = [1, 7, 13, 42, 99, 2026]


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


def _generar_cola(azar: random.Random, cuantos: int) -> tuple[list, set, set]:
    """Simula la cola de un día de ruta.

    Devuelve los sobres, los ids de entidad que DEBEN existir al final, y los
    ids de sobre que deben acabar rechazados.
    """
    sobres, esperadas, rechazables = [], set(), set()
    for i in range(cuantos):
        operaciones = [operacion_cliente(f"Tienda {i}-{j}") for j in range(azar.randint(1, 3))]
        # Uno de cada seis sobres viene roto: capturas a medias, campos vacíos.
        roto = azar.random() < 1 / 6
        if roto:
            operaciones[-1].datos.pop("nombre_comercial")
        s = sobre(*operaciones, secuencia=i, visita_id=uuid.uuid4())
        sobres.append(s)
        if roto:
            rechazables.add(s.operacion_id)
        else:
            esperadas |= {o.entidad_id for o in operaciones}
    return sobres, esperadas, rechazables


def _programar_entregas(azar: random.Random, sobres: list) -> list[list]:
    """Arma las tandas tal como las mandaría un teléfono con mala señal:
    partidas en trozos, algunas repetidas, y alguna reordenada."""
    tandas, i = [], 0
    while i < len(sobres):
        corte = azar.randint(1, 4)
        tanda = sobres[i : i + corte]
        tandas.append(tanda)
        if azar.random() < 0.4:
            tandas.append(list(tanda))              # el reintento del teléfono
        if azar.random() < 0.25:
            revuelta = list(tanda)
            azar.shuffle(revuelta)
            tandas.append(revuelta)                 # el mismo lote, otro orden
        i += corte
    if azar.random() < 0.5:
        azar.shuffle(tandas)                        # tandas fuera de orden
    return tandas


async def _contar(sesion, tabla: str) -> int:
    return (await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))).scalar_one()


@pytest.mark.parametrize("semilla_azar", SEMILLAS)
async def test_caos_de_entrega(cliente, semilla, sesion, semilla_azar):
    """Entrega duplicada, parcial y fuera de orden, todo a la vez."""
    azar = random.Random(semilla_azar)
    cab = await _cab_vendedor(cliente, sesion, semilla)
    sobres, esperadas, rechazables = _generar_cola(azar, azar.randint(6, 14))

    for tanda in _programar_entregas(azar, sobres):
        r = await cliente.post(
            "/v1/sync/push", json=a_json(tanda, lote_id=uuid.uuid4()), headers=cab
        )
        assert r.status_code == 200, r.text

    # ---- Invariante 1: ni un cliente de más, ni uno de menos ----
    creados = set(
        (await sesion.execute(text("SELECT id FROM clientes"))).scalars()
    )
    assert creados == esperadas

    # ---- Invariante 2: cada sobre resolvió exactamente una vez ----
    procesados = await _contar(sesion, "sync_operaciones")
    assert procesados == len(sobres)

    # ---- Invariante 3: la cuarentena no acumula copias ----
    en_cuarentena = set(
        (
            await sesion.execute(text("SELECT DISTINCT operacion_id FROM sync_cuarentena"))
        ).scalars()
    )
    assert en_cuarentena == rechazables
    assert await _contar(sesion, "sync_cuarentena") == len(rechazables)

    # ---- Invariante 4: ningún sobre quedó aceptado y rechazado a la vez ----
    aceptados = set(
        (
            await sesion.execute(
                text("SELECT operacion_id FROM sync_operaciones WHERE resultado = 'aceptada'")
            )
        ).scalars()
    )
    assert not (aceptados & rechazables)


@pytest.mark.parametrize("semilla_azar", SEMILLAS[:3])
async def test_restaurar_un_respaldo_viejo(cliente, semilla, sesion, semilla_azar):
    """El vendedor reinstaló la app o restauró un respaldo del teléfono.

    El outbox completo se reproduce desde el principio, como si nada se hubiera
    sincronizado nunca. Es la fuente de duplicados más realista de una
    operación DSD, y la que casi nadie prueba.
    """
    azar = random.Random(semilla_azar)
    cab = await _cab_vendedor(cliente, sesion, semilla)
    sobres, esperadas, _ = _generar_cola(azar, 10)

    # Día normal: se sincroniza todo.
    for i in range(0, len(sobres), 3):
        await cliente.post(
            "/v1/sync/push", json=a_json(sobres[i : i + 3], lote_id=uuid.uuid4()), headers=cab
        )
    antes = await _contar(sesion, "clientes")
    assert antes == len(esperadas)

    # El teléfono se restaura y reenvía la cola entera, con lotes nuevos.
    reenviados = 0
    for i in range(0, len(sobres), 5):
        r = await cliente.post(
            "/v1/sync/push", json=a_json(sobres[i : i + 5], lote_id=uuid.uuid4()), headers=cab
        )
        reenviados += r.json()["aceptadas"]

    assert reenviados == 0, "la restauración aplicó operaciones de nuevo"
    assert await _contar(sesion, "clientes") == antes


async def test_dos_dispositivos_sincronizan_en_paralelo(cliente, semilla, sesion):
    """Dos vendedores empujando a la vez no se estorban ni se mezclan.

    El advisory lock serializa los lotes de un MISMO equipo, no los de equipos
    distintos: si bloqueara a todos, la sincronización de la mañana sería una
    fila india.
    """
    import asyncio

    otro_vendedor = uuid.uuid4()
    otra_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO usuarios(id, codigo, nombre, password_hash, rol_codigo, "
             "creado_en, actualizado_en) "
             "SELECT :id,'VEND02','Pedro', password_hash,'vendedor', now(), now() "
             "  FROM usuarios WHERE codigo='VEND01'"),
        {"id": otro_vendedor},
    )
    await sesion.execute(
        text("INSERT INTO rutas(id, codigo, nombre, vendedor_id) VALUES (:r,'R09','Ruta 9',:v)"),
        {"r": otra_ruta, "v": otro_vendedor},
    )
    await sesion.execute(
        text("INSERT INTO usuarios_rutas(usuario_id, ruta_id) VALUES (:v,:r)"),
        {"v": otro_vendedor, "r": otra_ruta},
    )
    dispositivo2 = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54 de Pedro','activo',now())"),
        {"d": dispositivo2, "u": otro_vendedor},
    )
    await sesion.commit()

    cab1 = await _cab_vendedor(cliente, sesion, semilla)
    r2 = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND02", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo2),
    })
    cab2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

    lote1 = a_json([sobre(secuencia=i) for i in range(5)], lote_id=uuid.uuid4())
    lote2 = a_json([sobre(secuencia=i) for i in range(5)], lote_id=uuid.uuid4())

    a, b = await asyncio.gather(
        cliente.post("/v1/sync/push", json=lote1, headers=cab1),
        cliente.post("/v1/sync/push", json=lote2, headers=cab2),
    )
    assert a.json()["aceptadas"] == 5
    assert b.json()["aceptadas"] == 5
    assert await _contar(sesion, "clientes") == 10

    # Cada cliente quedó en la ruta de su vendedor, no mezclados.
    por_ruta = dict(
        (
            await sesion.execute(text("SELECT ruta_id, count(*) FROM clientes GROUP BY ruta_id"))
        ).all()
    )
    assert por_ruta == {semilla["ruta"]: 5, otra_ruta: 5}


async def test_el_mismo_lote_enviado_dos_veces_en_paralelo(cliente, semilla, sesion):
    """El teléfono reintentó mientras el envío original seguía procesándose.

    Sin el advisory lock, los dos pasarían a la vez por el '¿ya procesado?' y
    ambos aplicarían: duplicados exactos, del tipo más difícil de detectar
    después.
    """
    import asyncio

    cab = await _cab_vendedor(cliente, sesion, semilla)
    cuerpo = a_json([sobre(secuencia=i) for i in range(8)])

    respuestas = await asyncio.gather(
        cliente.post("/v1/sync/push", json=cuerpo, headers=cab),
        cliente.post("/v1/sync/push", json=cuerpo, headers=cab),
    )
    aceptadas = sum(r.json()["aceptadas"] for r in respuestas)
    duplicadas = sum(r.json()["duplicadas"] for r in respuestas)

    assert aceptadas == 8, f"se aplicó de más: {aceptadas}"
    assert duplicadas == 8
    assert await _contar(sesion, "clientes") == 8


async def test_una_cola_de_una_semana_sin_senal(cliente, semilla, sesion):
    """Tope de sobres por lote: mandar miles de golpe por 3G en un mercado es
    la forma más segura de que el lote nunca complete. Se manda por tandas."""
    from app.domain.sync.sobres import MAX_SOBRES_POR_LOTE

    cab = await _cab_vendedor(cliente, sesion, semilla)
    acumulados = [sobre(secuencia=i) for i in range(MAX_SOBRES_POR_LOTE + 50)]

    excesivo = await cliente.post(
        "/v1/sync/push", json=a_json(acumulados), headers=cab
    )
    assert excesivo.status_code == 422
    assert await _contar(sesion, "clientes") == 0

    total = 0
    for i in range(0, len(acumulados), MAX_SOBRES_POR_LOTE):
        r = await cliente.post(
            "/v1/sync/push",
            json=a_json(acumulados[i : i + MAX_SOBRES_POR_LOTE], lote_id=uuid.uuid4()),
            headers=cab,
        )
        assert r.status_code == 200
        total += r.json()["aceptadas"]

    assert total == len(acumulados)
    assert await _contar(sesion, "clientes") == len(acumulados)
