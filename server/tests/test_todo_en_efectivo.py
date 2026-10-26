"""Toda venta es en efectivo: el dashboard no repite lo vendido como «Efectivo» (ADR 0002 §154).

«Toda la venta es en efectivo, así que cambia esto, porque aparece lo vendido y lo
de efectivo.» Desde la +28 no hay transferencias (§81), y las columnas «Importe» y
«Efectivo» decían lo mismo.

1. **Todo en efectivo:** las tablas del Tablero (por vendedor y por día) y la de
   Vendedores traen solo «Importe».
2. **Con una transferencia de un teléfono viejo**, el efectivo ya no es lo vendido
   y las columnas vuelven: «Efectivo» y «Transferencia».
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tests.test_panel_liquidacion import sembrar_dia_de_trabajo
from tests.test_plan_visita import _entrar

pytestmark = pytest.mark.asyncio

COLUMNA_EFECTIVO = '<th class="numero">Efectivo</th>'


def _siete_dias() -> str:
    """Los últimos siete días. No «esta semana»: el lunes es un solo día, y con un
    día el Tablero no trae la tabla por día. La prueba fallaba cada lunes."""
    hoy = date.today()
    return f"/panel?periodo=rango&desde={hoy - timedelta(days=6)}&hasta={hoy}"


async def test_todo_en_efectivo_no_hay_columna_de_efectivo(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)  # una venta de $2,250, en efectivo
    await _entrar(cliente)

    tablero = (await cliente.get(_siete_dias())).text
    assert 'id="tabla_por_vendedor"' in tablero and 'id="tabla_por_dia"' in tablero
    assert '<th class="numero">Importe</th>' in tablero
    assert COLUMNA_EFECTIVO not in tablero
    assert '<th class="numero">Transferencia</th>' not in tablero

    vendedores = (await cliente.get("/panel/vendedores")).text
    assert 'id="tabla_vendedores"' in vendedores
    assert '<th class="numero">Importe</th>' in vendedores
    assert COLUMNA_EFECTIVO not in vendedores


async def test_con_una_transferencia_vieja_se_separan(cliente, sesion, semilla):
    datos = await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(
        text(
            "UPDATE ventas SET forma_pago = 'transferencia', pago_estado = 'por_confirmar' "
            " WHERE id = :v"
        ),
        {"v": datos["venta"]},
    )
    await sesion.commit()
    await _entrar(cliente)

    tablero = (await cliente.get(_siete_dias())).text
    # Por vendedor y por día.
    assert tablero.count(COLUMNA_EFECTIVO) == 2
    assert '<th class="numero">Transferencia</th>' in tablero

    vendedores = (await cliente.get("/panel/vendedores")).text
    assert COLUMNA_EFECTIVO in vendedores
