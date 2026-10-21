"""La venta a una tienda que el vendedor no «ve», y su cancelación (ADR 0002 §165).

«Hice una venta a un cliente que no me tocaba hoy y lo quise sincronizar y no me
dejó, luego la cancelé y ahora para sincronizarlas las dos me aparecen con
error.» En la cuarentena: «la venta referencia un cliente que no existe» y,
detrás, «la venta a cancelar no existe».

La tienda existía. Con el rol de producción, RLS solo le deja ver al vendedor las
tiendas de sus rutas, y su teléfono puede traer otras: una tienda sin ruta —su
delta va a todos los teléfonos— o una que la oficina pasó a otra ruta mientras él
vendía sin señal. El SELECT del manejador no la encontraba. Las demás pruebas
corren como dueño de las tablas, que salta RLS, y por eso no se veía.

Lo que se defiende:

1. **Por la subida real y con RLS**, la venta y su cancelación a una tienda sin
   ruta entran las dos, y la mercancía vuelve al camión.
2. La de una tienda de otra ruta entra en la ruta del vendedor, **marcada**.
3. **Lo que ya quedó en cuarentena se recupera de una vez**: la cancelación dice
   qué fila reprocesar, y al reprocesar la venta entra también su cancelación.
   Descartar la venta descarta también lo que venía detrás.
4. El formulario de la tienda en el panel ya no le borra la ruta cuando esa ruta
   está inactiva (una de las formas en que una tienda se queda sin ruta).
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.sync.sobres import Operacion
from app.infra.sync.manejadores import MOTIVO_CLIENTE_DE_OTRA_RUTA
from tests.ayudas_sync import a_json, sobre
from tests.conftest import PASSWORD_VENDEDOR
from tests.conftest import csrf_del_panel as _csrf
from tests.test_plan_visita import _entrar
from tests.test_rls import cliente_rls, motor_restringido, rol_api  # noqa: F401
from tests.test_venta_ingesta import RELOJ_DISPOSITIVO, escenario, payload  # noqa: F401

pytestmark = pytest.mark.asyncio

MOTIVO = "Me equivoqué de tienda, era la de al lado"


def _lote(escenario, *, cliente_id=None) -> tuple[dict, uuid.UUID]:  # noqa: F811
    """La venta y, detrás, su cancelación: como las encola el teléfono."""
    venta = uuid.uuid4()
    datos = payload(escenario)
    if cliente_id is not None:
        datos["cliente_id"] = str(cliente_id)
    return (
        a_json([
            sobre(Operacion("venta.crear", venta, datos), secuencia=1),
            sobre(
                Operacion(
                    "venta.cancelar",
                    venta,
                    {"motivo": MOTIVO, "fecha_dispositivo": RELOJ_DISPOSITIVO,
                     "ticket_impreso": True, "reimpresiones": 0},
                ),
                secuencia=2,
            ),
        ]),
        venta,
    )


async def _cab_vendedor(cliente, escenario) -> dict:  # noqa: F811
    """Entra con el teléfono del escenario, como el vendedor."""
    r = await cliente.post(
        "/v1/auth/login",
        json={"codigo": "VEND01", "password": PASSWORD_VENDEDOR,
              "dispositivo_id": str(escenario["dispositivo"])},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _estado(sesion, venta) -> str | None:
    await sesion.rollback()
    return (
        await sesion.execute(text("SELECT estado FROM ventas WHERE id = :v"), {"v": venta})
    ).scalar_one_or_none()


async def _en_camion(sesion, escenario) -> Decimal:  # noqa: F811
    return (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"),
            {"a": escenario["camion"], "p": escenario["producto"]},
        )
    ).scalar_one()


async def test_la_venta_y_su_cancelacion_a_una_tienda_sin_ruta_entran_con_rls(
    cliente_rls, sesion, escenario  # noqa: F811
):
    await sesion.execute(
        text("UPDATE clientes SET ruta_id = NULL WHERE id = :c"), {"c": escenario["cliente"]}
    )
    await sesion.commit()
    cab = await _cab_vendedor(cliente_rls, escenario)
    cuerpo, venta = _lote(escenario)

    r = await cliente_rls.post("/v1/sync/push", json=cuerpo, headers=cab)

    assert r.status_code == 200, r.text
    assert r.json()["aceptadas"] == 2, r.json()["resultados"]
    assert await _estado(sesion, venta) == "cancelada"
    assert await _en_camion(sesion, escenario) == 0, "salió y volvió al camión"


async def test_la_de_una_tienda_de_otra_ruta_entra_marcada_con_rls(
    cliente_rls, sesion, escenario  # noqa: F811
):
    otra = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R77', 'Ruta 77')"), {"r": otra}
    )
    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :r WHERE id = :c"),
        {"r": otra, "c": escenario["cliente"]},
    )
    await sesion.commit()
    cab = await _cab_vendedor(cliente_rls, escenario)
    venta = uuid.uuid4()
    cuerpo = a_json([sobre(Operacion("venta.crear", venta, payload(escenario)))])

    r = await cliente_rls.post("/v1/sync/push", json=cuerpo, headers=cab)

    assert r.json()["aceptadas"] == 1, r.json()["resultados"]
    await sesion.rollback()
    fila = (
        await sesion.execute(
            text("SELECT ruta_id, revision_motivos FROM ventas WHERE id = :v"), {"v": venta}
        )
    ).mappings().one()
    assert fila["ruta_id"] == escenario["ruta"]
    assert MOTIVO_CLIENTE_DE_OTRA_RUTA in fila["revision_motivos"]


async def _las_dos_en_cuarentena(cliente, sesion, escenario):  # noqa: F811
    """El estado en que quedó el teléfono: la venta y su cancelación rechazadas.

    Se produce con una tienda que el servidor todavía no tiene —la causa que
    queda—, y luego la oficina la da de alta: la corrige.
    """
    tienda = uuid.uuid4()
    cab = await _cab_vendedor(cliente, escenario)
    cuerpo, venta = _lote(escenario, cliente_id=tienda)
    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.json()["rechazadas"] == 2
    filas = {
        f["tipo"]: f
        for f in (
            await sesion.execute(text("SELECT id, tipo, error_mensaje FROM sync_cuarentena"))
        ).mappings()
    }
    return cab, cuerpo, venta, tienda, filas


async def test_la_cancelacion_dice_que_fila_reprocesar(cliente, sesion, escenario):  # noqa: F811
    _, _, _, _, filas = await _las_dos_en_cuarentena(cliente, sesion, escenario)
    assert f"está en cuarentena (#{filas['venta.crear']['id']})" in (
        filas["venta.cancelar"]["error_mensaje"]
    )


async def test_al_reprocesar_la_venta_entra_tambien_su_cancelacion(
    cliente, sesion, escenario  # noqa: F811
):
    cab, cuerpo, venta, tienda, filas = await _las_dos_en_cuarentena(cliente, sesion, escenario)
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
            "actualizado_en) VALUES (:c, 'C00777', 'Abarrotes Lupita', :r, now(), now())"
        ),
        {"c": tienda, "r": escenario["ruta"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    pantalla = await cliente.get(f"/panel/cuarentena/{filas['venta.crear']['id']}")
    r = await cliente.post(
        f"/panel/cuarentena/{filas['venta.crear']['id']}/reprocesar",
        data={"csrf": _csrf(cliente, pantalla)},
        follow_redirects=True,
    )

    assert "Con ella entró también su cancelación" in r.text
    assert await _estado(sesion, venta) == "cancelada"
    assert await _en_camion(sesion, escenario) == 0
    estados = (
        await sesion.execute(text("SELECT DISTINCT estado FROM sync_cuarentena"))
    ).scalars().all()
    assert estados == ["reprocesada"]

    # Y el teléfono toca la barra roja: las dos ya están del otro lado.
    otra = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert [x["estado"] for x in otra.json()["resultados"]] == ["duplicada", "duplicada"]


async def test_si_su_cancelacion_no_puede_entrar_no_entra_ninguna(
    cliente, sesion, escenario  # noqa: F811
):
    """El día ya se liquidó: la cancelación no puede entrar. Si la venta entrara
    sola, quedaría vendida aquí y cancelada en el teléfono: no entra ninguna."""
    _, _, venta, tienda, filas = await _las_dos_en_cuarentena(cliente, sesion, escenario)
    carga = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
            "actualizado_en) VALUES (:c, 'C00777', 'Abarrotes Lupita', :r, now(), now())"
        ),
        {"c": tienda, "r": escenario["ruta"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "  vendedor_id, ruta_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (:id, 'CG-0001', :b, :c, :v, :r, '2026-09-29', 'confirmada', now())"
        ),
        {"id": carga, "b": escenario["bodega"], "c": escenario["camion"],
         "v": escenario["vendedor"], "r": escenario["ruta"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, fecha_operativa, "
            "  estado, cerrada_en, efectivo_esperado, efectivo_entregado) "
            "VALUES (:id, 'LQ-000009', :c, :v, '2026-09-29', 'cerrada', now(), 0, 0)"
        ),
        {"id": uuid.uuid4(), "c": carga, "v": escenario["vendedor"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    pantalla = await cliente.get(f"/panel/cuarentena/{filas['venta.crear']['id']}")
    r = await cliente.post(
        f"/panel/cuarentena/{filas['venta.crear']['id']}/reprocesar",
        data={"csrf": _csrf(cliente, pantalla)},
        follow_redirects=True,
    )

    assert "No entró ninguna" in r.text
    assert await _estado(sesion, venta) is None, "la venta no se quedó sola"
    filas_ahora = {
        f["tipo"]: f
        for f in (
            await sesion.execute(text("SELECT tipo, estado, error_mensaje FROM sync_cuarentena"))
        ).mappings()
    }
    assert {f["estado"] for f in filas_ahora.values()} == {"pendiente"}
    assert "ya se liquidó" in filas_ahora["venta.cancelar"]["error_mensaje"]


async def test_descartar_la_venta_descarta_tambien_su_cancelacion(
    cliente, sesion, escenario  # noqa: F811
):
    _, _, _, _, filas = await _las_dos_en_cuarentena(cliente, sesion, escenario)
    await _entrar(cliente)
    id_venta = filas["venta.crear"]["id"]
    pantalla = await cliente.get(f"/panel/cuarentena/{id_venta}")
    await cliente.post(
        f"/panel/cuarentena/{id_venta}/descartar",
        data={"csrf": _csrf(cliente, pantalla), "nota": "La capturé a mano"},
    )

    await sesion.rollback()
    cancelacion = (
        await sesion.execute(
            text("SELECT estado, nota_resolucion FROM sync_cuarentena WHERE id = :id"),
            {"id": filas["venta.cancelar"]["id"]},
        )
    ).mappings().one()
    assert cancelacion["estado"] == "descartada"
    assert cancelacion["nota_resolucion"] == (
        f"Se descarta con su venta (#{id_venta}): La capturé a mano"
    )


async def test_el_formulario_de_la_tienda_no_le_borra_una_ruta_inactiva(
    cliente, sesion, escenario  # noqa: F811
):
    await sesion.execute(
        text("UPDATE rutas SET activo = false WHERE id = :r"), {"r": escenario["ruta"]}
    )
    await sesion.commit()
    await _entrar(cliente)

    r = await cliente.get(f"/panel/clientes/{escenario['cliente']}")

    formulario = r.text.split('action="/panel/clientes/', 2)[-1]
    assert re.search(rf'value="{escenario["ruta"]}"\s+selected', formulario), (
        "sin su ruta en la lista, el formulario la mandaba como «— sin ruta —»"
    )
