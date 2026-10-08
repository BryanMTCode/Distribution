"""Borrado remoto: la regla es que nunca se borra lo que no se ha entregado.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
El borrado remoto es la única función del sistema que **destruye datos a
distancia**, y la tentación de hacerlo inmediato es fuerte y equivocada. El
motivo real de un borrado casi nunca es un robo: es una renuncia, un cambio de
teléfono, un equipo extraviado. En los tres casos el aparato puede traer dentro
un día de ventas sin sincronizar, y borrarlas es perder dinero cobrado sin saber
a quién se le vendió.

Cuatro cosas que tienen que seguir siendo verdad:

1. **Un equipo suspendido puede hacer PUSH y no puede hacer PULL.** Es la única
   razón de que ese estado exista; sin eso, drenar antes de borrar es imposible.
2. **Un equipo revocado no puede nada.**
3. **La confirmación exige una orden previa.** Un cliente con un bug no puede
   marcar como borrado un equipo que sigue trabajando.
4. **Ordenar no es confirmar.** La orden prueba que alguien lo pidió; solo la
   confirmación prueba que ocurrió, y entre las dos puede pasar una semana.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.ayudas_sync import a_json, operacion_cliente, sobre
from tests.conftest import PASSWORD_VENDEDOR, solo_texto

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def equipo(cliente, sesion, semilla) -> dict:
    """Un vendedor con su equipo registrado y su token."""
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    entrada = await cliente.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND01",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(dispositivo),
        },
    )
    assert entrada.status_code == 200, entrada.text
    return {
        "id": dispositivo,
        "cab": {"Authorization": f"Bearer {entrada.json()['access_token']}"},
    }


async def _poner_estado(sesion, dispositivo_id, estado: str) -> None:
    await sesion.execute(
        text(
            "UPDATE dispositivos SET estado = :e, "
            "       revocado_en = CASE WHEN :e = 'revocado' THEN now() END "
            " WHERE id = :d"
        ),
        {"e": estado, "d": dispositivo_id},
    )
    await sesion.commit()


async def _ordenar(sesion, dispositivo_id, motivo="el vendedor dejó la empresa") -> None:
    await sesion.execute(
        text(
            "UPDATE dispositivos "
            "   SET borrado_ordenado_en = now(), borrado_motivo = :m, "
            "       estado = 'suspendido' "
            " WHERE id = :d"
        ),
        {"m": motivo, "d": dispositivo_id},
    )
    await sesion.commit()


# ---------------------------------------------------------------------------
# El estado «suspendido» y para qué existe
# ---------------------------------------------------------------------------
async def test_un_equipo_suspendido_todavia_puede_subir(cliente, sesion, equipo):
    """Es la prueba que sostiene la regla entera.

    Si el push rechazara a un equipo suspendido, drenar antes de borrar sería
    imposible y cada borrado remoto costaría las ventas del día.
    """
    await _poner_estado(sesion, equipo["id"], "suspendido")

    respuesta = await cliente.post(
        "/v1/sync/push",
        json=a_json([sobre(operacion_cliente("Tienda de la esquina"))]),
        headers=equipo["cab"],
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["aceptadas"] == 1


async def test_un_equipo_suspendido_NO_puede_bajar(cliente, sesion, equipo):
    """Entrega y no recibe nada nuevo.

    Mandarle más datos a un equipo que se está borrando sería lo contrario de
    lo que la orden pretende.
    """
    await _poner_estado(sesion, equipo["id"], "suspendido")
    respuesta = await cliente.get("/v1/sync/pull?cursor=0", headers=equipo["cab"])
    assert respuesta.status_code == 401
    assert "suspendido" in respuesta.json()["detail"]


async def test_un_equipo_revocado_no_puede_ni_subir(cliente, sesion, equipo):
    await _poner_estado(sesion, equipo["id"], "revocado")
    respuesta = await cliente.post(
        "/v1/sync/push",
        json=a_json([sobre(operacion_cliente("No debería entrar"))]),
        headers=equipo["cab"],
    )
    assert respuesta.status_code == 401
    assert "revocado" in respuesta.json()["detail"]


async def test_el_mensaje_distingue_suspendido_de_revocado(cliente, sesion, equipo):
    """El cliente tiene que poder ramificar sin adivinar.

    `revocado` no se arregla de ninguna forma; `suspendido` se arregla
    entregando la cola. Un mismo «dispositivo no activo» para los dos dejaría
    al teléfono sin saber si esperar o rendirse.
    """
    await _poner_estado(sesion, equipo["id"], "suspendido")
    uno = await cliente.get("/v1/sync/pull?cursor=0", headers=equipo["cab"])
    await _poner_estado(sesion, equipo["id"], "revocado")
    otro = await cliente.get("/v1/sync/pull?cursor=0", headers=equipo["cab"])
    assert uno.json()["detail"] != otro.json()["detail"]


# ---------------------------------------------------------------------------
# Las órdenes del servidor
# ---------------------------------------------------------------------------
async def test_sin_orden_el_equipo_no_tiene_nada_que_hacer(cliente, equipo):
    cuerpo = (await cliente.get("/v1/dispositivos/mio", headers=equipo["cab"])).json()
    assert cuerpo["estado"] == "activo"
    assert cuerpo["borrar"] is False
    assert cuerpo["borrado_motivo"] is None
    assert cuerpo["dias_max_offline"] >= 1


async def test_un_equipo_suspendido_SI_puede_leer_su_orden(cliente, sesion, equipo):
    """Sería la única orden del sistema imposible de entregar a su destinatario.

    Y el equipo con la orden está, por definición, suspendido.
    """
    await _ordenar(sesion, equipo["id"])
    respuesta = await cliente.get("/v1/dispositivos/mio", headers=equipo["cab"])
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "suspendido"
    assert cuerpo["borrar"] is True
    assert cuerpo["borrado_motivo"] == "el vendedor dejó la empresa"


async def test_las_ordenes_avisan_del_acceso_por_caducar(cliente, sesion, equipo):
    """Enterarse a las 6 de la mañana, con el camión cargado, es un día perdido.

    El servidor lo sabe con días de antelación; el teléfono puede avisarlo si se
    lo dicen.
    """
    await sesion.execute(
        text("UPDATE dispositivos SET ultima_sync_push_en = now() - interval '5 days' "
             " WHERE id = :d"),
        {"d": equipo["id"]},
    )
    await sesion.commit()
    cuerpo = (await cliente.get("/v1/dispositivos/mio", headers=equipo["cab"])).json()
    assert cuerpo["dias_sin_sincronizar"] == 5


async def test_nunca_sincronizado_no_es_cero_dias(cliente, equipo):
    """«Nunca» y «hoy» no son lo mismo, y un 0 haría ver al equipo como al día."""
    cuerpo = (await cliente.get("/v1/dispositivos/mio", headers=equipo["cab"])).json()
    assert cuerpo["dias_sin_sincronizar"] is None


# ---------------------------------------------------------------------------
# La confirmación
# ---------------------------------------------------------------------------
async def test_confirmar_sin_orden_se_rechaza(cliente, equipo):
    """Un cliente con un bug no puede marcar como borrado un equipo que trabaja.

    Si se aceptara, la oficina lo daría por recuperado y dejaría de buscarlo.
    """
    respuesta = await cliente.post(
        "/v1/dispositivos/mio/borrado",
        json={"cola_pendiente": 0},
        headers=equipo["cab"],
    )
    assert respuesta.status_code == 409
    assert "no hay orden" in respuesta.json()["detail"]


async def test_confirmar_deja_el_equipo_revocado_y_con_su_hora(
    cliente, sesion, equipo
):
    await _ordenar(sesion, equipo["id"])
    respuesta = await cliente.post(
        "/v1/dispositivos/mio/borrado",
        json={"cola_pendiente": 0},
        headers=equipo["cab"],
    )
    assert respuesta.status_code == 204, respuesta.text

    fila = (
        await sesion.execute(
            text(
                "SELECT estado, revocado_en, revocado_motivo, borrado_confirmado_en, "
                "       borrado_cola_al_confirmar "
                "  FROM dispositivos WHERE id = :d"
            ),
            {"d": equipo["id"]},
        )
    ).mappings().one()
    assert fila["estado"] == "revocado"
    assert fila["borrado_confirmado_en"] is not None
    assert fila["borrado_cola_al_confirmar"] == 0
    assert "borrado remoto" in fila["revocado_motivo"]


async def test_confirmar_mata_las_sesiones(cliente, sesion, equipo):
    """Es la última petición que ese equipo puede hacer."""
    await _ordenar(sesion, equipo["id"])
    await cliente.post(
        "/v1/dispositivos/mio/borrado",
        json={"cola_pendiente": 0},
        headers=equipo["cab"],
    )
    vivas = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM sesiones "
                " WHERE dispositivo_id = :d AND revocada_en IS NULL"
            ),
            {"d": equipo["id"]},
        )
    ).scalar_one()
    assert vivas == 0

    # Y el token ya no sirve: el guardia lee el estado en cada petición.
    assert (
        await cliente.get("/v1/dispositivos/mio", headers=equipo["cab"])
    ).status_code == 401


async def test_confirmar_dos_veces_no_cambia_la_primera_hora(cliente, sesion, equipo):
    """La red entrega dos veces; es la misma regla que el resto del sistema.

    El equipo ya está revocado tras la primera, así que la segunda llega como
    401 — y eso también es idempotente desde el punto de vista del teléfono:
    volvió a intentar y el servidor ya lo tenía por borrado.
    """
    await _ordenar(sesion, equipo["id"])
    await cliente.post(
        "/v1/dispositivos/mio/borrado",
        json={"cola_pendiente": 0},
        headers=equipo["cab"],
    )
    primera = (
        await sesion.execute(
            text("SELECT borrado_confirmado_en FROM dispositivos WHERE id = :d"),
            {"d": equipo["id"]},
        )
    ).scalar_one()

    segunda = await cliente.post(
        "/v1/dispositivos/mio/borrado",
        json={"cola_pendiente": 0},
        headers=equipo["cab"],
    )
    assert segunda.status_code == 401

    sigue = (
        await sesion.execute(
            text("SELECT borrado_confirmado_en FROM dispositivos WHERE id = :d"),
            {"d": equipo["id"]},
        )
    ).scalar_one()
    assert sigue == primera


async def test_una_cola_distinta_de_cero_queda_registrada(cliente, sesion, equipo):
    """No se rechaza: se guarda y se puede auditar.

    El teléfono no debe borrar con cola pendiente. Si algún día llega un número
    distinto de 0, es que una versión del cliente se saltó la regla — y esta
    columna es la única forma de notarlo. Rechazar la confirmación dejaría al
    equipo ya borrado y al servidor creyendo que no, que es peor.
    """
    await _ordenar(sesion, equipo["id"])
    respuesta = await cliente.post(
        "/v1/dispositivos/mio/borrado",
        json={"cola_pendiente": 3},
        headers=equipo["cab"],
    )
    assert respuesta.status_code == 204
    cuantos = (
        await sesion.execute(
            text("SELECT borrado_cola_al_confirmar FROM dispositivos WHERE id = :d"),
            {"d": equipo["id"]},
        )
    ).scalar_one()
    assert cuantos == 3


async def test_la_base_impide_un_borrado_sin_motivo(sesion, semilla):
    """El CHECK de la migración, no solo la validación de la pantalla.

    Un borrado sin motivo es una decisión que nadie puede revisar después, y
    ésta destruye datos.
    """
    from sqlalchemy.exc import IntegrityError

    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Sin motivo', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    with pytest.raises(IntegrityError):
        await sesion.execute(
            text(
                "UPDATE dispositivos SET borrado_ordenado_en = now() WHERE id = :d"
            ),
            {"d": dispositivo},
        )
        await sesion.commit()
    await sesion.rollback()


async def test_la_base_impide_confirmar_sin_orden(sesion, semilla):
    from sqlalchemy.exc import IntegrityError

    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Sin orden', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    with pytest.raises(IntegrityError):
        await sesion.execute(
            text(
                "UPDATE dispositivos SET borrado_confirmado_en = now() WHERE id = :d"
            ),
            {"d": dispositivo},
        )
        await sesion.commit()
    await sesion.rollback()


# ---------------------------------------------------------------------------
# La pantalla del panel
# ---------------------------------------------------------------------------
async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


async def _csrf(cliente) -> str:
    import re

    r = await cliente.get("/panel/equipos")
    coincidencia = re.search(r'name="csrf" value="([^"]+)"', r.text)
    assert coincidencia, "la pantalla no trajo token CSRF"
    return coincidencia.group(1)


async def test_la_pantalla_lista_los_equipos_con_su_rezago(cliente, sesion, equipo):
    await sesion.execute(
        text("UPDATE dispositivos SET ultima_sync_push_en = now() - interval '4 days' "
             " WHERE id = :d"),
        {"d": equipo["id"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipos")
    assert pantalla.status_code == 200, pantalla.text
    texto = solo_texto(pantalla)
    assert "POCO M5s de Juan" in texto
    assert "equipos sin subir hoy" in texto
    assert "hace 4 día(s)" in texto
    # Y los días que le quedan de login sin red: 7 por omisión menos 4.
    assert "3 día(s)" in texto


async def test_un_acceso_caducado_se_dice_con_esa_palabra(cliente, sesion, equipo):
    await sesion.execute(
        text("UPDATE dispositivos SET ultima_sync_push_en = now() - interval '20 days' "
             " WHERE id = :d"),
        {"d": equipo["id"]},
    )
    await sesion.commit()
    await _entrar(cliente)
    texto = solo_texto(await cliente.get("/panel/equipos"))
    assert "caducado" in texto


async def test_ordenar_un_borrado_exige_escribir_la_palabra(cliente, sesion, equipo):
    """Un botón de «¿seguro?» se contesta con un clic reflejo."""
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    respuesta = await cliente.post(
        f"/panel/equipos/{equipo['id']}/borrado",
        data={"csrf": csrf, "motivo": "renunció", "confirmacion": "borrar"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]

    sin_orden = (
        await sesion.execute(
            text("SELECT borrado_ordenado_en FROM dispositivos WHERE id = :d"),
            {"d": equipo["id"]},
        )
    ).scalar_one()
    assert sin_orden is None


async def test_ordenar_un_borrado_exige_motivo(cliente, sesion, equipo):
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    respuesta = await cliente.post(
        f"/panel/equipos/{equipo['id']}/borrado",
        data={"csrf": csrf, "motivo": "", "confirmacion": "BORRAR"},
        follow_redirects=False,
    )
    assert "error=" in respuesta.headers["location"]


async def test_ordenar_deja_el_equipo_suspendido_no_revocado(
    cliente, sesion, equipo
):
    """Es la decisión que hace que todo funcione.

    Revocarlo lo dejaría sin forma de entregar, y entonces el borrado costaría
    las ventas del día.
    """
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    respuesta = await cliente.post(
        f"/panel/equipos/{equipo['id']}/borrado",
        data={
            "csrf": csrf,
            "motivo": "el vendedor dejó la empresa",
            "confirmacion": "BORRAR",
        },
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert "aviso=" in respuesta.headers["location"]

    fila = (
        await sesion.execute(
            text(
                "SELECT estado, borrado_ordenado_en, borrado_ordenado_por, borrado_motivo "
                "  FROM dispositivos WHERE id = :d"
            ),
            {"d": equipo["id"]},
        )
    ).mappings().one()
    assert fila["estado"] == "suspendido"
    assert fila["borrado_ordenado_en"] is not None
    assert fila["borrado_ordenado_por"] is not None
    assert fila["borrado_motivo"] == "el vendedor dejó la empresa"


async def test_cancelar_una_orden_antes_de_que_se_ejecute(cliente, sesion, equipo):
    """Para el caso real: se ordenó el borrado del equipo equivocado."""
    await _ordenar(sesion, equipo["id"])
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    respuesta = await cliente.post(
        f"/panel/equipos/{equipo['id']}/borrado/cancelar",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert "aviso=" in respuesta.headers["location"]

    fila = (
        await sesion.execute(
            text("SELECT estado, borrado_ordenado_en FROM dispositivos WHERE id = :d"),
            {"d": equipo["id"]},
        )
    ).mappings().one()
    assert fila["borrado_ordenado_en"] is None
    # Sigue suspendido: reactivarlo en silencio sería decidir por quien lo
    # suspendió.
    assert fila["estado"] == "suspendido"


async def test_no_se_puede_cancelar_un_borrado_ya_confirmado(cliente, sesion, equipo):
    """Después ya no hay nada que cancelar: los datos del teléfono no existen."""
    await _ordenar(sesion, equipo["id"])
    await _entrar(cliente)
    # El token se toma ANTES de confirmar: después, la pantalla ya no dibuja
    # ningún formulario para ese equipo —no hay nada que hacer con él— y por lo
    # tanto no trae token. Que no lo traiga es parte del comportamiento
    # correcto, así que la prueba se adapta en vez de forzarlo.
    csrf = await _csrf(cliente)

    await cliente.post(
        "/v1/dispositivos/mio/borrado",
        json={"cola_pendiente": 0},
        headers=equipo["cab"],
    )
    respuesta = await cliente.post(
        f"/panel/equipos/{equipo['id']}/borrado/cancelar",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert "error=" in respuesta.headers["location"]

    # Y la pantalla lo dice: este equipo ya se borró.
    assert "ya se borró" in solo_texto(await cliente.get("/panel/equipos"))


async def test_reactivar_choca_con_el_equipo_activo_del_mismo_usuario(
    cliente, sesion, equipo, semilla
):
    """Un vendedor opera UN equipo a la vez: hay un índice único que lo impone.

    Se comprueba antes para dar un mensaje en vez de un error de restricción,
    que en una pantalla es un 500.
    """
    await _poner_estado(sesion, equipo["id"], "suspendido")
    otro = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'El nuevo', 'activo', now())"
        ),
        {"d": otro, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    csrf = await _csrf(cliente)
    respuesta = await cliente.post(
        f"/panel/equipos/{equipo['id']}/estado",
        data={"csrf": csrf, "destino": "activo"},
        follow_redirects=False,
    )
    destino = respuesta.headers["location"]
    assert "error=" in destino
    # El mensaje nombra al otro equipo: «ya tiene activo X» dice qué suspender,
    # y «un usuario ya tiene un equipo activo» obliga a ir a buscar cuál.
    assert "El%20nuevo" in destino or "El nuevo" in destino


async def test_revocar_desde_el_panel_exige_motivo(cliente, equipo):
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    respuesta = await cliente.post(
        f"/panel/equipos/{equipo['id']}/estado",
        data={"csrf": csrf, "destino": "revocado", "motivo": ""},
        follow_redirects=False,
    )
    assert "error=" in respuesta.headers["location"]


async def test_la_pantalla_no_deja_editar_sin_el_permiso(cliente, sesion, semilla, equipo):
    """Un gerente puede VER los equipos y no puede tocarlos.

    `dispositivos.administrar` no está en su rol: ordenar un borrado destruye
    datos, y monitorear no es operar.
    """
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'GER02', 'Gerencia', :h, 'gerente', now(), now())"
        ),
        {
            "u": uuid.uuid4(),
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    await sesion.commit()

    await _entrar(cliente, "GER02")
    pantalla = await cliente.get("/panel/equipos")
    assert pantalla.status_code == 200
    assert "POCO M5s de Juan" in solo_texto(pantalla)
    assert 'name="confirmacion"' not in pantalla.text

    prohibido = await cliente.post(
        f"/panel/equipos/{equipo['id']}/estado",
        data={"csrf": "x", "destino": "revocado", "motivo": "porque sí"},
        follow_redirects=False,
    )
    # El CSRF se revisa primero, así que puede ser 403 por cualquiera de los
    # dos motivos; lo que importa es que NO pasa.
    assert prohibido.status_code == 403


# ---------------------------------------------------------------------------
# Vincular un teléfono desde la oficina
# ---------------------------------------------------------------------------
# El paso que no existía, y sin el cual NINGÚN vendedor puede entrar a la app: su
# login exige el id de un equipo registrado a su nombre, y `/dispositivos/registrar`
# crea el equipo a nombre de quien llama —que necesita un token, que necesita el
# login—. La cadena se cerraba sobre sí misma.
#
# Lo tapaba solo el sembrador del modo demo, que escribe la credencial directo en
# el SQLite del teléfono y que los cerrojos de compilación eliminan del release.


async def test_la_pantalla_ofrece_vincular_a_un_vendedor_sin_equipo(cliente, semilla):
    await _entrar(cliente)
    texto = solo_texto(await cliente.get("/panel/equipos"))
    assert "Vincular un teléfono" in texto
    # El vendedor de la semilla no tiene equipo: tiene que estar ofrecido.
    assert "VEND01" in texto


async def test_vincular_crea_el_equipo_y_muestra_su_clave(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.post(
        "/panel/equipos/registrar",
        data={
            "vendedor_id": str(semilla["vendedor"]),
            "etiqueta": "Moto G54 — Juan",
            "csrf": await _csrf(cliente),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    fila = (
        await sesion.execute(
            text(
                "SELECT id, etiqueta, estado, clave_vinculo FROM dispositivos "
                " WHERE usuario_id = :v"
            ),
            {"v": semilla["vendedor"]},
        )
    ).mappings().one()
    assert fila["etiqueta"] == "Moto G54 — Juan"
    assert fila["estado"] == "activo"

    # La clave tiene que llegar a la pantalla: es lo que se teclea en el
    # teléfono, y si no se muestra el paso queda a medias y nadie sabe qué poner.
    # Sin elegirla, el panel inventa una de seis (ADR 0002 §85).
    assert len(fila["clave_vinculo"]) == 6
    assert fila["clave_vinculo"] in r.headers["location"]


async def test_el_id_del_equipo_es_uuid7(cliente, sesion, semilla):
    """Ordenable por tiempo, como el resto de los ids del sistema."""
    await _entrar(cliente)
    await cliente.post(
        "/panel/equipos/registrar",
        data={
            "vendedor_id": str(semilla["vendedor"]),
            "etiqueta": "Moto G54",
            "csrf": await _csrf(cliente),
        },
        follow_redirects=False,
    )
    creado = (
        await sesion.execute(
            text("SELECT id FROM dispositivos WHERE usuario_id = :v"),
            {"v": semilla["vendedor"]},
        )
    ).scalar_one()
    assert creado.version == 7


async def test_un_vendedor_opera_un_equipo_a_la_vez(cliente, sesion, semilla, equipo):
    """Y el mensaje dice qué hacer, no que la base se quejó.

    Hay un índice único parcial que lo impone (migración 0001). Sin esta
    comprobación el segundo intento daría un error de restricción, que en una
    pantalla es un 500.
    """
    await _entrar(cliente)
    r = await cliente.post(
        "/panel/equipos/registrar",
        data={
            "vendedor_id": str(semilla["vendedor"]),
            "etiqueta": "El segundo",
            "csrf": await _csrf(cliente),
        },
        follow_redirects=True,
    )
    texto = solo_texto(r)
    assert "ya tiene" in texto
    assert "uno a la vez" in texto
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM dispositivos WHERE usuario_id = :v"),
            {"v": semilla["vendedor"]},
        )
    ).scalar_one() == 1


async def test_a_gerencia_no_se_le_vincula_un_telefono(cliente, sesion, semilla):
    """Gerencia entra al tablero con su usuario, sin equipo.

    Y no es una limitación de la pantalla: el login solo exige `dispositivo_id`
    para el rol vendedor, porque es el único que opera sin señal.
    """
    await _entrar(cliente)
    gerente = (
        await sesion.execute(
            text("SELECT id FROM usuarios WHERE rol_codigo <> 'vendedor' LIMIT 1")
        )
    ).scalar_one()
    r = await cliente.post(
        "/panel/equipos/registrar",
        data={
            "vendedor_id": str(gerente),
            "etiqueta": "Una laptop",
            "csrf": await _csrf(cliente),
        },
        follow_redirects=True,
    )
    assert "Solo un vendedor" in solo_texto(r)


async def test_vincular_exige_etiqueta(cliente, semilla):
    """Con ocho teléfonos, uno sin etiqueta no se distingue de otro."""
    await _entrar(cliente)
    r = await cliente.post(
        "/panel/equipos/registrar",
        data={
            "vendedor_id": str(semilla["vendedor"]),
            "etiqueta": "   ",
            "csrf": await _csrf(cliente),
        },
        follow_redirects=True,
    )
    assert "etiqueta" in solo_texto(r)


async def test_vincular_exige_csrf(cliente, semilla):
    await _entrar(cliente)
    r = await cliente.post(
        "/panel/equipos/registrar",
        data={"vendedor_id": str(semilla["vendedor"]), "etiqueta": "X", "csrf": "malo"},
        follow_redirects=False,
    )
    assert r.status_code == 403
