"""La profundidad de cola que el teléfono reporta en cada push.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTE DATO MERECE SU PROPIO ARCHIVO
────────────────────────────────────────────────────────────────────────────
Es el único dato del sistema que **solo el teléfono puede dar**. El servidor ve
lo que ya llegó; cuántas operaciones siguen en la bandeja de salida del equipo es
información que vive del otro lado de la red.

Y de él depende el cierre del día. Hasta ahora `liquidaciones.sync_completa` se
escribía en `true` porque una persona marcaba una casilla, y al auditar un cierre
con sobrante ese `true` parecía decir "el equipo estaba al día" cuando en realidad
decía "alguien dijo que sí".

Tres cosas que se rompen, y las tres en silencio:

1. **Que NULL y 0 se confundan.** `NULL` es "nunca lo reportó" —app vieja— y `0` es
   "dijo que no le queda nada". Si un DEFAULT 0 los igualara, cada equipo sin
   actualizar parecería estar al día desde el primer día.
2. **Que un push sin el campo borre lo último reportado.** El número lo lee el
   cierre del día; perderlo porque un lote vino de una versión vieja dejaría la
   liquidación sin respaldo sin que nada lo avise.
3. **Que el campo nuevo rompa el push.** Es opcional a propósito: un equipo con app
   vieja tiene que seguir sincronizando igual, porque si no, la primera
   consecuencia de este cambio sería un vendedor que no puede subir sus ventas.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.ayudas_sync import a_json, operacion_cliente, sobre
from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _equipo(cliente, sesion, semilla) -> tuple[uuid.UUID, dict]:
    """Un dispositivo registrado y su cabecera de autorización."""
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d,:u,'Moto G54 de Juan','activo',now())"
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
    return dispositivo_id, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _cola(sesion, dispositivo_id) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT cola_pendiente, cola_reportada_en "
                    "  FROM dispositivos WHERE id = :d"
                ),
                {"d": dispositivo_id},
            )
        ).mappings().one()
    )


async def _empujar(cliente, cab, cuerpo) -> dict:
    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Se guarda, con su hora
# ---------------------------------------------------------------------------


async def test_el_numero_reportado_se_guarda(cliente, semilla, sesion):
    equipo, cab = await _equipo(cliente, sesion, semilla)
    cuerpo = a_json([sobre(operacion_cliente())])
    cuerpo["cola_pendiente"] = 7

    await _empujar(cliente, cab, cuerpo)

    fila = await _cola(sesion, equipo)
    assert fila["cola_pendiente"] == 7
    # Sin la hora, el número no sirve: un cero de anteayer no dice nada sobre hoy
    # (§0.3). El cierre del día compara esa fecha contra la de la carga.
    assert fila["cola_reportada_en"] is not None


async def test_cero_reportado_es_distinto_de_nunca_reportado(cliente, semilla, sesion):
    """`NULL` y `0` tienen que poder distinguirse, y de eso depende el cierre.

    `NULL` = app vieja o equipo que nunca sincronizó. `0` = el equipo dijo que ya no
    le queda nada. Si se confundieran, cada equipo sin actualizar parecería estar al
    día y la liquidación cerraría creyendo tener un respaldo que no tiene.
    """
    equipo, cab = await _equipo(cliente, sesion, semilla)
    assert (await _cola(sesion, equipo))["cola_pendiente"] is None

    cuerpo = a_json([sobre(operacion_cliente())])
    cuerpo["cola_pendiente"] = 0
    await _empujar(cliente, cab, cuerpo)

    fila = await _cola(sesion, equipo)
    assert fila["cola_pendiente"] == 0
    assert fila["cola_pendiente"] is not None


async def test_un_push_sin_el_campo_no_borra_lo_ya_reportado(cliente, semilla, sesion):
    """Un equipo con app vieja no puede dejar ciega a la liquidación.

    El número lo lee el cierre del día. Si un lote sin el campo lo pusiera en NULL,
    el respaldo desaparecería y nada lo avisaría: la pantalla simplemente volvería a
    pedir la casilla sin explicar por qué.
    """
    equipo, cab = await _equipo(cliente, sesion, semilla)
    primero = a_json([sobre(operacion_cliente("Tienda uno"))])
    primero["cola_pendiente"] = 3
    await _empujar(cliente, cab, primero)
    antes = await _cola(sesion, equipo)

    # El segundo lote no lo trae: versión vieja de la app, o un cliente distinto.
    await _empujar(cliente, cab, a_json([sobre(operacion_cliente("Tienda dos"))]))

    despues = await _cola(sesion, equipo)
    assert despues["cola_pendiente"] == 3
    assert despues["cola_reportada_en"] == antes["cola_reportada_en"]


async def test_un_push_sin_el_campo_sigue_funcionando(cliente, semilla, sesion):
    """Lo primero que no puede pasar es que un equipo deje de poder subir ventas."""
    _, cab = await _equipo(cliente, sesion, semilla)
    cuerpo = a_json([sobre(operacion_cliente())])

    respuesta = await _empujar(cliente, cab, cuerpo)
    assert respuesta["aceptadas"] == 1
    assert (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one() == 1


async def test_el_numero_baja_conforme_se_vacia_la_cola(cliente, semilla, sesion):
    """Dos lotes seguidos: el segundo pisa al primero, no se suma."""
    equipo, cab = await _equipo(cliente, sesion, semilla)

    for quedan, nombre in ((2, "Tienda uno"), (0, "Tienda dos")):
        cuerpo = a_json([sobre(operacion_cliente(nombre))])
        cuerpo["cola_pendiente"] = quedan
        await _empujar(cliente, cab, cuerpo)

    assert (await _cola(sesion, equipo))["cola_pendiente"] == 0


# ---------------------------------------------------------------------------
# Lo que no se acepta
# ---------------------------------------------------------------------------


async def test_un_numero_negativo_se_rechaza(cliente, semilla, sesion):
    """Una cola negativa no significa nada, y guardarla haría que el bloqueo del
    cierre —que compara contra cero— dejara de dispararse."""
    _, cab = await _equipo(cliente, sesion, semilla)
    cuerpo = a_json([sobre(operacion_cliente())])
    cuerpo["cola_pendiente"] = -1

    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 422


async def test_un_numero_entre_comillas_se_rechaza(cliente, semilla, sesion):
    """`contracts/README.md` §1.4: los conteos van como entero de JSON.

    Sin `strict=True` Pydantic acepta "3" y lo convierte, y entonces el contrato se
    vuelve una sugerencia justo en el campo del que depende el cierre del día. Lo
    mismo con `true`, que en Python pasaría como 1 porque `bool` es subclase de
    `int`: una cola de «sí» valdría una operación pendiente.
    """
    _, cab = await _equipo(cliente, sesion, semilla)

    for invalido in ("3", True, 3.0):
        cuerpo = a_json([sobre(operacion_cliente())])
        cuerpo["cola_pendiente"] = invalido
        r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
        assert r.status_code == 422, f"aceptó {invalido!r}"


async def test_el_equipo_no_puede_declarar_de_quien_es_la_cola(
    cliente, semilla, sesion
):
    """El dispositivo sale del TOKEN, y el cuerpo no tiene dónde decir otra cosa.

    Si pudiera declararlo, un equipo podría poner en cero la cola de otro y
    desbloquear la liquidación de un vendedor que todavía tiene ventas sin subir.
    El lote completo se rechaza con 422 en vez de ignorar el campo: con
    `extra="forbid"`, quien mande algo que la API no admite se entera, en vez de
    creer que surtió efecto.
    """
    equipo, cab = await _equipo(cliente, sesion, semilla)
    cuerpo = a_json([sobre(operacion_cliente())])
    cuerpo["cola_pendiente"] = 0
    cuerpo["dispositivo_id"] = str(uuid.uuid4())

    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 422

    # Y nada se guardó: el lote no se procesó en absoluto.
    assert (await _cola(sesion, equipo))["cola_pendiente"] is None
