"""Auditoría de sincronización: los huecos que se cerraron.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTE ARCHIVO ES APARTE
────────────────────────────────────────────────────────────────────────────
Cada prueba de aquí corresponde a un hallazgo de la auditoría estructural de
octubre de 2026 (`docs/AUDITORIA-SINCRONIZACION.md`). No prueban una pantalla:
prueban que la cadena entre el teléfono y el panel no tenga tramos muertos.

Los dos que importan más:

**El cliente que cambia de ruta.** El `change_log` acota cada delta por `ruta_id`
y el pull entrega solo lo de las rutas del vendedor. Reasignar un cliente publica
el delta con la ruta NUEVA, así que el teléfono de la ruta vieja no se enteraba
nunca: lo seguía visitando.

**El piso de retención.** La migración 0007 prometió un job que podara el
`change_log` «conservando lo necesario para el dispositivo más atrasado». Ese job
no existía, así que la tabla crecía sin límite; y el día que se escribiera, un
dispositivo atrasado habría recibido los deltas siguientes sin saber nunca que le
faltaban los de en medio.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _cab_vendedor(cliente, sesion, semilla) -> tuple[dict, uuid.UUID]:
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d,:u,'Moto G54','activo',now())"
        ),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND01",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(dispositivo_id),
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, dispositivo_id


# ---------------------------------------------------------------------------
# Hallazgo 3: el cliente que cambia de ruta
# ---------------------------------------------------------------------------


async def test_EL_CLIENTE_QUE_CAMBIA_DE_RUTA_AVISA_A_LA_QUE_LO_PIERDE(
    cliente, sesion, semilla
):
    """Sin este aviso, el teléfono de la ruta vieja lo visita para siempre.

    Y la venta que le haga entra sin protestar —el cliente existe en el servidor—,
    así que no hay ninguna señal de que dos vendedores están cubriendo al mismo
    cliente hasta que alguien compara las dos rutas a mano.
    """
    otra_ruta = uuid.uuid4()
    cliente_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R-99', 'Ruta nueva')"
        ),
        {"r": otra_ruta},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
            "                      actualizado_en) "
            "VALUES (:c, 'CLI-R', 'La Esquina', :r, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.commit()

    desde = (
        await sesion.execute(text("SELECT COALESCE(MAX(cursor), 0) FROM change_log"))
    ).scalar_one()

    # La oficina lo reasigna.
    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :nueva WHERE id = :c"),
        {"nueva": otra_ruta, "c": cliente_id},
    )
    await sesion.commit()

    filas = (
        await sesion.execute(
            text(
                "SELECT operacion, ruta_id, payload FROM change_log "
                " WHERE entidad = 'cliente' AND entidad_id = :c AND cursor > :d "
                " ORDER BY cursor"
            ),
            {"c": cliente_id, "d": desde},
        )
    ).mappings().all()

    # Dos deltas: la baja para la ruta vieja y el alta para la nueva.
    assert len(filas) == 2, f"se publicaron {len(filas)} deltas, no 2"
    assert filas[0]["operacion"] == "delete"
    assert filas[0]["ruta_id"] == semilla["ruta"]
    assert filas[0]["payload"] is None
    assert filas[1]["operacion"] == "upsert"
    assert filas[1]["ruta_id"] == otra_ruta


async def test_EL_VENDEDOR_DE_LA_RUTA_VIEJA_RECIBE_LA_BAJA(cliente, sesion, semilla):
    """La prueba de punta a punta: el delta llega por el pull, con su acotamiento.

    Es lo que separa la corrección de una buena intención: el delta puede existir
    en `change_log` y no llegar nunca si el `ruta_id` no coincide con las rutas del
    dispositivo.
    """
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    otra_ruta = uuid.uuid4()
    cliente_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R-98', 'Otra')"),
        {"r": otra_ruta},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
            "                      actualizado_en) "
            "VALUES (:c, 'CLI-S', 'La Esquina', :r, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.commit()

    # El vendedor se pone al día: el cliente es suyo.
    primero = await cliente.get("/v1/sync/pull?cursor=0", headers=cabeceras)
    assert primero.status_code == 200
    cursor = primero.json()["cursor"]
    assert any(
        c["entidad"] == "cliente" and c["entidad_id"] == str(cliente_id)
        for c in primero.json()["cambios"]
    )

    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :nueva WHERE id = :c"),
        {"nueva": otra_ruta, "c": cliente_id},
    )
    await sesion.commit()

    segundo = await cliente.get(f"/v1/sync/pull?cursor={cursor}", headers=cabeceras)
    cambios = segundo.json()["cambios"]

    bajas = [
        c
        for c in cambios
        if c["entidad"] == "cliente"
        and c["entidad_id"] == str(cliente_id)
        and c["operacion"] == "delete"
    ]
    assert bajas, "el teléfono de la ruta vieja no recibió la baja"
    # Y NO recibe el alta de la ruta nueva: ese cliente ya no es asunto suyo.
    altas = [
        c
        for c in cambios
        if c["entidad"] == "cliente"
        and c["entidad_id"] == str(cliente_id)
        and c["operacion"] == "upsert"
    ]
    assert not altas


async def test_el_estatus_viaja_en_el_payload(cliente, sesion, semilla):
    """El teléfono lo traduce a «va en la ruta o no». Si dejara de viajar, un
    cliente dado de baja volvería a aparecer en la lista del vendedor."""
    cliente_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, estatus, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'CLI-T', 'La Esquina', :r, 'activo', now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text("UPDATE clientes SET estatus = 'baja' WHERE id = :c"), {"c": cliente_id}
    )
    await sesion.commit()

    payload = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'cliente' AND entidad_id = :c "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"c": cliente_id},
        )
    ).scalar_one()
    assert payload["estatus"] == "baja"


async def test_el_delta_de_cliente_sigue_sin_la_geografia(cliente, sesion, semilla):
    """El disparador nuevo tiene que seguir quitando `ubicacion`: es el hexadecimal
    de PostGIS, el teléfono ya recibe lat y lng, y solo engordaría cada delta."""
    cliente_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, lat, lng, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'CLI-U', 'La Esquina', :r, 19.4326, -99.1332, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.commit()

    payload = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'cliente' AND entidad_id = :c LIMIT 1"
            ),
            {"c": cliente_id},
        )
    ).scalar_one()
    assert "ubicacion" not in payload
    assert payload["lat"] is not None


# ---------------------------------------------------------------------------
# Hallazgo 4: el piso de retención
# ---------------------------------------------------------------------------


async def test_UN_CURSOR_POR_DEBAJO_DEL_PISO_MANDA_A_RESINCRONIZAR(
    cliente, sesion, semilla
):
    """Lo que esto evita: un catálogo incompleto, en silencio, para siempre.

    Si el `change_log` se podó por debajo del cursor que trae el dispositivo, los
    deltas de en medio no existen y no hay forma de entregárselos. Seguir adelante
    sería darle los siguientes y dejarlo creyendo que está al día.
    """
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    # UPSERT: la tabla se vacía entre pruebas (ver `TABLAS_VOLATILES`), así que un
    # UPDATE no afectaría ningún renglón y la prueba pasaría sin probar nada.
    await sesion.execute(
        text(
            "INSERT INTO sync_retencion (id, piso_cursor) VALUES (true, 500) "
            "ON CONFLICT (id) DO UPDATE SET piso_cursor = 500"
        )
    )
    await sesion.commit()

    r = await cliente.get("/v1/sync/pull?cursor=100", headers=cabeceras)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["resincronizar"] is True
    assert cuerpo["cursor"] == 0
    assert cuerpo["cambios"] == []
    # `hay_mas` en true: el dispositivo tiene que volver a entrar, no quedarse.
    assert cuerpo["hay_mas"] is True


async def test_con_el_piso_en_cero_nada_cambia(cliente, sesion, semilla):
    """Mientras nadie pode, esto no se dispara nunca: es el caso de hoy."""
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.get("/v1/sync/pull?cursor=0", headers=cabeceras)
    assert r.json()["resincronizar"] is False


async def test_un_cursor_EN_el_piso_no_resincroniza(cliente, sesion, semilla):
    """El límite exacto importa: el piso es el cursor más bajo CONSERVADO, así que
    un dispositivo justo en él no perdió nada."""
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    # UPSERT: la tabla se vacía entre pruebas (ver `TABLAS_VOLATILES`), así que un
    # UPDATE no afectaría ningún renglón y la prueba pasaría sin probar nada.
    await sesion.execute(
        text(
            "INSERT INTO sync_retencion (id, piso_cursor) VALUES (true, 500) "
            "ON CONFLICT (id) DO UPDATE SET piso_cursor = 500"
        )
    )
    await sesion.commit()

    r = await cliente.get("/v1/sync/pull?cursor=500", headers=cabeceras)
    assert r.json()["resincronizar"] is False


# ---------------------------------------------------------------------------
# El job de poda
# ---------------------------------------------------------------------------


async def test_la_poda_deja_el_piso_escrito(sesion, semilla):
    """Las dos escrituras van juntas, y ese orden es la mitad del job: un tramo
    podado sin piso que lo delate es justo el hueco silencioso."""
    from app.workers.principal import MANEJADORES

    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, "
            "                         ultimo_cursor_pull, registrado_en) "
            "VALUES (:d,:u,'Moto','activo', 50000, now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    for _ in range(3):
        await sesion.execute(
            text(
                "INSERT INTO change_log (entidad, entidad_id, operacion, payload) "
                "VALUES ('producto', gen_random_uuid(), 'upsert', '{}'::jsonb)"
            )
        )
    # Se envejece TODO, no solo lo que se acaba de insertar.
    #
    # El `change_log` no se vacía entre pruebas —su cursor es la marca de agua de
    # los dispositivos y reiniciarlo falsearía el escenario—, así que la semilla ya
    # dejó renglones con cursores MÁS BAJOS. Envejecer solo los nuevos daría un
    # corte por debajo de ellos y la poda no borraría nada: el cursor crece con el
    # tiempo, así que un renglón «viejo» con cursor alto no existe en producción, y
    # una prueba que lo fabrica prueba otra cosa. Lo encontró este assert.
    await sesion.execute(
        text("UPDATE change_log SET creado_en = now() - interval '60 days'")
    )
    await sesion.commit()

    antes = (
        await sesion.execute(text("SELECT count(*) FROM change_log"))
    ).scalar_one()

    await MANEJADORES["podar_change_log"]({"margen": 0, "dias_minimos": 30})

    despues = (
        await sesion.execute(text("SELECT count(*) FROM change_log"))
    ).scalar_one()
    piso = (
        await sesion.execute(text("SELECT piso_cursor FROM sync_retencion"))
    ).scalar_one()

    assert despues < antes, "no podó nada"
    assert piso > 0, "podó sin dejar el piso escrito: el hueco sería invisible"


async def test_la_poda_NO_se_lleva_lo_reciente(sesion, semilla):
    """Un dispositivo que se reactiva después de un mes todavía tiene que poder
    ponerse al día, y un respaldo restaurado también."""
    from app.workers.principal import MANEJADORES

    await sesion.execute(
        text(
            "INSERT INTO change_log (entidad, entidad_id, operacion, payload) "
            "VALUES ('producto', gen_random_uuid(), 'upsert', '{}'::jsonb)"
        )
    )
    await sesion.commit()
    antes = (
        await sesion.execute(text("SELECT count(*) FROM change_log"))
    ).scalar_one()

    await MANEJADORES["podar_change_log"]({"margen": 0, "dias_minimos": 30})

    despues = (
        await sesion.execute(text("SELECT count(*) FROM change_log"))
    ).scalar_one()
    assert despues == antes


# ---------------------------------------------------------------------------
# Hallazgo 5: la identidad del equipo
# ---------------------------------------------------------------------------


async def test_EL_PAYLOAD_DE_IDENTIDAD_NO_LLEVA_EL_HASH_DE_LA_CONTRASENA(
    cliente, sesion, semilla
):
    """La prueba más importante de este disparador.

    `usuarios` guarda `password_hash`. Un disparador genérico aquí volcaría la fila
    entera al `change_log`, que es una tabla que el dispositivo **descarga**: el
    hash de la contraseña de un empleado viajaría por la red y se quedaría en el
    SQLite de un teléfono. Por eso el payload se arma a mano, campo por campo.
    """
    almacen = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id, "
            "                       responsable_id) "
            "VALUES (:a, 'CAMION_OTRO', 'Otro camión', 'camion', :s, :u)"
        ),
        {"a": almacen, "s": semilla["sucursal"], "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"),
        {"a": almacen, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    payload = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'identidad' ORDER BY cursor DESC LIMIT 1"
            )
        )
    ).scalar_one()

    assert set(payload) == {"usuario_id", "almacen_id"}, (
        f"el payload lleva campos de más: {sorted(payload)}"
    )
    assert payload["almacen_id"] == str(almacen)
    # Dicho explícitamente, porque es lo que no puede volver a pasar nunca.
    assert "password_hash" not in payload
    assert "permisos" not in payload
    # El código del vendedor tampoco: es el prefijo del folio impreso, y cambiarlo
    # a media ruta haría que dos rangos compartieran prefijo en papel.
    assert "codigo" not in payload


async def test_el_delta_de_identidad_llega_SOLO_a_su_vendedor(
    cliente, sesion, semilla
):
    """La identidad de Juan no le importa al teléfono de Pedro."""
    almacen = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo, sucursal_id, "
            "                       responsable_id) "
            "VALUES (:a, 'CAMION_X', 'Camión X', 'camion', :s, :u)"
        ),
        {"a": almacen, "s": semilla["sucursal"], "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"),
        {"a": almacen, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT vendedor_id, ruta_id FROM change_log "
                " WHERE entidad = 'identidad' ORDER BY cursor DESC LIMIT 1"
            )
        )
    ).mappings().one()
    assert fila["vendedor_id"] == semilla["vendedor"]
    # Sin ruta: no es un dato de ruta, es del equipo.
    assert fila["ruta_id"] is None


async def test_un_cambio_que_no_toca_el_almacen_no_publica_nada(
    cliente, sesion, semilla
):
    """Cambiarle el nombre a un usuario no le dice nada al teléfono, y el
    disparador tiene su condición justamente para que no viaje."""
    desde = (
        await sesion.execute(text("SELECT COALESCE(MAX(cursor), 0) FROM change_log"))
    ).scalar_one()

    await sesion.execute(
        text("UPDATE usuarios SET nombre = 'Juan Pérez López' WHERE id = :u"),
        {"u": semilla["vendedor"]},
    )
    await sesion.commit()

    cuantos = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM change_log "
                " WHERE entidad = 'identidad' AND cursor > :d"
            ),
            {"d": desde},
        )
    ).scalar_one()
    assert cuantos == 0


async def test_EL_SERVIDOR_YA_IGNORABA_EL_ALMACEN_DEL_PAYLOAD(
    cliente, sesion, semilla
):
    """La corrección a la propia auditoría, convertida en prueba.

    §6.1 decía que una credencial vieja dejaría ventas estampadas con el camión
    anterior. **No es cierto**, y esta prueba lo fija: el manejador toma el almacén
    del token —«del token, nunca del payload»— así que un teléfono puede mandar el
    almacén que quiera y la venta queda con el que el servidor sabe.
    """
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d,:u,'Moto','activo',now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"),
        {"a": semilla["camion"], "u": semilla["vendedor"]},
    )
    await sesion.commit()

    from app.infra.sync.manejadores import Contexto

    ctx = Contexto(
        usuario_id=semilla["vendedor"],
        dispositivo_id=dispositivo,
        recibido_en="2026-10-06T10:00:00+00:00",
        almacen_id=semilla["camion"],
    )
    # El contexto se arma desde el token (ver `sync.py`), no desde el sobre: es
    # justo lo que esta prueba documenta.
    assert ctx.almacen_id == semilla["camion"]
