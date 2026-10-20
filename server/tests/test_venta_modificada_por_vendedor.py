"""El vendedor modifica su venta —sube, baja o quita—, y la pista (ADR 0002 §162).

«Que puedan quitar artículos de la venta, pero que se ajuste todo y el
inventario se devuelva a su camión; que puedan bajar la cantidad de un artículo
y subirla. Que quede pista de los cambios.»

1. **La venta como debe quedar:** el renglón que ya estaba solo cambia de
   cantidad (no de producto ni de precio), el nuevo se valida como en
   `venta.crear`, el que no viene se quita. Lo que sube sale del camión y lo que
   baja o se quita vuelve, con su movimiento.
2. **Lo que no es modificar:** quitarlo todo (es cancelar), cantidades en cero o
   negativas, cambiarle el precio a un renglón.
3. **Mismos bloqueos que cancelar:** cancelada, de otro vendedor o del día
   liquidado.
4. **La pista**, en el historial, en «Cambios de los vendedores» y en
   Pendientes; también la de la cancelación del vendedor.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import (
    MOTIVO_CANCELADA_POR_VENDEDOR,
    MOTIVO_MODIFICADA_POR_VENDEDOR,
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

MOTIVO = "Me equivoqué de cantidad"


async def _partida(sesion, venta) -> dict:
    return dict(
        (
            await sesion.execute(
                text("SELECT id, producto_id FROM venta_partidas WHERE venta_id = :v"),
                {"v": venta},
            )
        )
        .mappings()
        .one()
    )


def _cajas(partida_id, producto, cajas: int, *, precio="300.0000") -> dict:
    """Un renglón de cajas de atún (24 piezas, $300)."""
    return {
        "id": str(partida_id),
        "producto_id": str(producto),
        "unidad_codigo": "CAJA",
        "factor_unidad": "24.0000",
        "cantidad": f"{cajas}.000",
        "cantidad_base": f"{cajas * 24}.000",
        "precio_unitario": precio,
        "descuento": "0.00",
        "importe": f"{cajas * 300}.00",
    }


def _piezas(producto, piezas: int) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "linea": 1,  # el teléfono cree que es la 1; el servidor la pone al final
        "producto_id": str(producto),
        "unidad_codigo": "PZA",
        "factor_unidad": "1.0000",
        "cantidad": f"{piezas}.000",
        "cantidad_base": f"{piezas}.000",
        "precio_unitario": "13.0000",
        "descuento": "0.00",
        "importe": f"{piezas * 13}.00",
    }


def _datos(venta, *partidas, **extra) -> dict:
    return {
        "venta_id": str(venta),
        "motivo": MOTIVO,
        "fecha_dispositivo": "2026-10-20T17:42:03.250Z",
        "ticket_impreso": True,
        "reimpresiones": 1,
        "partidas": list(partidas),
        **extra,
    }


async def _modificar(sesion, ctx, datos) -> None:
    # La entidad es la MODIFICACIÓN: una venta se puede modificar varias veces.
    await obtener_manejador("venta.modificar")(sesion, ctx, uuid.uuid4(), datos)


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
        )
        .mappings()
        .one()
    )


async def _pista(sesion, venta, accion="modificar") -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT usuario_id, motivo, datos_antes, datos_despues FROM auditoria "
                    " WHERE entidad = 'venta' AND entidad_id = :v AND accion = :a "
                    " ORDER BY id DESC LIMIT 1"
                ),
                {"v": venta, "a": accion},
            )
        )
        .mappings()
        .one()
    )


async def test_bajar_devuelve_al_camion(sesion, semilla, venta_del_dia):  # noqa: F811
    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    await _modificar(
        sesion, _contexto(semilla, venta_del_dia), _datos(v, _cajas(p["id"], producto, 15))
    )
    await sesion.commit()

    fila = await _venta(sesion, v)
    assert fila["total"] == Decimal("4500.00")
    assert fila["requiere_revision"] is True
    assert MOTIVO_MODIFICADA_POR_VENDEDOR in fila["revision_motivos"]
    # 120 arriba + las 5 cajas (120 piezas) que ya no se llevó.
    assert await _en_camion(sesion, semilla, producto) == Decimal("240")
    movimiento = (
        await sesion.execute(
            text(
                "SELECT tipo, cantidad FROM movimientos_inventario "
                " WHERE documento_tipo = 'venta_modificada' AND documento_id = :v"
            ),
            {"v": v},
        )
    ).one()
    assert movimiento.tipo == "devolucion" and movimiento.cantidad == Decimal("120")

    pista = await _pista(sesion, v)
    assert pista["usuario_id"] == semilla["vendedor"]
    assert pista["datos_antes"]["total"] == "6000.00"
    cambio = pista["datos_despues"]["cambios"][0]
    assert (cambio["antes"], cambio["cantidad"]) == ("20.000", "15.000")
    assert pista["datos_despues"]["ticket_impreso"] is True

    # Y el teléfono recibe la venta como quedó.
    delta = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log WHERE entidad = 'venta' AND entidad_id = :v "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"v": v},
        )
    ).scalar_one()
    assert len(delta["partidas"]) == 1
    assert Decimal(str(delta["partidas"][0]["cantidad"])) == Decimal("15")


async def test_subir_y_agregar_otro_sale_del_camion(sesion, semilla, venta_del_dia):  # noqa: F811
    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    # Hay 120 piezas arriba: 21 cajas (+24) y 10 piezas sueltas (+10).
    await _modificar(
        sesion,
        _contexto(semilla, venta_del_dia),
        _datos(v, _cajas(p["id"], producto, 21), _piezas(producto, 10)),
    )
    await sesion.commit()

    assert (await _venta(sesion, v))["total"] == Decimal("6430.00")
    assert await _en_camion(sesion, semilla, producto) == Decimal("86")
    lineas = (
        await sesion.execute(
            text(
                "SELECT linea, unidad_codigo FROM venta_partidas WHERE venta_id = :v ORDER BY linea"
            ),
            {"v": v},
        )
    ).all()
    assert [(linea, u) for linea, u in lineas] == [(1, "CAJA"), (2, "PZA")]


async def test_quitar_un_renglon_lo_devuelve_completo(sesion, semilla, venta_del_dia):  # noqa: F811
    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    ctx = _contexto(semilla, venta_del_dia)
    piezas = _piezas(producto, 10)
    await _modificar(sesion, ctx, _datos(v, _cajas(p["id"], producto, 20), piezas))
    await sesion.commit()
    assert await _en_camion(sesion, semilla, producto) == Decimal("110")

    # Segunda modificación de la misma venta: se quitan las piezas.
    await _modificar(
        sesion, ctx, _datos(v, _cajas(p["id"], producto, 20), motivo="Ya no quiso las piezas")
    )
    await sesion.commit()

    assert await _en_camion(sesion, semilla, producto) == Decimal("120")
    assert (await _venta(sesion, v))["total"] == Decimal("6000.00")
    quitado = (await _pista(sesion, v))["datos_despues"]["cambios"][0]
    assert quitado["quitado"] is True and quitado["unidad"] == "PZA"


async def test_lo_que_no_es_modificar_se_rechaza(sesion, semilla, venta_del_dia):  # noqa: F811
    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    ctx = _contexto(semilla, venta_del_dia)
    casos = [
        ("se cancela", _datos(v)),  # quitarlo todo
        ("negativa", _datos(v, {**_cajas(p["id"], producto, 1), "cantidad": "-1.000"})),
        ("en cero", _datos(v, {**_cajas(p["id"], producto, 1), "cantidad": "0.000"})),
        ("ni de precio", _datos(v, _cajas(p["id"], producto, 20, precio="1.0000"))),
        ("no cuadra", _datos(v, {**_cajas(p["id"], producto, 20), "importe": "1.00"})),
    ]
    for texto_del_error, datos in casos:
        with pytest.raises(ErrorDeManejador, match=texto_del_error):
            await _modificar(sesion, ctx, datos)
        await sesion.rollback()
    assert (await _venta(sesion, v))["total"] == Decimal("6000.00")
    assert await _en_camion(sesion, semilla, producto) == Decimal("120")


async def test_mismos_bloqueos_que_cancelar(sesion, semilla, venta_del_dia):  # noqa: F811
    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    datos = _datos(v, _cajas(p["id"], producto, 15))
    otro = Contexto(
        dispositivo_id=venta_del_dia["dispositivo"],
        usuario_id=semilla["admin"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )
    with pytest.raises(ErrorDeManejador, match="otro vendedor"):
        await _modificar(sesion, otro, datos)
    await sesion.rollback()

    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, fecha_operativa, "
            "  estado, cerrada_en, efectivo_esperado, efectivo_entregado) "
            "VALUES (:id, 'LQ-000010', :c, :v, :d, 'cerrada', now(), 6000, 6000)"
        ),
        {
            "id": uuid.uuid4(),
            "c": venta_del_dia["carga"],
            "v": semilla["vendedor"],
            "d": venta_del_dia["dia"],
        },
    )
    await sesion.commit()
    with pytest.raises(ErrorDeManejador, match="ya se liquidó"):
        await _modificar(sesion, _contexto(semilla, venta_del_dia), datos)
    await sesion.rollback()

    await sesion.execute(text("UPDATE ventas SET estado = 'cancelada' WHERE id = :v"), {"v": v})
    await sesion.commit()
    with pytest.raises(ErrorDeManejador, match="cancelada"):
        await _modificar(sesion, _contexto(semilla, venta_del_dia), datos)
    await sesion.rollback()


async def test_sin_cambios_no_deja_pista(sesion, semilla, venta_del_dia):  # noqa: F811
    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    await _modificar(
        sesion, _contexto(semilla, venta_del_dia), _datos(v, _cajas(p["id"], producto, 20))
    )
    await sesion.commit()
    assert (await _venta(sesion, v))["requiere_revision"] is False
    cuantas = (
        await sesion.execute(text("SELECT count(*) FROM auditoria WHERE entidad_id = :v"), {"v": v})
    ).scalar_one()
    assert cuantas == 0


async def test_la_cancelacion_del_vendedor_tambien_deja_pista(
    sesion,
    semilla,
    venta_del_dia,  # noqa: F811
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
    despues = (await _pista(sesion, v, "cancelar"))["datos_despues"]
    assert despues["desde"] == "app"
    assert despues["ticket_impreso"] is False
    assert despues["lat"] is None


async def test_el_dashboard_muestra_la_pista(cliente, sesion, semilla, venta_del_dia):  # noqa: F811
    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    await _modificar(
        sesion,
        _contexto(semilla, venta_del_dia),
        _datos(v, _cajas(p["id"], producto, 15), _piezas(producto, 10)),
    )
    await sesion.commit()
    await _entrar(cliente)

    detalle = solo_texto(await cliente.get(f"/panel/ventas/{v}"))
    assert "Historial de cambios" in detalle
    assert "Modificó la venta: Atún en agua 140 g 20 → 15 CAJA" in detalle
    assert "Atún en agua 140 g: se agregó 10 PZA" in detalle
    assert "total $6,000.00 → $4,630.00" in detalle
    assert "con el ticket ya impreso (1 copia)" in detalle
    assert "«Me equivoqué de cantidad»" in detalle

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

    v, producto = venta_del_dia["venta"], venta_del_dia["producto"]
    p = await _partida(sesion, v)
    r = await cliente.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND01",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(venta_del_dia["dispositivo"]),
        },
    )
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    cuerpo = a_json(
        [
            sobre(
                Operacion(
                    tipo="venta.modificar",
                    entidad_id=uuid.uuid4(),
                    datos=_datos(v, _cajas(p["id"], producto, 15)),
                )
            )
        ]
    )
    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["aceptadas"] == 1, r.json()
    await sesion.rollback()
    assert (await _venta(sesion, v))["total"] == Decimal("4500.00")
