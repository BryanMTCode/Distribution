"""Cuando una ruta cambia de manos, sus clientes viajan con ella (migración 0042).

Lo encontró la auditoría panel → teléfono. El panel cambiaba el titular de la ruta
escribiendo `usuarios_rutas`, y el `change_log` no se enteraba:

1. El teléfono del titular **nuevo** recibía la ruta vacía: su cursor ya estaba
   más allá de los deltas de esos clientes, publicados cuando se dieron de alta.
2. El teléfono del titular **anterior** se quedaba con todos los clientes, y cada
   venta que les hiciera caía en cuarentena.
3. Durante la media hora que dura el token, el pull filtraba con las rutas de la
   foto: lo que se editaba en la ruta nueva quedaba atrás del cursor y no llegaba
   nunca.

Las pruebas usan tokens emitidos ANTES del cambio y nunca los renuevan: así es
como está el teléfono en la calle cuando la oficina mueve la ruta.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.seguridad import hashear_password
from tests.conftest import PASSWORD_VENDEDOR
from tests.conftest import csrf_del_panel as _csrf
from tests.test_plan_visita import _cliente, _entrar

pytestmark = pytest.mark.asyncio


async def _vendedor_con_telefono(sesion, codigo: str) -> uuid.UUID:
    usuario = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "SELECT :u, sucursal_id, :c, :c, :h, 'vendedor', now(), now() "
            "  FROM usuarios WHERE codigo = 'VEND01'"
        ),
        {"u": usuario, "c": codigo, "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()
    return usuario


async def _cabecera(cliente, sesion, usuario_id: uuid.UUID, codigo: str) -> dict:
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Moto G54', 'activo', now())"
        ),
        {"d": dispositivo, "u": usuario_id},
    )
    await sesion.commit()
    r = await cliente.post(
        "/v1/auth/login",
        json={"codigo": codigo, "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo)},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _hasta_el_final(cliente, cab: dict, cursor: int = 0) -> tuple[int, list[dict]]:
    """Recorre el pull completo, como lo hace el teléfono."""
    cambios: list[dict] = []
    hay_mas = True
    while hay_mas:
        r = await cliente.get(f"/v1/sync/pull?cursor={cursor}", headers=cab)
        assert r.status_code == 200, r.text
        d = r.json()
        cambios += d["cambios"]
        cursor, hay_mas = d["cursor"], d["hay_mas"]
    return cursor, cambios


def _de(cambios: list[dict], entidad: str, operacion: str) -> set[str]:
    return {
        c["entidad_id"]
        for c in cambios
        if c["entidad"] == entidad and c["operacion"] == operacion
    }


async def _cambiar_titular(cliente, ruta_id, vendedor_id) -> None:
    await _entrar(cliente)
    pantalla = await cliente.get("/panel/equipo")
    r = await cliente.post(
        f"/panel/equipo/rutas/{ruta_id}/titular",
        data={"csrf": _csrf(cliente, pantalla), "vendedor_id": str(vendedor_id)},
        follow_redirects=True,
    )
    assert "titular actualizado" in r.text


async def test_el_titular_nuevo_recibe_la_ruta_completa_y_el_anterior_la_suelta(
    cliente, sesion, semilla
):
    # La ruta lleva tiempo trabajándose: sus clientes son viejos.
    tienda = await _cliente(sesion, semilla, "Abarrotes Lupita", secuencia=1)
    fonda = await _cliente(sesion, semilla, "Fonda Doña Mary", secuencia=2)

    anterior = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    maria = await _vendedor_con_telefono(sesion, "VEND02")
    nueva = await _cabecera(cliente, sesion, maria, "VEND02")

    # Los dos teléfonos están al día ANTES del cambio.
    cursor_anterior, lo_del_anterior = await _hasta_el_final(cliente, anterior)
    cursor_nueva, lo_de_maria = await _hasta_el_final(cliente, nueva)
    assert {str(tienda), str(fonda)} <= _de(lo_del_anterior, "cliente", "upsert")
    assert not _de(lo_de_maria, "cliente", "upsert") & {str(tienda), str(fonda)}

    await _cambiar_titular(cliente, semilla["ruta"], maria)

    # María, con el token de antes del cambio, recibe la ruta completa: los
    # clientes y su cartera, sin esperar a que alguien los edite.
    _, delta = await _hasta_el_final(cliente, nueva, cursor_nueva)
    assert _de(delta, "cliente", "upsert") == {str(tienda), str(fonda)}
    assert _de(delta, "cartera", "upsert") == {str(tienda), str(fonda)}
    lupita = next(c for c in delta if c["entidad"] == "cliente" and c["entidad_id"] == str(tienda))
    assert lupita["payload"]["nombre_comercial"] == "Abarrotes Lupita"
    assert lupita["payload"]["ruta_id"] == str(semilla["ruta"])
    assert "ubicacion" not in lupita["payload"]
    # La cartera llega DESPUÉS del cliente al que pertenece.
    orden = [(c["entidad"], c["entidad_id"]) for c in delta]
    assert orden.index(("cliente", str(tienda))) < orden.index(("cartera", str(tienda)))

    # El anterior recibe la baja de cada uno.
    _, delta = await _hasta_el_final(cliente, anterior, cursor_anterior)
    assert _de(delta, "cliente", "delete") == {str(tienda), str(fonda)}
    assert not _de(delta, "cliente", "upsert")


async def test_lo_que_se_edita_despues_le_llega_al_nuevo_aunque_su_token_sea_viejo(
    cliente, sesion, semilla
):
    """El hueco de la media hora: el pull filtraba con las rutas del token."""
    tienda = await _cliente(sesion, semilla, "Abarrotes Lupita")
    anterior = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    maria = await _vendedor_con_telefono(sesion, "VEND02")
    nueva = await _cabecera(cliente, sesion, maria, "VEND02")
    cursor_anterior, _ = await _hasta_el_final(cliente, anterior)
    cursor_nueva, _ = await _hasta_el_final(cliente, nueva)

    await _cambiar_titular(cliente, semilla["ruta"], maria)
    cursor_anterior, _ = await _hasta_el_final(cliente, anterior, cursor_anterior)
    cursor_nueva, _ = await _hasta_el_final(cliente, nueva, cursor_nueva)

    await sesion.execute(
        text("UPDATE clientes SET telefono = '5512345678' WHERE id = :c"), {"c": tienda}
    )
    await sesion.commit()

    _, delta = await _hasta_el_final(cliente, nueva, cursor_nueva)
    editado = [c for c in delta if c["entidad"] == "cliente" and c["entidad_id"] == str(tienda)]
    assert editado and editado[-1]["payload"]["telefono"] == "5512345678"

    # Y el anterior ya no: si le llegara, el cliente le «revivía» en el teléfono.
    _, delta = await _hasta_el_final(cliente, anterior, cursor_anterior)
    assert not _de(delta, "cliente", "upsert")


async def test_un_cliente_con_deuda_que_cambia_de_ruta_llega_con_su_saldo(
    cliente, sesion, semilla
):
    """El saldo vive en el delta de `cartera`, que solo se publicaba al cambiar el
    crédito. El teléfono nuevo veía al cliente sin deuda y con toda su línea libre."""
    tienda = await _cliente(sesion, semilla, "Abarrotes Lupita")
    venta = uuid.uuid4()
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Moto G54', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "UPDATE clientes SET permite_credito = true, limite_credito = 1000 WHERE id = :c"
        ),
        {"c": tienda},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :a, 'credito',
                    800.00, 800.00, now(), CURRENT_DATE)
            """
        ),
        {"v": venta, "d": dispositivo, "c": tienda, "u": semilla["vendedor"],
         "a": semilla["camion"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                            importe_pagado, fecha_emision,
                                            fecha_vencimiento, estado, actualizado_en)
            VALUES (:v, :c, 800.00, 0, CURRENT_DATE, CURRENT_DATE + 15, 'abierta', now())
            """
        ),
        {"v": venta, "c": tienda},
    )
    maria = await _vendedor_con_telefono(sesion, "VEND02")
    otra = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre, vendedor_id) VALUES (:r, 'R09', 'Norte', :v)"),
        {"r": otra, "v": maria},
    )
    await sesion.execute(
        text("INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r)"),
        {"u": maria, "r": otra},
    )
    await sesion.commit()
    nueva = await _cabecera(cliente, sesion, maria, "VEND02")
    cursor, _ = await _hasta_el_final(cliente, nueva)

    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :r WHERE id = :c"), {"r": otra, "c": tienda}
    )
    await sesion.commit()

    _, delta = await _hasta_el_final(cliente, nueva, cursor)
    orden = [(c["entidad"], c["operacion"]) for c in delta if c["entidad_id"] == str(tienda)]
    assert orden == [("cliente", "upsert"), ("cartera", "upsert")]
    cartera = next(c for c in delta if c["entidad"] == "cartera")
    assert Decimal(str(cartera["payload"]["saldo"])) == Decimal("800")


async def test_el_alcance_y_el_camion_se_leen_en_cada_peticion(cliente, sesion, semilla):
    """Con el token de antes del cambio, `/yo` ya dice lo nuevo: es lo mismo que usan
    el push (¿de quién es este cliente? ¿de qué camión se descuenta?) y el pull."""
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    antes = (await cliente.get("/v1/auth/yo", headers=cab)).json()
    assert antes["rutas"] == [str(semilla["ruta"])]
    assert antes["almacen_id"] == str(semilla["camion"])

    await sesion.execute(
        text("DELETE FROM usuarios_rutas WHERE usuario_id = :u"), {"u": semilla["vendedor"]}
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = NULL WHERE id = :u"), {"u": semilla["vendedor"]}
    )
    await sesion.commit()

    despues = (await cliente.get("/v1/auth/yo", headers=cab)).json()
    assert despues["rutas"] == []
    assert despues["almacen_id"] is None


async def test_un_permiso_quitado_deja_de_valer_sin_esperar_al_token(cliente, sesion, semilla):
    """Igual que en el panel: quitarle un permiso a alguien vale desde la siguiente
    petición, no media hora después."""
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    assert "clientes.crear" in (await cliente.get("/v1/auth/yo", headers=cab)).json()["permisos"]

    await sesion.execute(
        text(
            "INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
            "VALUES (:u, 'clientes.crear', false)"
        ),
        {"u": semilla["vendedor"]},
    )
    await sesion.commit()

    yo = (await cliente.get("/v1/auth/yo", headers=cab)).json()
    assert "clientes.crear" not in yo["permisos"]
    assert yo["rol"] == "vendedor"


async def test_crear_una_ruta_con_titular_no_publica_nada_si_esta_vacia(sesion, semilla):
    """La republicación es de lo que la ruta YA tiene; una ruta nueva no tiene nada."""
    maria = await _vendedor_con_telefono(sesion, "VEND02")
    antes = (await sesion.execute(text("SELECT count(*) FROM change_log"))).scalar_one()
    ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre, vendedor_id) VALUES (:r, 'R09', 'Nueva', :v)"),
        {"r": ruta, "v": maria},
    )
    await sesion.execute(
        text("INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r)"),
        {"u": maria, "r": ruta},
    )
    await sesion.commit()
    despues = (await sesion.execute(text("SELECT count(*) FROM change_log"))).scalar_one()
    assert despues == antes


async def test_borrar_un_usuario_con_ruta_no_choca_con_su_propio_aviso(sesion, semilla):
    """Un vendedor dado de alta por error, al que se le dio una ruta que luego se
    llenó de clientes. Al borrarlo, la cascada quita su `usuarios_rutas`, y el aviso
    de «suelta estos clientes» apuntaría a un usuario que ya no existe: la llave de
    `change_log.vendedor_id` tumbaría el borrado entero."""
    maria = await _vendedor_con_telefono(sesion, "VEND02")
    await sesion.execute(
        text("INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r)"),
        {"u": maria, "r": semilla["ruta"]},
    )
    await sesion.commit()
    # La ruta estaba vacía al dársela (nada se republicó a su nombre) y se llenó
    # después.
    await _cliente(sesion, semilla, "Abarrotes Lupita")

    await sesion.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": maria})
    await sesion.commit()
    queda = (
        await sesion.execute(text("SELECT count(*) FROM usuarios WHERE id = :u"), {"u": maria})
    ).scalar_one()
    assert queda == 0
