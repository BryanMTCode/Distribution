"""Que el teléfono se entere de que la base se puso en blanco (ADR 0002 §90).

Reporte de la dirección (octubre 2026): «aparecen tiendas en la app que se
borraron, y en el dashboard no están, como debe de ser». La base en blanco no
publica bajas, así que el teléfono nunca se enteraba.

1. **El teléfono marcado vuelve a empezar**: su pull le pide resincronizar y
   olvidar lo de antes, y se lo sigue pidiendo hasta que pide desde el cursor 0
   —la prueba de que ya empezó de cero—. Ahí la marca se apaga sola.
2. **La migración marca los teléfonos que ya existían** cuando la base se puso
   en blanco antes de esta versión, y no los que se vincularon después.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from tests.test_sync_pull import _alta_producto, _cab_vendedor

pytestmark = pytest.mark.asyncio


async def _marca(sesion, dispositivo: uuid.UUID | None = None) -> list[bool]:
    filas = (
        await sesion.execute(
            text("SELECT empezar_de_cero FROM dispositivos "
                 " WHERE CAST(:d AS uuid) IS NULL OR id = :d ORDER BY registrado_en"),
            {"d": dispositivo},
        )
    ).scalars().all()
    return list(filas)


async def test_el_telefono_marcado_vuelve_a_empezar_y_la_marca_se_apaga_sola(
    cliente, semilla, sesion
):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "A")
    cursor = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()["cursor"]
    assert cursor > 0

    # La base se puso en blanco: el teléfono queda marcado.
    await sesion.execute(text("UPDATE dispositivos SET empezar_de_cero = true"))
    await sesion.commit()

    for _ in range(2):  # contestar no apaga la marca: la respuesta se puede perder
        r = (await cliente.get(f"/v1/sync/pull?cursor={cursor}", headers=cab)).json()
        assert r["resincronizar"] is True
        assert r["base_en_blanco"] is True
        assert (r["cursor"], r["cambios"]) == (0, [])
    assert await _marca(sesion) == [True]

    # Pide desde 0: ya empezó de cero, recibe todo y la marca se apaga.
    desde_cero = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()
    assert desde_cero["resincronizar"] is False
    assert "producto" in {c["entidad"] for c in desde_cero["cambios"]}
    assert await _marca(sesion) == [False]

    # Y de ahí en adelante sincroniza normal.
    await _alta_producto(sesion, "B")
    r = (
        await cliente.get(f"/v1/sync/pull?cursor={desde_cero['cursor']}", headers=cab)
    ).json()
    assert r["resincronizar"] is False and r["base_en_blanco"] is False
    assert [c["payload"]["sku"] for c in r["cambios"]] == ["B"]


async def test_sin_marca_nadie_vuelve_a_empezar(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await _alta_producto(sesion, "A")
    cursor = (await cliente.get("/v1/sync/pull?cursor=0", headers=cab)).json()["cursor"]
    r = (await cliente.get(f"/v1/sync/pull?cursor={cursor}", headers=cab)).json()
    assert r["resincronizar"] is False and r["base_en_blanco"] is False


async def test_la_migracion_marca_los_telefonos_de_antes_de_la_base_en_blanco(
    sesion, semilla
):
    """La base de la dirección ya se puso en blanco antes de esta versión: sus
    teléfonos tienen que limpiarse sin volver a borrar nada en el servidor."""
    from db.sql import leer_sql

    antes, despues = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) VALUES "
            "(:a, :v, 'De antes', 'suspendido', now() - interval '2 days'), "
            "(:d, :v, 'De después', 'activo', now())"
        ),
        {"a": antes, "d": despues, "v": semilla["vendedor"]},
    )
    await sesion.execute(
        text("INSERT INTO auditoria (entidad, entidad_id, accion, motivo, ocurrido_en) "
             "VALUES ('base', gen_random_uuid(), 'en_blanco', 'prueba', "
             "        now() - interval '1 day')")
    )
    actualizar = re.search(
        r"UPDATE dispositivos.*?;", leer_sql("0052_telefono_desde_cero.sql"), re.S
    ).group(0)
    await sesion.execute(text(actualizar))
    assert await _marca(sesion, antes) == [True]
    assert await _marca(sesion, despues) == [False]


async def test_sin_base_en_blanco_la_migracion_no_marca_a_nadie(sesion, semilla):
    from db.sql import leer_sql

    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (gen_random_uuid(), :v, 'Uno', 'activo', now() - interval '9 days')"),
        {"v": semilla["vendedor"]},
    )
    actualizar = re.search(
        r"UPDATE dispositivos.*?;", leer_sql("0052_telefono_desde_cero.sql"), re.S
    ).group(0)
    await sesion.execute(text(actualizar))
    assert await _marca(sesion) == [False]
