"""De cuánto estaba a cuánto pasó cada artículo (ADR 0002 §189).

«En los ajustes y eso quiero ver de cuántos estaba a cuánto pasó: entradas,
salidas, ajustes, todo eso.»

1. **Movimientos**, una pestaña de Inventario: todo lo que movió la mercancía
   en el periodo, por artículo y almacén, con lo que había, el cambio y lo que
   quedó, sin abrir folios. Las ventas solo si se piden.
2. **En el detalle** de cada entrada, salida, carga y operación, la misma tabla.
3. **«Quedó» es lo de justo después de ese folio**: lo que se movió después no
   lo cambia, y el último renglón dice lo mismo que Existencias.

El día de trabajo de siempre: la bodega con 760 atunes después de cargar 240 al
camión, que vendió 180 y trae 60.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra import traspaso_camion
from app.infra.ajuste_de_existencia import registrar_ajuste
from tests.test_mover_mercancia import _otro_camion
from tests.test_panel_liquidacion import _entrar, sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


def _tabla(html: str, ident: str) -> list[str]:
    """Los renglones de la tabla `ident`, cada uno como texto plano."""
    m = re.search(rf'<table id="{ident}">.*?<tbody>(.*?)</tbody>', html, re.S)
    assert m, f"no está la tabla {ident}"
    return [
        " ".join(re.sub(r"<[^>]+>", " ", r).split())
        for r in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S)
    ]


@pytest.fixture
async def dia(sesion, semilla) -> dict:
    return await sembrar_dia_de_trabajo(sesion, semilla)


async def _entrada(sesion, semilla, producto, piezas: int) -> uuid.UUID:
    """Una entrada confirmada a la bodega, escrita como la escribe el panel."""
    entrada = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO entradas (id, folio, almacen_destino_id, motivo, estado, nota, "
             "                      confirmada_en, confirmada_por) "
             "VALUES (:e, 'EN-000050', :b, 'inicial', 'confirmada', 'arranque', now(), :q)"),
        {"e": entrada, "b": semilla["bodega"], "q": semilla["admin"]},
    )
    await sesion.execute(
        text("INSERT INTO entrada_detalle (entrada_id, producto_id, cantidad) "
             "VALUES (:e, :p, :n)"),
        {"e": entrada, "p": producto, "n": piezas},
    )
    await sesion.execute(
        text("INSERT INTO movimientos_inventario (tipo, almacen_destino_id, producto_id, "
             "    cantidad, documento_tipo, documento_id) "
             "VALUES ('ajuste', :b, :p, :n, 'entrada', :e)"),
        {"e": entrada, "b": semilla["bodega"], "p": producto, "n": piezas},
    )
    await sesion.execute(
        text("UPDATE existencias SET cantidad = cantidad + :n "
             " WHERE almacen_id = :b AND producto_id = :p"),
        {"b": semilla["bodega"], "p": producto, "n": piezas},
    )
    await sesion.commit()
    return entrada


async def _conteo(sesion, semilla, almacen, producto, *, habia: str, contado: str) -> uuid.UUID:
    """Un ajuste por conteo, con la misma función que usa el panel."""
    folio = await registrar_ajuste(
        sesion, almacen_id=almacen, producto_id=producto, es_camion=almacen == semilla["camion"],
        tipo="conteo", actual=Decimal(habia), delta=Decimal(contado) - Decimal(habia),
        contado=Decimal(contado), nota="Se contó con el vendedor en la línea",
        quien=semilla["admin"],
    )
    await sesion.commit()
    return (
        await sesion.execute(text("SELECT id FROM ajustes_camion WHERE folio = :f"), {"f": folio})
    ).scalar_one()


async def test_la_entrada_dice_cuanto_habia_y_cuanto_quedo(cliente, sesion, semilla, dia):
    entrada = await _entrada(sesion, semilla, dia["producto"], 12)
    await _entrar(cliente)

    html = (await cliente.get(f"/panel/entradas/{entrada}")).text
    assert _tabla(html, "antes_y_despues") == ["Atún en agua 140 g ATUN-140 Bodega 760 +12 772"]

    # Lo que se mueve después no cambia lo que quedó con ese folio.
    await _conteo(sesion, semilla, semilla["bodega"], dia["producto"], habia="772",
                  contado="770")
    html = (await cliente.get(f"/panel/entradas/{entrada}")).text
    assert _tabla(html, "antes_y_despues") == ["Atún en agua 140 g ATUN-140 Bodega 760 +12 772"]


async def test_el_ajuste_dice_cuanto_habia_y_cuanto_quedo(cliente, sesion, semilla, dia):
    """El camión traía 60 y se contaron 50: de 60 a 50."""
    ajuste = await _conteo(sesion, semilla, semilla["camion"], dia["producto"], habia="60",
                           contado="50")
    await _entrar(cliente)

    html = (await cliente.get(f"/panel/operaciones/ajuste/{ajuste}")).text
    assert _tabla(html, "antes_y_despues") == ["Atún en agua 140 g ATUN-140 Camión 01 60 -10 50"]


async def test_la_carga_dice_la_bodega_y_el_camion(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    html = (await cliente.get(f"/panel/cargas/{dia['carga']}")).text

    # De la bodega salieron 240 (había 1,000); al camión, que estaba vacío, llegaron.
    assert _tabla(html, "antes_y_despues") == [
        "Atún en agua 140 g ATUN-140 Bodega 1,000 -240 760",
        "Atún en agua 140 g ATUN-140 Camión 01 0 +240 240",
    ]


async def test_mover_entre_camiones_dice_los_dos(cliente, sesion, semilla, dia):
    otro = await _otro_camion(sesion, semilla)
    movido = await traspaso_camion.mover_entre_camiones(
        sesion, origen_id=semilla["camion"], destino_id=otro["camion"],
        cantidades={dia["producto"]: Decimal(5)}, nota="Le faltaba atún a Pedro",
        quien=semilla["admin"],
    )
    await sesion.commit()
    cabeza = (
        await sesion.execute(text("SELECT id FROM ajustes_camion WHERE folio = :f"),
                             {"f": movido.folio})
    ).scalar_one()
    await _entrar(cliente)

    html = (await cliente.get(f"/panel/operaciones/ajuste/{cabeza}")).text
    assert _tabla(html, "antes_y_despues") == [
        "Atún en agua 140 g ATUN-140 Camión 01 60 -5 55",
        "Atún en agua 140 g ATUN-140 Camión 02 0 +5 5",
    ]


async def test_movimientos_lo_dice_todo_sin_abrir_folios(cliente, sesion, semilla, dia):
    entrada = await _entrada(sesion, semilla, dia["producto"], 12)
    await _conteo(sesion, semilla, semilla["camion"], dia["producto"], habia="60", contado="50")
    await _entrar(cliente)

    html = (await cliente.get("/panel/inventario/movimientos")).text
    # Sin la fecha y la hora del principio.
    renglones = [r.split(" ", 2)[2] for r in _tabla(html, "tabla_movimientos")]

    # Lo más nuevo arriba: el conteo del camión, de 60 a 50.
    assert renglones[0].startswith("Ajuste por conteo · AC-")
    assert renglones[0].endswith("Camión 01 Atún en agua 140 g ATUN-140 60 -10 50")
    assert renglones[1:] == [
        "Entrada · EN-000050 Bodega Atún en agua 140 g ATUN-140 760 +12 772",
        "Carga · CG-TEST Bodega Atún en agua 140 g ATUN-140 1,000 -240 760",
        "Carga · CG-TEST Camión 01 Atún en agua 140 g ATUN-140 0 +240 240",
    ]
    assert f'href="/panel/entradas/{entrada}"' in html

    # Las ventas, si se piden: el camión traía 240 y vendió 180.
    html = (await cliente.get("/panel/inventario/movimientos?que=ventas")).text
    assert [r.split(" ", 2)[2] for r in _tabla(html, "tabla_movimientos")] == [
        "Venta · VEND01-000001 Camión 01 Atún en agua 140 g ATUN-140 240 -180 60"
    ]

    # Solo las entradas, y solo un almacén.
    html = (await cliente.get("/panel/inventario/movimientos?que=entradas")).text
    assert len(_tabla(html, "tabla_movimientos")) == 1
    html = (
        await cliente.get(f"/panel/inventario/movimientos?almacen={semilla['camion']}")
    ).text
    assert all("Camión 01" in r for r in _tabla(html, "tabla_movimientos"))


async def test_el_libro_de_un_articulo_dice_cuanto_habia(cliente, sesion, semilla, dia):
    # La carga y la venta del día de siempre se escribieron juntas; el conteo va
    # después, en su propia operación: de 60 a 50.
    await _conteo(sesion, semilla, semilla["camion"], dia["producto"], habia="60",
                  contado="50")
    await _entrar(cliente)
    html = (await cliente.get(f"/panel/inventario/{semilla['camion']}/{dia['producto']}")).text
    plano = " ".join(re.sub(r"<[^>]+>", " ", html).split())

    assert "Había Movimiento Quedó" in plano
    assert "60 -10 50" in plano
