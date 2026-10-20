"""El vendedor le agrega a su venta lo que le faltó, y la pista (ADR 0002 §162).

«Que puedan modificar la venta: si les faltó poner uno de un artículo, que lo
agreguen y puedan imprimir el nuevo ticket. Que quede pista de los cambios, y
cuidado con el inventario para que no puedan aprovecharse.»

1. **Solo agrega:** renglones nuevos, validados como los de `venta.crear`, que
   SALEN DEL CAMIÓN; una cantidad que no es positiva se rechaza.
2. **Mismos bloqueos que cancelar:** cancelada, de otro vendedor o del día
   liquidado.
3. **La pista:** la venta queda marcada, el historial dice qué se agregó, si el
   ticket ya se había impreso y dónde estaba; la cancelación del vendedor también.
4. **El dashboard** lo muestra: en la venta, en «Cambios de los vendedores» y en
   los pendientes del día.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import (
    MOTIVO_AGREGADA_POR_VENDEDOR,
    MOTIVO_CANCELADA_POR_VENDEDOR,
    Contexto,
    ErrorDeManejador,
    obtener_manejador,
)
from tests.ayudas_sync import a_json, sobre
from tests.conftest import PASSWORD_VENDEDOR, solo_texto
from tests.test_operaciones import _contexto
from tests.test_panel_venta_editar import _en_camion, venta_del_dia  # noqa: F401
from tests.test_plan_visita import _entrar

pytestmark = pytest.mark.asyncio

MOTIVO = "Se me olvidó agregarlo"


def _una_caja(producto, **cambios) -> dict:
    partida = {
        "id": str(uuid.uuid4()),
        "linea": 1,  # el teléfono cree que es la 1; el servidor la pone al final
        "producto_id": str(producto),
        "unidad_codigo": "CAJA",
        "factor_unidad": "24.0000",
        "cantidad": "1.000",
        "cantidad_base": "24.000",
        "precio_unitario": "300.0000",
        "descuento": "0.00",
        "importe": "300.00",
    }
    partida.update(cambios)
    return partida


def _datos(venta_del_dia, *partidas, **extra) -> dict:  # noqa: F811
    return {
        "motivo": MOTIVO,
        "fecha_dispositivo": "2026-10-20T17:42:03.250Z",
        "ticket_impreso": True,
        "reimpresiones": 1,
        "partidas": list(partidas) or [_una_caja(venta_del_dia["producto"])],
        **extra,
    }


async def _agregar(sesion, ctx, venta, datos) -> None:
    await obtener_manejador("venta.agregar")(sesion, ctx, venta, datos)


async def _venta(sesion, venta) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT estado, total, subtotal, requiere_revision, revision_motivos "
                    "  FROM ventas WHERE id = :v"
                ),
                {"v": venta},
            )
        ).mappings().one()
    )


async def test_agrega_un_renglon_que_sale_del_camion(sesion, semilla, venta_del_dia):  # noqa: F811
    v = venta_del_dia["venta"]
    await _agregar(sesion, _contexto(semilla, venta_del_dia), v, _datos(venta_del_dia))
    await sesion.commit()

    fila = await _venta(sesion, v)
    assert fila["total"] == Decimal("6300.00")
    assert fila["subtotal"] == Decimal("6300.00")
    assert fila["requiere_revision"] is True
    assert MOTIVO_AGREGADA_POR_VENDEDOR in fila["revision_motivos"]
    lineas = (
        await sesion.execute(
            text("SELECT linea, cantidad FROM venta_partidas WHERE venta_id = :v ORDER BY linea"),
            {"v": v},
        )
    ).all()
    assert [linea for linea, _ in lineas] == [1, 2], "lo de antes no se toca"
    # 120 arriba − 24 de la caja agregada.
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("96")

    # La pista.
    pista = (
        await sesion.execute(
            text(
                "SELECT usuario_id, motivo, datos_antes, datos_despues FROM auditoria "
                " WHERE entidad = 'venta' AND entidad_id = :v AND accion = 'agregar'"
            ),
            {"v": v},
        )
    ).mappings().one()
    assert pista["usuario_id"] == semilla["vendedor"]
    assert pista["motivo"] == MOTIVO
    assert pista["datos_antes"]["total"] == "6000.00"
    assert pista["datos_despues"]["agregado"] == "300.00"
    assert pista["datos_despues"]["ticket_impreso"] is True
    assert pista["datos_despues"]["desde"] == "app"

    # Y el teléfono recibe la venta como quedó, con las dos partidas.
    delta = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log WHERE entidad = 'venta' AND entidad_id = :v "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"v": v},
        )
    ).scalar_one()
    assert delta["total"] in ("6300.00", 6300, 6300.0)
    assert len(delta["partidas"]) == 2


async def test_solo_agrega_lo_que_no_es_positivo_se_rechaza(
    sesion, semilla, venta_del_dia  # noqa: F811
):
    menos = _una_caja(
        venta_del_dia["producto"], cantidad="-1.000", cantidad_base="-24.000", importe="-300.00"
    )
    with pytest.raises(ErrorDeManejador, match="solo se le agrega"):
        await _agregar(
            sesion, _contexto(semilla, venta_del_dia), venta_del_dia["venta"],
            _datos(venta_del_dia, menos),
        )
    await sesion.rollback()
    mal = _una_caja(venta_del_dia["producto"], importe="1.00")
    with pytest.raises(ErrorDeManejador, match="no cuadra"):
        await _agregar(
            sesion, _contexto(semilla, venta_del_dia), venta_del_dia["venta"],
            _datos(venta_del_dia, mal),
        )
    await sesion.rollback()
    assert (await _venta(sesion, venta_del_dia["venta"]))["total"] == Decimal("6000.00")
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120")


async def test_mismos_bloqueos_que_cancelar(sesion, semilla, venta_del_dia):  # noqa: F811
    v = venta_del_dia["venta"]
    otro = Contexto(
        dispositivo_id=venta_del_dia["dispositivo"],
        usuario_id=semilla["admin"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )
    with pytest.raises(ErrorDeManejador, match="otro vendedor"):
        await _agregar(sesion, otro, v, _datos(venta_del_dia))
    await sesion.rollback()

    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, fecha_operativa, "
            "  estado, cerrada_en, efectivo_esperado, efectivo_entregado) "
            "VALUES (:id, 'LQ-000010', :c, :v, :d, 'cerrada', now(), 6000, 6000)"
        ),
        {"id": uuid.uuid4(), "c": venta_del_dia["carga"], "v": semilla["vendedor"],
         "d": venta_del_dia["dia"]},
    )
    await sesion.commit()
    with pytest.raises(ErrorDeManejador, match="ya se liquidó"):
        await _agregar(sesion, _contexto(semilla, venta_del_dia), v, _datos(venta_del_dia))
    await sesion.rollback()

    await sesion.execute(text("UPDATE ventas SET estado = 'cancelada' WHERE id = :v"), {"v": v})
    await sesion.commit()
    with pytest.raises(ErrorDeManejador, match="cancelada"):
        await _agregar(sesion, _contexto(semilla, venta_del_dia), v, _datos(venta_del_dia))
    await sesion.rollback()


async def test_la_cancelacion_del_vendedor_tambien_deja_pista(
    sesion, semilla, venta_del_dia  # noqa: F811
):
    v = venta_del_dia["venta"]
    await obtener_manejador("venta.cancelar")(
        sesion,
        _contexto(semilla, venta_del_dia),
        v,
        {
            "motivo": "Me equivoqué de cliente",
            "fecha_dispositivo": "2026-10-20T17:42:03.250Z",
            "ticket_impreso": False,
            "reimpresiones": 0,
        },
    )
    await sesion.commit()

    fila = await _venta(sesion, v)
    assert fila["requiere_revision"] is True
    assert MOTIVO_CANCELADA_POR_VENDEDOR in fila["revision_motivos"]
    despues = (
        await sesion.execute(
            text(
                "SELECT datos_despues FROM auditoria WHERE entidad = 'venta' "
                "   AND entidad_id = :v AND accion = 'cancelar'"
            ),
            {"v": v},
        )
    ).scalar_one()
    assert despues["desde"] == "app"
    assert despues["ticket_impreso"] is False
    assert despues["lat"] is None


async def test_el_dashboard_muestra_la_pista(cliente, sesion, semilla, venta_del_dia):  # noqa: F811
    v = venta_del_dia["venta"]
    await _agregar(sesion, _contexto(semilla, venta_del_dia), v, _datos(venta_del_dia))
    await sesion.commit()
    await _entrar(cliente)

    detalle = solo_texto(await cliente.get(f"/panel/ventas/{v}"))
    assert "Historial de cambios" in detalle
    assert "Agregó artículos: 1 CAJA Atún en agua 140 g" in detalle
    assert "con el ticket ya impreso (1 copia)" in detalle
    assert "«Se me olvidó agregarlo»" in detalle
    assert "El vendedor le agregó artículos" in detalle or "agregada_por_vendedor" in detalle

    r = await cliente.get("/panel/ventas/cambios?dias=1")
    assert r.status_code == 200
    html = r.text
    assert 'id="tabla_cambios_vendedores"' in html
    assert 'class="fila-alerta"' in html, "el que se hizo con el ticket impreso, a la vista"
    assert "VEND01-000001" in html
    assert 'id="cambios_con_ticket"' in html

    tablero = solo_texto(await cliente.get("/panel"))
    assert "cambio(s) de los vendedores" in tablero


async def test_sin_cambios_la_lista_lo_dice(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/ventas/cambios")
    assert r.status_code == 200
    assert 'id="sin_cambios"' in r.text


async def test_por_la_cola_real(cliente, sesion, semilla, venta_del_dia):  # noqa: F811
    from app.domain.sync.sobres import Operacion

    r = await cliente.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND01",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(venta_del_dia["dispositivo"]),
        },
    )
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    cuerpo = a_json([
        sobre(
            Operacion(
                tipo="venta.agregar",
                entidad_id=venta_del_dia["venta"],
                datos=_datos(venta_del_dia),
            )
        )
    ])
    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["aceptadas"] == 1, r.json()
    await sesion.rollback()
    assert (await _venta(sesion, venta_del_dia["venta"]))["total"] == Decimal("6300.00")
