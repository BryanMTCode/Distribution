"""La foto del camión que el teléfono usa para cuadrarse (`GET /v1/sync/camion`).

Lo reportó la operación en octubre de 2026: el teléfono decía 1 Maruchan y el
panel 0; la oficina sumó 5 y el teléfono pasó a 6. Los ajustes viajan como
diferencia y arrastran cualquier error previo. La foto es el estado, y el
teléfono la aplica solo cuando no hay nada en vuelo (ver `cuadrarCamion` en
Dart). Lo que aquí se defiende es que la foto diga la verdad sobre DESDE DÓNDE:
su cursor tiene que ser exactamente el que el pull deja al teléfono cuando ya
trajo todo, ni uno menos (sumaría dos veces) ni uno más (no se aplicaría nunca).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_alcance_de_ruta import _cabecera, _hasta_el_final

pytestmark = pytest.mark.asyncio


async def _producto(sesion, sku: str) -> uuid.UUID:
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, creado_en, actualizado_en) "
            "VALUES (:id, :sku, :sku, 'PZA', now(), now())"
        ),
        {"id": producto, "sku": sku},
    )
    await sesion.commit()
    return producto


async def _existencia(sesion, almacen, producto, cantidad) -> None:
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) VALUES (:a, :p, :c) "
            "ON CONFLICT (almacen_id, producto_id) DO UPDATE SET cantidad = excluded.cantidad"
        ),
        {"a": almacen, "p": producto, "c": cantidad},
    )
    await sesion.commit()


async def test_la_foto_trae_el_camion_y_el_cursor_que_deja_el_pull(cliente, sesion, semilla):
    maruchan = await _producto(sesion, "MAR-64")
    atun = await _producto(sesion, "ATUN-140")
    await _existencia(sesion, semilla["camion"], maruchan, 5)
    await _existencia(sesion, semilla["camion"], atun, 0)
    # Lo de la bodega no es del camión.
    await _existencia(sesion, semilla["bodega"], atun, 240)

    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    cursor, _ = await _hasta_el_final(cliente, cab)

    foto = (await cliente.get("/v1/sync/camion", headers=cab)).json()
    assert foto["almacen_id"] == str(semilla["camion"])
    assert foto["cursor"] == cursor
    assert foto["cuarentena"] == 0
    # Solo renglones con saldo, en el formato de tres decimales del contrato.
    assert foto["existencias"] == [{"producto_id": str(maruchan), "cantidad": "5.000"}]


async def test_un_ajuste_que_el_telefono_no_ha_traido_mueve_el_cursor(cliente, sesion, semilla):
    """El teléfono compara su cursor contra este: si la foto ya trae un ajuste que
    él no ha aplicado, los cursores no coinciden y no se cuadra."""
    maruchan = await _producto(sesion, "MAR-64")
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    cursor, _ = await _hasta_el_final(cliente, cab)

    await sesion.execute(
        text(
            "INSERT INTO ajustes_camion (folio, almacen_id, producto_id, tipo, "
            "    existencia_al_capturar, contado, delta, nota, usuario_id) "
            "VALUES ('AC-000001', :a, :p, 'conteo', 0, 5, 5, 'conteo de prueba', :u)"
        ),
        {"a": semilla["camion"], "p": maruchan, "u": semilla["admin"]},
    )
    await sesion.commit()

    foto = (await cliente.get("/v1/sync/camion", headers=cab)).json()
    assert foto["cursor"] > cursor
    nuevo, _ = await _hasta_el_final(cliente, cab, cursor)
    assert foto["cursor"] == nuevo


async def test_lo_que_el_servidor_rechazo_de_este_telefono_se_cuenta(cliente, sesion, semilla):
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    dispositivo = (
        await sesion.execute(
            text("SELECT id FROM dispositivos WHERE usuario_id = :u"), {"u": semilla["vendedor"]}
        )
    ).scalar_one()
    await sesion.execute(
        text(
            """
            INSERT INTO sync_cuarentena
              (operacion_id, dispositivo_id, usuario_id, tipo, payload,
               hash_payload, error_codigo, error_mensaje)
            VALUES (gen_random_uuid(), :d, :u, 'venta.crear', '{}'::jsonb,
                    'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
                    'payload_invalido', 'de prueba')
            """
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    assert (await cliente.get("/v1/sync/camion", headers=cab)).json()["cuarentena"] == 1


async def test_sin_camion_no_hay_foto(cliente, sesion, semilla):
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = NULL WHERE id = :u"), {"u": semilla["vendedor"]}
    )
    await sesion.commit()
    assert (await cliente.get("/v1/sync/camion", headers=cab)).status_code == 409


async def test_las_cantidades_con_fraccion_viajan_exactas(cliente, sesion, semilla):
    frijol = await _producto(sesion, "FRI-1K")
    await _existencia(sesion, semilla["camion"], frijol, Decimal("3.5"))
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    foto = (await cliente.get("/v1/sync/camion", headers=cab)).json()
    assert foto["existencias"] == [{"producto_id": str(frijol), "cantidad": "3.500"}]
