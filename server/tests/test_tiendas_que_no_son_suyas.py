"""Las tiendas que no son del vendedor se van de su teléfono (ADR 0002 §174).

«Me aparecen 90 y tantas para el que trae la ruta 1107», y el jueves de la 1107
son 47. El teléfono solo suelta una tienda cuando el servidor le manda la baja, y
había tres caminos por donde no llegaba nunca: la tienda sin ruta que recibe una,
el cliente de mayoreo (sin ruta, le llegaba a todos) y el teléfono que se vincula
de nuevo con lo de su vendedor anterior.

Se prueba con el pull de verdad: la baja tiene que LLEGAR al teléfono, no solo
existir en el `change_log`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.test_blindaje_sync import _cab_vendedor

pytestmark = pytest.mark.asyncio


async def _tienda(sesion, *, ruta: uuid.UUID | None, mayoreo: bool = False) -> uuid.UUID:
    tienda = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, mayoreo, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, :codigo, 'Abarrotes Lupita', :r, :m, now(), now())"
        ),
        {"c": tienda, "codigo": f"T-{tienda.hex[:6]}", "r": ruta, "m": mayoreo},
    )
    return tienda


async def _otra_ruta(sesion, codigo: str = "R1102") -> uuid.UUID:
    ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, :c, 'Otra')"),
        {"r": ruta, "c": codigo},
    )
    return ruta


async def _bajar(cliente, cabeceras, cursor: int = 0) -> tuple[list[dict], int]:
    """Todo lo que el teléfono baja desde ese cursor, página tras página."""
    cambios: list[dict] = []
    while True:
        r = await cliente.get(f"/v1/sync/pull?cursor={cursor}", headers=cabeceras)
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        cambios += cuerpo["cambios"]
        cursor = cuerpo["cursor"]
        if not cuerpo["hay_mas"]:
            return cambios, cursor


def _lo_ultimo(cambios: list[dict], tienda: uuid.UUID) -> str | None:
    """Cómo queda la tienda en el teléfono: lo que dice su último delta."""
    suyos = [c for c in cambios if c["entidad"] == "cliente" and c["entidad_id"] == str(tienda)]
    return suyos[-1]["operacion"] if suyos else None


async def test_la_tienda_sin_ruta_que_recibe_otra_ruta_se_va_del_telefono(
    cliente, sesion, semilla
):
    """El caso de Luis: su teléfono la enseñaba como suya y era de la R1102."""
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    otra = await _otra_ruta(sesion)
    tienda = await _tienda(sesion, ruta=None)
    await sesion.commit()

    cambios, cursor = await _bajar(cliente, cabeceras)
    assert _lo_ultimo(cambios, tienda) == "upsert", "sin ruta le llega a todos"

    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :r WHERE id = :c"), {"r": otra, "c": tienda}
    )
    await sesion.commit()

    cambios, _ = await _bajar(cliente, cabeceras, cursor)
    assert _lo_ultimo(cambios, tienda) == "delete", (
        "la tienda pasó a la R1102 y el teléfono de la R04 se quedó con ella"
    )


async def test_el_telefono_de_la_ruta_que_la_recibe_se_queda_con_ella(
    cliente, sesion, semilla
):
    """La baja va a todos, también al de la ruta nueva: el alta tiene que venir detrás."""
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    tienda = await _tienda(sesion, ruta=None)
    await sesion.commit()
    _, cursor = await _bajar(cliente, cabeceras)

    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :r WHERE id = :c"),
        {"r": semilla["ruta"], "c": tienda},
    )
    await sesion.commit()

    cambios, _ = await _bajar(cliente, cabeceras, cursor)
    suyos = [c["operacion"] for c in cambios if c["entidad_id"] == str(tienda)
             and c["entidad"] == "cliente"]
    assert suyos == ["delete", "upsert"]


async def test_el_cliente_de_mayoreo_no_le_llega_a_ningun_telefono(cliente, sesion, semilla):
    """Es de la oficina. Sin ruta salía en «Todos» de cada vendedor."""
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    tienda = await _tienda(sesion, ruta=None, mayoreo=True)
    await sesion.commit()

    cambios, cursor = await _bajar(cliente, cabeceras)
    assert _lo_ultimo(cambios, tienda) == "delete"
    assert not any(
        c["entidad_id"] == str(tienda) and c["operacion"] == "upsert" for c in cambios
    ), "el teléfono recibió al cliente de mayoreo"

    # Y el que ya era cliente de ruta y pasa a mayoreo, se va.
    normal = await _tienda(sesion, ruta=None)
    await sesion.commit()
    _, cursor = await _bajar(cliente, cabeceras, cursor)
    await sesion.execute(
        text("UPDATE clientes SET mayoreo = true WHERE id = :c"), {"c": normal}
    )
    await sesion.commit()
    cambios, _ = await _bajar(cliente, cabeceras, cursor)
    assert _lo_ultimo(cambios, normal) == "delete"


async def test_el_telefono_que_se_registra_suelta_lo_que_no_es_suyo(
    cliente, sesion, semilla
):
    """El teléfono vinculado de nuevo, con las tiendas de su vendedor anterior."""
    otra = await _otra_ruta(sesion)
    ajena = await _tienda(sesion, ruta=otra)
    suya = await _tienda(sesion, ruta=semilla["ruta"])
    sin_ruta = await _tienda(sesion, ruta=None)
    mayoreo = await _tienda(sesion, ruta=None, mayoreo=True)
    borrada = await _tienda(sesion, ruta=otra)
    await sesion.commit()
    await sesion.execute(text("DELETE FROM clientes WHERE id = :c"), {"c": borrada})
    await sesion.commit()
    desde = (
        await sesion.execute(text("SELECT COALESCE(max(cursor), 0) FROM change_log"))
    ).scalar_one()

    # Así lo hace la oficina: Teléfonos → Registrar. El teléfono ya iba en `desde`.
    cabeceras, _ = await _cab_vendedor(cliente, sesion, semilla)
    cambios, _ = await _bajar(cliente, cabeceras, desde)

    assert _lo_ultimo(cambios, ajena) == "delete"
    assert _lo_ultimo(cambios, mayoreo) == "delete"
    assert _lo_ultimo(cambios, borrada) == "delete"
    # Lo suyo no se toca: ni su ruta ni la que no tiene ruta.
    assert _lo_ultimo(cambios, suya) is None
    assert _lo_ultimo(cambios, sin_ruta) is None


async def test_el_telefono_que_pasa_a_otro_vendedor_suelta_las_del_anterior(
    cliente, sesion, semilla
):
    otra = await _otra_ruta(sesion)
    ajena = await _tienda(sesion, ruta=otra)
    suya = await _tienda(sesion, ruta=semilla["ruta"])
    otro = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo) "
            "SELECT :o, sucursal_id, 'VEND77', 'Otro', password_hash, rol_codigo "
            "  FROM usuarios WHERE id = :v"
        ),
        {"o": otro, "v": semilla["vendedor"]},
    )
    await sesion.execute(
        text("INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:o, :r)"),
        {"o": otro, "r": otra},
    )
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado) "
             "VALUES (:d, :o, 'Moto G54', 'activo')"),
        {"d": dispositivo, "o": otro},
    )
    await sesion.commit()
    desde = (
        await sesion.execute(text("SELECT max(cursor) FROM change_log"))
    ).scalar_one()

    await sesion.execute(
        text("UPDATE dispositivos SET usuario_id = :v WHERE id = :d"),
        {"v": semilla["vendedor"], "d": dispositivo},
    )
    await sesion.commit()

    bajas = set(
        (
            await sesion.execute(
                text("SELECT entidad_id FROM change_log WHERE cursor > :d "
                     "   AND entidad = 'cliente' AND operacion = 'delete' "
                     "   AND vendedor_id = :v"),
                {"d": desde, "v": semilla["vendedor"]},
            )
        ).scalars()
    )
    assert ajena in bajas
    assert suya not in bajas


async def test_la_limpieza_de_una_vez_dice_cuantas_bajas_mando(sesion, semilla):
    """La que corre la migración para los teléfonos que ya traen tiendas de más."""
    otra = await _otra_ruta(sesion)
    for _ in range(3):
        await _tienda(sesion, ruta=otra)
    await _tienda(sesion, ruta=semilla["ruta"])
    await _tienda(sesion, ruta=None)
    await sesion.commit()

    bajas = (
        await sesion.execute(
            text("SELECT fn_soltar_tiendas_ajenas(:v)"), {"v": semilla["vendedor"]}
        )
    ).scalar_one()
    assert bajas == 3

