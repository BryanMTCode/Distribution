"""Lo cancelado no se mezcla con lo demás (ADR 0002 §95).

Pedido de la dirección (octubre 2026): «si borro una transacción o algo, que no
siga apareciendo con todas las demás, sino que salga en una parte que diga
eliminadas». Las ventas tienen su pestaña «Canceladas»; las entradas, salidas,
cargas y los movimientos del vendedor la llevan plegada al final de su tabla.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import solo_texto
from tests.test_panel_entradas import _abrir, _csrf, _entrar, _id_de
from tests.test_panel_liquidacion import sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


async def _cancelar_la_venta(sesion, dia) -> str:
    folio = (
        await sesion.execute(
            text("UPDATE ventas SET estado = 'cancelada' WHERE id = :v RETURNING folio_local"),
            {"v": dia["venta"]},
        )
    ).scalar_one()
    await sesion.commit()
    return folio


async def test_las_ventas_canceladas_tienen_su_pestana(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _entrar(cliente)
    folio = (
        await sesion.execute(text("SELECT folio_local FROM ventas WHERE id = :v"),
                             {"v": dia["venta"]})
    ).scalar_one()
    assert folio in solo_texto(await cliente.get("/panel/ventas?solo_revision=0"))

    await _cancelar_la_venta(sesion, dia)
    todas = await cliente.get("/panel/ventas?solo_revision=0")
    assert folio not in solo_texto(todas)
    assert "Canceladas (1)" in solo_texto(todas)
    aparte = solo_texto(await cliente.get("/panel/ventas?ver=canceladas"))
    assert "Ventas canceladas" in aparte and folio in aparte


async def test_una_entrada_cancelada_va_plegada_al_final(cliente, sesion, semilla):
    await _entrar(cliente)
    viva = _id_de(await _abrir(cliente, semilla))
    muerta = _id_de(await _abrir(cliente, semilla))
    await cliente.post(
        f"/panel/entradas/{muerta}/cancelar",
        data={"csrf": _csrf(cliente), "motivo": "la remisión era de otra sucursal"},
    )
    folios = dict(
        (
            await sesion.execute(
                text("SELECT id, folio FROM entradas WHERE id IN (:a, :b)"),
                {"a": viva, "b": muerta},
            )
        ).all()
    )
    html = (await cliente.get("/panel/entradas")).text
    lista, plegadas = html.split('id="entradas_canceladas"')
    assert folios[viva] in lista and folios[muerta] not in lista
    assert folios[muerta] in plegadas.split("</details>")[0]
    assert "Entradas canceladas" in plegadas


async def test_los_movimientos_cancelados_del_vendedor_van_aparte(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    folio = await _cancelar_la_venta(sesion, dia)
    await _entrar(cliente)
    html = (
        await cliente.get(f"/panel/vendedores/{semilla['vendedor']}?periodo=mes")
    ).text
    lista, plegados = html.split('id="movimientos_cancelados"')
    assert folio not in lista
    assert folio in plegados.split("</details>")[0]
