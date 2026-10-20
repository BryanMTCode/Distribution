"""El vendedor cancela su venta desde el teléfono (ADR 0002 §161).

«Quiero que los vendedores puedan cancelar ventas por si se equivocan.» Viaja por
la cola como `venta.cancelar`, detrás de su `venta.crear`, y el servidor la
cancela **igual que el panel**: con su motivo, a nombre del vendedor, con la
mercancía de vuelta al camión y el delta para el teléfono.

1. **Se cancela** y la mercancía vuelve al camión.
2. **Repetida no hace nada**: un sobre que se reenvía, o la oficina que se
   adelantó, no devuelven dos veces.
3. **La de otro vendedor** y **la que no existe** se rechazan.
4. **El día liquidado** se rechaza: ese corte ya se firmó.
5. **Por la cola real**: el push la acepta y la aplica.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import (
    MOTIVO_DE_CANCELACION_SIN_TEXTO,
    Contexto,
    ErrorDeManejador,
    obtener_manejador,
)
from tests.ayudas_sync import a_json, sobre
from tests.conftest import PASSWORD_VENDEDOR
from tests.test_operaciones import _contexto
from tests.test_panel_venta_editar import _en_camion, venta_del_dia  # noqa: F401

pytestmark = pytest.mark.asyncio

MOTIVO = "Me equivoqué de cliente, era la tienda de al lado"


async def _cancelar(sesion, ctx, venta, motivo: str | None = MOTIVO) -> None:
    datos = {"fecha_dispositivo": "2026-10-20T17:42:03.250Z"}
    if motivo is not None:
        datos["motivo"] = motivo
    await obtener_manejador("venta.cancelar")(sesion, ctx, venta, datos)


async def _venta(sesion, venta) -> dict:
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT v.estado, k.motivo, k.usuario_id, k.reingresa_stock "
                    "  FROM ventas v LEFT JOIN ventas_cancelaciones k ON k.venta_id = v.id "
                    " WHERE v.id = :v"
                ),
                {"v": venta},
            )
        ).mappings().one()
    )


async def test_se_cancela_y_la_mercancia_vuelve_al_camion(
    sesion, semilla, venta_del_dia  # noqa: F811
):
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120")

    await _cancelar(sesion, _contexto(semilla, venta_del_dia), venta_del_dia["venta"])
    await sesion.commit()

    fila = await _venta(sesion, venta_del_dia["venta"])
    assert fila["estado"] == "cancelada"
    assert fila["motivo"] == MOTIVO
    assert fila["usuario_id"] == semilla["vendedor"], "a nombre del vendedor"
    assert fila["reingresa_stock"] is True
    # 120 arriba + las 480 piezas de la venta.
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("600")

    # Y el teléfono lo recibe: el delta de la venta, ya cancelada y con su motivo.
    delta = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log WHERE entidad = 'venta' "
                "   AND entidad_id = :v ORDER BY cursor DESC LIMIT 1"
            ),
            {"v": venta_del_dia["venta"]},
        )
    ).scalar_one()
    assert delta["estado"] == "cancelada"
    assert delta["cancelacion_motivo"] == MOTIVO


async def test_repetida_no_devuelve_dos_veces(sesion, semilla, venta_del_dia):  # noqa: F811
    ctx = _contexto(semilla, venta_del_dia)
    await _cancelar(sesion, ctx, venta_del_dia["venta"])
    await sesion.commit()
    await _cancelar(sesion, ctx, venta_del_dia["venta"], motivo="otra vez")
    await sesion.commit()

    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("600")
    cuantas = (
        await sesion.execute(
            text("SELECT count(*) FROM ventas_cancelaciones WHERE venta_id = :v"),
            {"v": venta_del_dia["venta"]},
        )
    ).scalar_one()
    assert cuantas == 1
    assert (await _venta(sesion, venta_del_dia["venta"]))["motivo"] == MOTIVO


async def test_sin_motivo_entra_igual_con_su_nota(sesion, semilla, venta_del_dia):  # noqa: F811
    await _cancelar(sesion, _contexto(semilla, venta_del_dia), venta_del_dia["venta"], None)
    await sesion.commit()
    fila = await _venta(sesion, venta_del_dia["venta"])
    assert fila["estado"] == "cancelada"
    assert fila["motivo"] == MOTIVO_DE_CANCELACION_SIN_TEXTO


async def test_la_de_otro_vendedor_y_la_que_no_existe_se_rechazan(
    sesion, semilla, venta_del_dia  # noqa: F811
):
    otro = Contexto(
        dispositivo_id=venta_del_dia["dispositivo"],
        usuario_id=semilla["admin"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )
    with pytest.raises(ErrorDeManejador, match="otro vendedor"):
        await _cancelar(sesion, otro, venta_del_dia["venta"])
    await sesion.rollback()
    with pytest.raises(ErrorDeManejador, match="no existe"):
        await _cancelar(sesion, _contexto(semilla, venta_del_dia), uuid.uuid4())
    await sesion.rollback()
    assert (await _venta(sesion, venta_del_dia["venta"]))["estado"] == "confirmada"


async def test_el_dia_liquidado_se_rechaza(sesion, semilla, venta_del_dia):  # noqa: F811
    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, fecha_operativa, "
            "  estado, cerrada_en, efectivo_esperado, efectivo_entregado) "
            "VALUES (:id, 'LQ-000009', :c, :v, :d, 'cerrada', now(), 6000, 6000)"
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
        await _cancelar(sesion, _contexto(semilla, venta_del_dia), venta_del_dia["venta"])
    await sesion.rollback()
    assert (await _venta(sesion, venta_del_dia["venta"]))["estado"] == "confirmada"
    assert await _en_camion(sesion, semilla, venta_del_dia["producto"]) == Decimal("120")


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
    assert r.status_code == 200, r.text
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}

    cuerpo = a_json([
        sobre(
            Operacion(
                tipo="venta.cancelar",
                entidad_id=venta_del_dia["venta"],
                datos={"motivo": MOTIVO, "fecha_dispositivo": "2026-10-20T17:42:03.250Z"},
            )
        )
    ])
    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["aceptadas"] == 1, r.json()

    await sesion.rollback()
    assert (await _venta(sesion, venta_del_dia["venta"]))["estado"] == "cancelada"
