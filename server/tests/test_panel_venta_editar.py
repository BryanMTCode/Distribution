"""La oficina corrige y cancela una venta ya recibida.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Esta pantalla toca un documento que ya está impreso y en manos de un cliente, y
mueve inventario de un día que puede estar cerrado. Lo que se prueba no es que
guarde: es que **no se pueda hacer daño con ella**.

1. **La mercancía vuelve al camión**, exacta, y el teléfono se entera. Si la
   cancelación no devolviera el inventario, el camión arrastraría un faltante que
   el vendedor pagaría en la liquidación.
2. **Un día ya liquidado no se toca.** Alguien firmó ese cierre contra un conteo
   físico; cambiar una venta después deja ese papel explicando otra aritmética.
3. **Una venta a crédito con cobros aplicados tampoco**, o quedaría un pago
   aplicado a una factura que no existe.
4. **Las cantidades solo bajan.** Subirlas sería inventar una entrega.
5. **El motivo es obligatorio**, y el antes/después queda en `auditoria`: es lo
   único que puede explicar, meses después, por qué el sistema y el papel del
   cliente no dicen lo mismo.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, texto_plano

pytestmark = pytest.mark.asyncio

MOTIVO = "el vendedor capturó 20 cajas y entregó 2, el cliente lo confirmó"


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(cliente, respuesta=None) -> str:
    marca = 'name="csrf" value="'
    if respuesta is not None and marca in respuesta.text:
        inicio = respuesta.text.index(marca) + len(marca)
        return respuesta.text[inicio : respuesta.text.index('"', inicio)]

    import hashlib
    import hmac

    from app.core.config import obtener_config

    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cliente.cookies.get('dsd_panel', '')}".encode(),
        hashlib.sha256,
    ).hexdigest()


@pytest.fixture
async def venta_del_dia(sesion, semilla) -> dict:
    """Una venta de contado de 20 cajas, con el camión ya descontado.

    Montada con los mismos movimientos que escribe el manejador de sincronización,
    para que la existencia de partida sea la real: el camión arrancó con 240 piezas
    y la venta sacó 480... no: 20 cajas de 24 son 480, más de lo que trae. Se
    cargan 600 para que el escenario sea posible sin negativos, que es otro caso.
    """
    producto = uuid.uuid4()
    carga = uuid.uuid4()
    dispositivo = uuid.uuid4()
    venta = uuid.uuid4()
    cliente_id = uuid.uuid4()
    hoy = date.today()

    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA', 0)"
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
    # 600 cargadas − 480 vendidas = 120 arriba del camión.
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:b, :p, 400), (:c, :p, 120)"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "p": producto},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id,
                                vendedor_id, ruta_id, fecha_operativa, estado,
                                confirmada_en)
            VALUES (:id, 'CG-VENTA', :b, :c, :v, :r, :d, 'confirmada', now())
            """
        ),
        {
            "id": carga,
            "b": semilla["bodega"],
            "c": semilla["camion"],
            "v": semilla["vendedor"],
            "r": semilla["ruta"],
            "d": hoy,
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO carga_detalle (carga_id, producto_id, cantidad) "
            "VALUES (:c, :p, 600)"
        ),
        {"c": carga, "p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, "
            "                          ultima_sync_push_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, dias_credito, "
            "                      limite_credito, permite_credito, creado_en, actualizado_en) "
            "VALUES (:c, 'CLI-V1', 'La Esquina', :r, 15, 20000, true, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id, carga_id,
                                tipo, subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :r, :a, :carga, 'contado',
                    6000.00, 6000.00, now(), :dia)
            """
        ),
        {
            "v": venta,
            "d": dispositivo,
            "c": cliente_id,
            "u": semilla["vendedor"],
            "r": semilla["ruta"],
            "a": semilla["camion"],
            "carga": carga,
            "dia": hoy,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, tasa_iva, importe)
            VALUES (:id, :v, 1, :p, 'CAJA', 24, 20.000, 480.000, 300.0000, 0, 6000.00)
            """
        ),
        {"id": uuid.uuid4(), "v": venta, "p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario "
            "  (tipo, almacen_origen_id, producto_id, cantidad, documento_tipo, "
            "   documento_id) "
            "VALUES ('venta', :c, :p, 480, 'venta', :doc)"
        ),
        {"c": semilla["camion"], "p": producto, "doc": venta},
    )
    await sesion.commit()

    return {
        "venta": venta,
        "producto": producto,
        "carga": carga,
        "cliente": cliente_id,
        "dispositivo": dispositivo,
        "dia": hoy,
    }


