"""Transferencias: el dinero de las ventas que no llegó en la mano.

La operación es de contado (ADR 0002 §81). Lo que estas pruebas defienden:

1. Una venta por transferencia **espera al banco**; el efectivo no.
2. Confirmar va en bloque y **no confirma dos veces**.
3. «No llegó» va **con motivo y con nombre**.
4. La transferencia **no cuenta como efectivo** en el corte ni en el tablero.
5. La mano que vigila la ruta (supervisor) ve pero no confirma.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import csrf_del_panel, solo_texto
from tests.test_oficina_clientes_api import _usuario
from tests.test_panel_liquidacion import _entrar, sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def dia(sesion, semilla) -> dict:
    """El día de siempre (una venta de $2,250), con la venta pagada por transferencia."""
    datos = await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(
        text(
            "UPDATE ventas SET forma_pago = 'transferencia', pago_estado = 'por_confirmar', "
            "       referencia_pago = 'SPEI 4471' WHERE id = :v"
        ),
        {"v": datos["venta"]},
    )
    await sesion.commit()
    return datos


async def _venta(sesion, venta_id) -> dict:
    return dict(
        (
            await sesion.execute(text("SELECT * FROM ventas WHERE id = :v"), {"v": venta_id})
        ).mappings().one()
    )


async def test_la_lista_muestra_lo_que_espera_al_banco(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    r = await cliente.get("/panel/transferencias")
    assert r.status_code == 200
    plano = solo_texto(r)
    assert "VEND01-000001" in plano
    assert "SPEI 4471" in plano
    assert "2250.00" in plano


async def test_confirmar_en_bloque_y_no_dos_veces(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    pagina = await cliente.get("/panel/transferencias")
    r = await cliente.post(
        "/panel/transferencias/confirmar",
        data={"csrf": csrf_del_panel(cliente, pagina), "venta": [str(dia["venta"])]},
        follow_redirects=True,
    )
    assert "Se confirmaron 1 transferencia(s) por $2,250.00" in solo_texto(r)
    venta = await _venta(sesion, dia["venta"])
    assert venta["pago_estado"] == "confirmado"
    assert venta["pago_resuelto_por"] == semilla["admin"]

    r = await cliente.post(
        "/panel/transferencias/confirmar",
        data={"csrf": csrf_del_panel(cliente), "venta": [str(dia["venta"])]},
        follow_redirects=True,
    )
    assert "Ninguna seguía por confirmar" in solo_texto(r)
    auditadas = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM auditoria "
                " WHERE entidad_id = :v AND accion = 'confirmar_transferencia'"
            ),
            {"v": dia["venta"]},
        )
    ).scalar_one()
    assert auditadas == 1


async def test_no_llego_pide_motivo_y_deja_quien(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    ruta = f"/panel/transferencias/{dia['venta']}/no-llego"
    r = await cliente.post(
        ruta, data={"csrf": csrf_del_panel(cliente), "motivo": "  "}, follow_redirects=True
    )
    assert "Escribe qué pasó" in solo_texto(r)
    assert (await _venta(sesion, dia["venta"]))["pago_estado"] == "por_confirmar"

    r = await cliente.post(
        ruta,
        data={"csrf": csrf_del_panel(cliente), "motivo": "No aparece en el estado de cuenta"},
        follow_redirects=True,
    )
    assert "no llegó" in solo_texto(r)
    venta = await _venta(sesion, dia["venta"])
    assert venta["pago_estado"] == "rechazado"
    assert venta["pago_resolucion_nota"] == "No aparece en el estado de cuenta"
    # Sin la casilla, no se le carga nada al vendedor.
    cargos = (
        await sesion.execute(text("SELECT count(*) FROM cuenta_vendedor"))
    ).scalar_one()
    assert cargos == 0


async def test_la_base_no_deja_efectivo_por_confirmar(sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    with pytest.raises(IntegrityError):
        await sesion.execute(
            text("UPDATE ventas SET pago_estado = 'por_confirmar' WHERE id = :v"),
            {"v": dia["venta"]},
        )
    await sesion.rollback()


async def test_la_base_no_deja_rechazar_sin_motivo(sesion, semilla, dia):
    with pytest.raises(IntegrityError):
        await sesion.execute(
            text(
                "UPDATE ventas SET pago_estado = 'rechazado', pago_resuelto_por = :u "
                " WHERE id = :v"
            ),
            {"v": dia["venta"], "u": semilla["admin"]},
        )
    await sesion.rollback()


async def test_el_supervisor_ve_pero_no_confirma(cliente, sesion, semilla, dia):
    await _usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    pagina = await cliente.get("/panel/transferencias")
    assert pagina.status_code == 200
    assert 'name="venta"' not in pagina.text  # sin casillas
    r = await cliente.post(
        "/panel/transferencias/confirmar",
        data={"csrf": csrf_del_panel(cliente, pagina), "venta": [str(dia["venta"])]},
    )
    assert r.status_code == 403
    assert (await _venta(sesion, dia["venta"]))["pago_estado"] == "por_confirmar"


async def test_gerencia_confirma(cliente, sesion, semilla, dia):
    await _usuario(sesion, semilla, "GER01", "gerente")
    await _entrar(cliente, "GER01")
    pagina = await cliente.get("/panel/transferencias")
    await cliente.post(
        "/panel/transferencias/confirmar",
        data={"csrf": csrf_del_panel(cliente, pagina), "venta": [str(dia["venta"])]},
    )
    assert (await _venta(sesion, dia["venta"]))["pago_estado"] == "confirmado"


async def test_el_tablero_avisa_cuanto_espera_al_banco(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    r = await cliente.get("/panel")
    assert 'id="tarjeta_por_confirmar"' in r.text
    plano = solo_texto(r)
    assert "transferencias por confirmar" in plano
    assert "2,250.00" in plano


async def test_la_transferencia_no_es_efectivo_del_corte(sesion, semilla, dia):
    """El arqueo cuenta lo que viene en la bolsa: la transferencia no viene."""
    from app.api.admin.liquidaciones import _efectivo_esperado

    esperado = await _efectivo_esperado(sesion, semilla["vendedor"], dia["dia"])
    assert esperado == Decimal("0.00")

    await sesion.execute(
        text(
            "UPDATE ventas SET forma_pago = 'efectivo', pago_estado = 'confirmado', "
            "       referencia_pago = NULL WHERE id = :v"
        ),
        {"v": dia["venta"]},
    )
    await sesion.commit()
    esperado = await _efectivo_esperado(sesion, semilla["vendedor"], dia["dia"])
    assert esperado == Decimal("2250.00")
