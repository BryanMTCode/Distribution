"""Eliminar un cliente desde el panel, y lo que le llega al teléfono.

────────────────────────────────────────────────────────────────────────────
UN BOTÓN, TRES DESENLACES
────────────────────────────────────────────────────────────────────────────
La dirección pidió que «Eliminar» simplemente funcione. Funciona, y decide qué es
seguro:

1. **Sin documentos se borra.** El disparador publica un `delete` a la ruta del
   cliente, y el teléfono lo aplica como baja local sin tocar nada más (lo prueba
   `blindaje_sync_test.dart`: la baja no mata la tanda ni borra la venta sin
   subir).
2. **Con historia se da de baja**, porque borrarlo de verdad rompería sus ventas.
   Para quien usa el panel el efecto es el mismo: sale de las listas y de los
   teléfonos.
3. **Si debe, no se toca.** Ocultarlo del teléfono dejaría al vendedor sin poder
   cobrarle.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, solo_texto

pytestmark = pytest.mark.asyncio


async def _entrar(cliente) -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(cliente) -> str:
    import hashlib
    import hmac

    from app.core.config import obtener_config

    cookie = cliente.cookies.get("dsd_panel", "")
    return hmac.new(
        obtener_config().jwt_secreto.encode(), f"csrf:{cookie}".encode(), hashlib.sha256
    ).hexdigest()


@pytest.fixture
async def cliente_de_ruta(sesion, semilla) -> uuid.UUID:
    cid = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, estatus, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'C00077', 'Abarrotes La Esperanza', :r, 'activo', now(), now())"
        ),
        {"c": cid, "r": semilla["ruta"]},
    )
    await sesion.commit()
    return cid


async def _eliminar(cliente, cid, *, confirmo=True):
    datos = {"csrf": _csrf(cliente)}
    if confirmo:
        datos["confirmo"] = "1"
    return await cliente.post(
        f"/panel/clientes/{cid}/eliminar", data=datos, follow_redirects=False
    )


async def _venta(sesion, semilla, cid, *, tipo="contado", total="150.00"):
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    venta = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :a, :tipo,
                    :total, :total, now(), :dia)
            """
        ),
        {
            "v": venta, "d": dispositivo, "c": cid, "u": semilla["vendedor"],
            "a": semilla["camion"], "tipo": tipo, "total": total, "dia": date.today(),
        },
    )
    if tipo == "credito":
        await sesion.execute(
            text(
                "INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original, "
                "  importe_pagado, fecha_emision, fecha_vencimiento, estado) "
                "VALUES (:v, :c, :t, 0, CURRENT_DATE, CURRENT_DATE + 7, 'abierta')"
            ),
            {"v": venta, "c": cid, "t": total},
        )
    await sesion.commit()
    return venta


async def _ultimo_delta(sesion, cid):
    return (
        await sesion.execute(
            text(
                "SELECT operacion, ruta_id, payload FROM change_log "
                " WHERE entidad = 'cliente' AND entidad_id = :c "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"c": cid},
        )
    ).mappings().first()


async def test_sin_documentos_se_borra_y_el_telefono_recibe_el_delete(
    cliente, sesion, semilla, cliente_de_ruta
):
    await _entrar(cliente)
    r = await _eliminar(cliente, cliente_de_ruta)
    assert r.status_code == 303

    existe = (
        await sesion.execute(
            text("SELECT count(*) FROM clientes WHERE id = :c"), {"c": cliente_de_ruta}
        )
    ).scalar_one()
    assert existe == 0

    delta = await _ultimo_delta(sesion, cliente_de_ruta)
    assert delta["operacion"] == "delete"
    # A la ruta del cliente, que es donde están los teléfonos que lo tienen.
    assert delta["ruta_id"] == semilla["ruta"]


async def test_con_historia_se_da_de_baja_y_la_venta_queda_intacta(
    cliente, sesion, semilla, cliente_de_ruta
):
    venta = await _venta(sesion, semilla, cliente_de_ruta)
    await _entrar(cliente)
    r = await _eliminar(cliente, cliente_de_ruta)
    mensaje = solo_texto(await cliente.get(r.headers["location"]))

    estatus = (
        await sesion.execute(
            text("SELECT estatus FROM clientes WHERE id = :c"), {"c": cliente_de_ruta}
        )
    ).scalar_one()
    assert estatus == "baja"
    assert "se dio de baja" in mensaje
    assert "1 venta(s)" in mensaje
    # La venta es un papel que alguien tiene en la mano.
    assert (
        await sesion.execute(text("SELECT count(*) FROM ventas WHERE id = :v"), {"v": venta})
    ).scalar_one() == 1

    # Y al teléfono le llega como baja: el payload dice el estatus nuevo.
    delta = await _ultimo_delta(sesion, cliente_de_ruta)
    assert delta["operacion"] == "upsert"
    assert delta["payload"]["estatus"] == "baja"


async def test_si_todavia_debe_no_se_elimina(cliente, sesion, semilla, cliente_de_ruta):
    """Ocultarlo del teléfono dejaría al vendedor sin poder cobrarle."""
    await _venta(sesion, semilla, cliente_de_ruta, tipo="credito", total="820.00")
    await _entrar(cliente)
    r = await _eliminar(cliente, cliente_de_ruta)
    pagina = solo_texto(await cliente.get(r.headers["location"]))

    assert "todavía debe $820.00" in pagina
    estatus = (
        await sesion.execute(
            text("SELECT estatus FROM clientes WHERE id = :c"), {"c": cliente_de_ruta}
        )
    ).scalar_one()
    assert estatus == "activo"


async def test_sin_la_casilla_no_se_elimina(cliente, sesion, semilla, cliente_de_ruta):
    await _entrar(cliente)
    await _eliminar(cliente, cliente_de_ruta, confirmo=False)
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM clientes WHERE id = :c"), {"c": cliente_de_ruta}
        )
    ).scalar_one() == 1


async def test_el_dado_de_baja_ya_no_aparece_entre_los_clientes(
    cliente, sesion, semilla, cliente_de_ruta
):
    await _venta(sesion, semilla, cliente_de_ruta)
    await _entrar(cliente)
    assert "Abarrotes La Esperanza" in solo_texto(
        await cliente.get("/panel/clientes?filtro=todos")
    )
    await _eliminar(cliente, cliente_de_ruta)

    assert "Abarrotes La Esperanza" not in solo_texto(
        await cliente.get("/panel/clientes?filtro=todos&q=Esperanza")
    )
    # Sigue localizable donde se buscan los inactivos, para reactivarlo.
    assert "Abarrotes La Esperanza" in solo_texto(
        await cliente.get("/panel/clientes?filtro=inactivos")
    )


async def test_la_tabla_trae_editar_y_eliminar(cliente, semilla, cliente_de_ruta):
    await _entrar(cliente)
    html = (await cliente.get("/panel/clientes?filtro=todos")).text
    assert f'href="/panel/clientes/{cliente_de_ruta}#datos">Editar' in html
    assert f'href="/panel/clientes/{cliente_de_ruta}#eliminar">Eliminar' in html