async def _en_camion(sesion, semilla, producto) -> Decimal:
    return Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = :p"
                ),
                {"a": semilla["camion"], "p": producto},
            )
        ).scalar_one()
    )


async def _cancelar(cliente, venta_id, *, motivo=MOTIVO, devuelve="1"):
    detalle = await cliente.get(f"/panel/ventas/{venta_id}")
    datos = {"csrf": _csrf(cliente, detalle), "motivo": motivo}
    if devuelve:
        datos["reingresa_stock"] = devuelve
    return await cliente.post(
        f"/panel/ventas/{venta_id}/cancelar", data=datos, follow_redirects=True
    )


async def _corregir(cliente, venta_id, sesion, cantidad: str, *, motivo=MOTIVO):
    partida = (
        await sesion.execute(
            text("SELECT id FROM venta_partidas WHERE venta_id = :v"), {"v": venta_id}
        )
    ).scalar_one()
    detalle = await cliente.get(f"/panel/ventas/{venta_id}")
    return await cliente.post(
        f"/panel/ventas/{venta_id}/corregir",
        data={
            "csrf": _csrf(cliente, detalle),
            "motivo": motivo,
            f"cantidad_{partida}": cantidad,
        },
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Cancelar
# ---------------------------------------------------------------------------


async def test_CANCELAR_DEVUELVE_LA_MERCANCIA_AL_CAMION(
    cliente, semilla, venta_del_dia, sesion
):
    """Si no la devolviera, el camión arrastraría un faltante de 480 piezas que el
    vendedor pagaría en la liquidación por una venta que la oficina borró."""
    await _entrar(cliente)
    r = await _cancelar(cliente, venta_del_dia["venta"])

    assert "cancelada" in r.text
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("600.000")

    estado = (
        await sesion.execute(
            text("SELECT estado FROM ventas WHERE id = :v"), {"v": venta_del_dia["venta"]}
        )
    ).scalar_one()
    assert estado == "cancelada"

    movimiento = (
        await sesion.execute(
            text(
                "SELECT tipo, cantidad, almacen_origen_id, almacen_destino_id, "
                "       documento_tipo "
                "  FROM movimientos_inventario WHERE tipo = 'devolucion'"
            )
        )
    ).mappings().one()
    assert movimiento["cantidad"] == Decimal("480.000")
    assert movimiento["almacen_destino_id"] == semilla["camion"]
    assert movimiento["almacen_origen_id"] is None
    assert movimiento["documento_tipo"] == "venta_cancelada"


async def test_cancelar_escribe_su_documento_con_motivo_y_nombre(
    cliente, semilla, venta_del_dia, sesion
):
    """`ventas_cancelaciones` existe desde la migración 0005 y nunca se escribía."""
    await _entrar(cliente)
    await _cancelar(cliente, venta_del_dia["venta"])

    fila = (
        await sesion.execute(
            text(
                "SELECT motivo, reingresa_stock, usuario_id, autorizado_por "
                "  FROM ventas_cancelaciones WHERE venta_id = :v"
            ),
            {"v": venta_del_dia["venta"]},
        )
    ).mappings().one()
    assert fila["motivo"] == MOTIVO
    assert fila["reingresa_stock"] is True
    assert fila["usuario_id"] == semilla["admin"]


async def test_cancelar_sin_devolver_NO_toca_el_camion(
    cliente, semilla, venta_del_dia, sesion
):
    """El caso que `reingresa_stock` previó: se facturó al cliente equivocado y la
    mercancía SÍ se entregó. Devolverla al camión la duplicaría."""
    await _entrar(cliente)
    r = await _cancelar(cliente, venta_del_dia["venta"], devuelve="")

    assert "NO volvió al camión" in r.text
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120.000")
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM movimientos_inventario WHERE tipo = 'devolucion'")
        )
    ).scalar_one() == 0


async def test_cancelar_exige_un_motivo_que_alguien_pueda_leer(
    cliente, semilla, venta_del_dia, sesion
):
    await _entrar(cliente)
    r = await _cancelar(cliente, venta_del_dia["venta"], motivo="error")

    assert "al menos 10" in texto_plano(r)
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120.000")
    estado = (
        await sesion.execute(
            text("SELECT estado FROM ventas WHERE id = :v"), {"v": venta_del_dia["venta"]}
        )
    ).scalar_one()
    assert estado == "confirmada"


async def test_cancelar_dos_veces_no_devuelve_la_mercancia_dos_veces(
    cliente, semilla, venta_del_dia, sesion
):
    await _entrar(cliente)
    await _cancelar(cliente, venta_del_dia["venta"])
    segunda = await _cancelar(cliente, venta_del_dia["venta"])

    assert "ya estaba cancelada" in segunda.text
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("600.000")


async def test_cancelar_una_venta_a_credito_borra_la_deuda(
    cliente, semilla, venta_del_dia, sesion
):
    """Una cuenta por cobrar de una venta que no existe no es cobrable. Dejarla
    «incobrable» la haría aparecer en la cartera por antigüedad como si alguien
    tuviera que perseguirla."""
    await sesion.execute(
        text("UPDATE ventas SET tipo = 'credito' WHERE id = :v"),
        {"v": venta_del_dia["venta"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO cuentas_por_cobrar "
            "  (venta_id, cliente_id, importe_original, fecha_emision, fecha_vencimiento) "
            "VALUES (:v, :c, 6000.00, :d, :d)"
        ),
        {"v": venta_del_dia["venta"], "c": venta_del_dia["cliente"], "d": venta_del_dia["dia"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    await _cancelar(cliente, venta_del_dia["venta"])

    assert (
        await sesion.execute(
            text("SELECT count(*) FROM cuentas_por_cobrar WHERE venta_id = :v"),
            {"v": venta_del_dia["venta"]},
        )
    ).scalar_one() == 0


async def test_NO_SE_CANCELA_UNA_VENTA_A_CREDITO_YA_COBRADA(
    cliente, semilla, venta_del_dia, sesion
):
    """El dinero entró y el FIFO lo repartió. Cancelar la deuda dejaría un pago
    aplicado a una factura que no existe."""
    await sesion.execute(
        text("UPDATE ventas SET tipo = 'credito' WHERE id = :v"),
        {"v": venta_del_dia["venta"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO cuentas_por_cobrar "
            "  (venta_id, cliente_id, importe_original, importe_pagado, "
            "   fecha_emision, fecha_vencimiento, estado) "
            "VALUES (:v, :c, 6000.00, 2000.00, :d, :d, 'parcial')"
        ),
        {"v": venta_del_dia["venta"], "c": venta_del_dia["cliente"], "d": venta_del_dia["dia"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await _cancelar(cliente, venta_del_dia["venta"])

    assert "Reversa el cobro primero" in texto_plano(r)
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120.000")


async def test_NO_SE_TOCA_UNA_VENTA_DE_UN_DIA_YA_LIQUIDADO(
    cliente, semilla, venta_del_dia, sesion
):
    """Alguien firmó ese cierre contra un conteo físico del camión.

    Cambiar la venta después mueve el inventario de un día cerrado: el faltante que
    se le cobró al vendedor deja de corresponder a nada.
    """
    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, "
            "                           fecha_operativa, estado, cerrada_en) "
            "VALUES (:id, 'LQ-000001', :c, :v, :d, 'cerrada', now())"
        ),
        {
            "id": uuid.uuid4(),
            "c": venta_del_dia["carga"],
            "v": semilla["vendedor"],
            "d": venta_del_dia["dia"],
        },
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await _cancelar(cliente, venta_del_dia["venta"])

    assert "ya se liquidó (LQ-000001)" in texto_plano(r)
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120.000")

    # Y la pantalla lo dice ANTES de ofrecer el botón.
    plano = texto_plano(await cliente.get(f"/panel/ventas/{venta_del_dia['venta']}"))
    assert "ya se liquidó" in plano
    assert "Cancelar la venta" not in plano


# ---------------------------------------------------------------------------
# Corregir
# ---------------------------------------------------------------------------


async def test_CORREGIR_BAJA_LA_CANTIDAD_Y_DEVUELVE_LA_DIFERENCIA(
    cliente, semilla, venta_del_dia, sesion
):
    """20 cajas capturadas, 2 entregadas: vuelven 18 cajas = 432 piezas."""
    await _entrar(cliente)
    r = await _corregir(cliente, venta_del_dia["venta"], sesion, "2")

    assert "corregida" in r.text
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("552.000")

    fila = (
        await sesion.execute(
            text(
                "SELECT v.total, v.subtotal, v.corregida_por, v.correccion_motivo, "
                "       p.cantidad, p.cantidad_base, p.importe "
                "  FROM ventas v JOIN venta_partidas p ON p.venta_id = v.id "
                " WHERE v.id = :v"
            ),
            {"v": venta_del_dia["venta"]},
        )
    ).mappings().one()
    assert fila["cantidad"] == Decimal("2.000")
    assert fila["cantidad_base"] == Decimal("48.000")
    assert fila["importe"] == Decimal("600.00")
    assert fila["total"] == Decimal("600.00")
    assert fila["corregida_por"] == semilla["admin"]
    assert fila["correccion_motivo"] == MOTIVO


async def test_LAS_CANTIDADES_SOLO_BAJAN(cliente, semilla, venta_del_dia, sesion):
    """Entregar más de lo que dice la remisión es mercancía que salió del camión sin
    documento. Eso es una venta nueva, no una corrección."""
    await _entrar(cliente)
    r = await _corregir(cliente, venta_del_dia["venta"], sesion, "25")

    assert "solo pueden BAJAR" in texto_plano(r)
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120.000")


async def test_corregir_todo_a_cero_manda_a_cancelar(
    cliente, semilla, venta_del_dia, sesion
):
    """Una venta de cero pesos no explica nada. La cancelación sí: tiene documento,
    motivo y nombre."""
    await _entrar(cliente)
    r = await _corregir(cliente, venta_del_dia["venta"], sesion, "0")

    assert "lo que corresponde es cancelar" in texto_plano(r)
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120.000")


async def test_corregir_exige_motivo(cliente, semilla, venta_del_dia, sesion):
    await _entrar(cliente)
    r = await _corregir(cliente, venta_del_dia["venta"], sesion, "2", motivo="ya")

    assert "al menos 10" in texto_plano(r)
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120.000")


async def test_corregir_sin_cambiar_nada_no_publica_nada(
    cliente, semilla, venta_del_dia, sesion
):
    """Escribir la misma cantidad no es una corrección: reescribirla publicaría un
    delta que el teléfono tendría que aplicar para no cambiar nada."""
    await _entrar(cliente)
    r = await _corregir(cliente, venta_del_dia["venta"], sesion, "20")

    assert "No cambiaste ninguna cantidad" in texto_plano(r)


async def test_corregir_una_venta_a_credito_baja_la_deuda(
    cliente, semilla, venta_del_dia, sesion
):
    await sesion.execute(
        text("UPDATE ventas SET tipo = 'credito' WHERE id = :v"),
        {"v": venta_del_dia["venta"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO cuentas_por_cobrar "
            "  (venta_id, cliente_id, importe_original, fecha_emision, fecha_vencimiento) "
            "VALUES (:v, :c, 6000.00, :d, :d)"
        ),
        {"v": venta_del_dia["venta"], "c": venta_del_dia["cliente"], "d": venta_del_dia["dia"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    await _corregir(cliente, venta_del_dia["venta"], sesion, "2")

    fila = (
        await sesion.execute(
            text(
                "SELECT importe_original, saldo, estado FROM cuentas_por_cobrar "
                " WHERE venta_id = :v"
            ),
            {"v": venta_del_dia["venta"]},
        )
    ).mappings().one()
    assert fila["importe_original"] == Decimal("600.00")
    assert fila["saldo"] == Decimal("600.00")
    assert fila["estado"] == "abierta"


# ---------------------------------------------------------------------------
# La huella
# ---------------------------------------------------------------------------


async def test_el_antes_y_el_despues_quedan_en_auditoria(
    cliente, semilla, venta_del_dia, sesion
):
    """Es lo único que puede explicar, meses después, por qué el sistema y el papel
    del cliente no dicen lo mismo."""
    await _entrar(cliente)
    await _corregir(cliente, venta_del_dia["venta"], sesion, "2")

    fila = (
        await sesion.execute(
            text(
                "SELECT accion, motivo, datos_antes, datos_despues, usuario_id "
                "  FROM auditoria WHERE entidad = 'venta' AND entidad_id = :v"
            ),
            {"v": venta_del_dia["venta"]},
        )
    ).mappings().one()
    assert fila["accion"] == "corregir"
    assert fila["motivo"] == MOTIVO
    assert fila["datos_antes"]["total"] == "6000.00"
    assert fila["datos_antes"]["partidas"][0]["cantidad"] == "20.000"
    assert fila["datos_despues"]["total"] == "600.00"
    assert fila["usuario_id"] == semilla["admin"]


async def test_la_pantalla_avisa_que_la_venta_ya_no_coincide_con_su_papel(
    cliente, semilla, venta_del_dia, sesion
):
    await _entrar(cliente)
    await _corregir(cliente, venta_del_dia["venta"], sesion, "2")

    plano = texto_plano(await cliente.get(f"/panel/ventas/{venta_del_dia['venta']}"))
    assert "ya no coincide con su remisión impresa" in plano
    assert MOTIVO in plano


# ---------------------------------------------------------------------------
# Dar por revisada
# ---------------------------------------------------------------------------


async def test_dar_por_revisada_baja_la_bandera_y_deja_el_nombre(
    cliente, semilla, venta_del_dia, sesion
):
    """Una bandera que nadie baja deja de significar algo, y la lista de «por
    revisar» se vuelve ruido que se ignora completo."""
    await sesion.execute(
        text(
            "UPDATE ventas SET requiere_revision = true, "
            "       revision_motivos = ARRAY['reloj_desfasado'] WHERE id = :v"
        ),
        {"v": venta_del_dia["venta"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/ventas/{venta_del_dia['venta']}")
    await cliente.post(
        f"/panel/ventas/{venta_del_dia['venta']}/revisada",
        data={"csrf": _csrf(cliente, detalle), "nota": "el teléfono tenía la hora mal"},
        follow_redirects=True,
    )

    fila = (
        await sesion.execute(
            text(
                "SELECT requiere_revision, revision_motivos FROM ventas WHERE id = :v"
            ),
            {"v": venta_del_dia["venta"]},
        )
    ).mappings().one()
    assert fila["requiere_revision"] is False
    # El motivo original se queda: por qué se marcó es parte del historial.
    assert "reloj_desfasado" in fila["revision_motivos"]
    assert any(m.startswith("revisada_por:") for m in fila["revision_motivos"])


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


async def test_GERENCIA_SI_PUEDE_CANCELAR(cliente, semilla, venta_del_dia, sesion):
    """Decisión de la dirección, octubre 2026: gerencia deja de ser de solo lectura.

    La migración 0009 decía «Gerencia es de SOLO LECTURA sobre la operación.
    Monitorea, no opera», y la 0031 le concede los tres permisos de edición.
    """
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    await sesion.commit()

    await _entrar(cliente, "GER01")
    r = await _cancelar(cliente, venta_del_dia["venta"])

    assert "cancelada" in r.text
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("600.000")


async def test_un_vendedor_NI_ENTRA_al_panel(cliente, semilla, venta_del_dia):
    """Cancelar en la calle, frente al cliente y sin que nadie lo vea, es otra
    operación con otro riesgo.

    El vendedor no tiene `ventas.cancelar`, pero la defensa real es anterior: el
    panel no lo deja entrar, y por eso esta prueba comprueba la puerta y no el
    permiso. Un día que alguien le conceda un permiso de oficina por error, la
    puerta sigue cerrada.
    """
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "VEND01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "entra por la app del teléfono" in texto_plano(r)

    # Y sin sesión, el detalle de la venta tampoco se abre.
    detalle = await cliente.get(
        f"/panel/ventas/{venta_del_dia['venta']}", follow_redirects=False
    )
    assert detalle.status_code in (303, 307, 401, 403)


async def test_cancelar_exige_el_token_csrf(cliente, semilla, venta_del_dia):
    await _entrar(cliente)
    r = await cliente.post(
        f"/panel/ventas/{venta_del_dia['venta']}/cancelar",
        data={"csrf": "inventado", "motivo": MOTIVO, "reingresa_stock": "1"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# El delta hacia el teléfono
# ---------------------------------------------------------------------------


async def _ultimo_delta(sesion, venta_id):
    return (
        await sesion.execute(
            text(
                "SELECT payload, vendedor_id, ruta_id FROM change_log "
                " WHERE entidad = 'venta' AND entidad_id = :v "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"v": venta_id},
        )
    ).mappings().first()


async def test_UNA_VENTA_QUE_LLEGA_DEL_TELEFONO_NO_SE_LE_DEVUELVE(
    cliente, semilla, venta_del_dia, sesion
):
    """El disparador es AFTER UPDATE, no AFTER INSERT, y esto es lo que lo prueba.

    Una venta nace en el teléfono y llega por `sync/push`. Devolvérsela sería
    mandarle de vuelta lo que acaba de escribir: ancho de banda gastado en un eco y
    una oportunidad de aplicar mal algo que ya estaba bien.
    """
    assert await _ultimo_delta(sesion, venta_del_dia["venta"]) is None


async def test_CANCELAR_PUBLICA_LA_VENTA_AL_TELEFONO_DE_SU_VENDEDOR(
    cliente, semilla, venta_del_dia, sesion
):
    """Con sus partidas: el teléfono compara contra las suyas y devuelve al camión la
    diferencia. Y con el motivo, para que el vendedor LEA por qué cambió su venta —
    que cambie sin decirle por qué es la forma más rápida de que deje de confiar."""
    await _entrar(cliente)
    await _cancelar(cliente, venta_del_dia["venta"])

    delta = await _ultimo_delta(sesion, venta_del_dia["venta"])
    assert delta is not None
    assert delta["payload"]["estado"] == "cancelada"
    assert delta["payload"]["cancelacion_motivo"] == MOTIVO
    # Acotado al vendedor: la venta solo le importa a su equipo.
    assert delta["vendedor_id"] == semilla["vendedor"]

    partidas = delta["payload"]["partidas"]
    assert len(partidas) == 1
    # Cantidades como TEXTO con su escala, igual que el resto del contrato.
    assert partidas[0]["cantidad_base"] == "480.000"
    assert partidas[0]["producto_id"] == str(venta_del_dia["producto"])


async def test_corregir_publica_las_partidas_YA_CORREGIDAS(
    cliente, semilla, venta_del_dia, sesion
):
    """El disparador lee las partidas al dispararse, así que el encabezado se
    actualiza DESPUÉS de ellas. Si el orden se invirtiera, el delta viajaría con las
    cantidades viejas y el teléfono devolvería al camión una diferencia de cero."""
    await _entrar(cliente)
    await _corregir(cliente, venta_del_dia["venta"], sesion, "2")

    delta = await _ultimo_delta(sesion, venta_del_dia["venta"])
    assert delta["payload"]["partidas"][0]["cantidad"] == "2.000"
    assert delta["payload"]["partidas"][0]["cantidad_base"] == "48.000"
    assert Decimal(delta["payload"]["total"]) == Decimal("600.00")


async def test_dar_por_revisada_NO_publica_un_delta(
    cliente, semilla, venta_del_dia, sesion
):
    """Bajar una bandera de oficina no le dice nada al teléfono. El disparador tiene
    una condición estrecha justamente para que esto no viaje."""
    await sesion.execute(
        text("UPDATE ventas SET requiere_revision = true WHERE id = :v"),
        {"v": venta_del_dia["venta"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/ventas/{venta_del_dia['venta']}")
    await cliente.post(
        f"/panel/ventas/{venta_del_dia['venta']}/revisada",
        data={"csrf": _csrf(cliente, detalle)},
        follow_redirects=True,
    )

    assert await _ultimo_delta(sesion, venta_del_dia["venta"]) is None
