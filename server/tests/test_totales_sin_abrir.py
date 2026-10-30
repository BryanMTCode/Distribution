"""Cada lista del panel dice su total sin abrir ningún folio (ADR 0002 §188).

«No quiero tener que entrar a un folio, o sea, poder ver el total. Hazlo todo
más fácil de ver.»

1. **Cargas** dice lo que vale cada una a precio de venta, lo de cada día y el
   total de lo que se ve.
2. **Corte a mano** dice lo que debía entregar cada vendedor, lo que entregó y
   la diferencia, y la suma de los cerrados.
3. **Ventas**, **Entradas** y **Salidas** terminan con su renglón de total.
   Lo cancelado no suma: no pasó.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from tests.test_panel_liquidacion import _entrar, sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


def _renglon(html: str, ident: str) -> str:
    """El texto del renglón `ident`, sin etiquetas y con un espacio entre celdas."""
    m = re.search(rf'<tr[^>]*\bid="{ident}"[^>]*>(.*?)</tr>', html, re.S)
    assert m, f"no está el renglón {ident}"
    return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())


@pytest.fixture
async def dia(sesion, semilla) -> dict:
    """La carga de 240 atunes a $12.50 la pieza, y la venta de 180 ($2,250)."""
    datos = await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(
        text("INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio) "
             "VALUES (:l, :p, 'PZA', 12.50)"),
        {"l": semilla["lista_precios"], "p": datos["producto"]},
    )
    await sesion.commit()
    return datos


async def test_cargas_dice_lo_que_vale_cada_una_y_el_total(cliente, semilla, dia):  # noqa: ARG001
    await _entrar(cliente)
    html = (await cliente.get("/panel/cargas?filtro=hoy")).text

    # 240 × $12.50, en su renglón y en el total.
    assert "$3,000.00" in html.split('<tr id="total_cargas">')[0]
    assert _renglon(html, "total_cargas") == "Total · 1 carga(s) 1 240 $3,000.00"


async def test_cargas_por_dia_dice_lo_que_vale_cada_dia(cliente, semilla, dia):  # noqa: ARG001
    await _entrar(cliente)
    hoy = dia["dia"].isoformat()
    html = (
        await cliente.get(f"/panel/cargas?filtro=fecha&periodo=rango&desde={hoy}&hasta={hoy}")
    ).text

    assert "$3,000.00" in _renglon(html, f"dia_{hoy}")
    assert "240 piezas, que valen $3,000.00 a precio de venta" in " ".join(
        re.sub(r"<[^>]+>", " ", html).split()
    )


async def test_la_carga_cancelada_no_suma(cliente, sesion, semilla, dia):
    await sesion.execute(
        text("UPDATE cargas SET estado = 'cancelada' WHERE id = :c"), {"c": dia["carga"]}
    )
    await sesion.commit()
    await _entrar(cliente)
    html = (await cliente.get("/panel/cargas?filtro=todas")).text

    assert _renglon(html, "total_cargas") == "Total · 0 carga(s) 0 0 $0.00"


async def test_corte_a_mano_dice_lo_que_debia_lo_que_entrego_y_la_suma(
    cliente, sesion, semilla, dia
):
    from app.api.admin.liquidaciones import abrir_corte, cerrar_corte, guardar_arqueo

    lq = await abrir_corte(sesion, dia["carga"])
    await guardar_arqueo(sesion, lq, "2200", "")
    await cerrar_corte(sesion, lq, quien=semilla["admin"], confirmo_sincronizado=True,
                       conteo_automatico=True)
    await sesion.commit()

    await _entrar(cliente)
    html = (await cliente.get("/panel/liquidaciones")).text
    plano = " ".join(re.sub(r"<[^>]+>", " ", html).split())

    assert "Debía entregar" in plano and "Entregó" in plano
    assert "$2,250.00 $2,200.00 faltan $50.00" in plano
    assert _renglon(html, "total_cortes") == (
        "Total de los 1 cerrado(s) $2,250.00 $2,200.00 faltan $50.00"
    )


async def test_ventas_termina_con_el_total(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    html = (await cliente.get("/panel/ventas?solo_revision=0")).text
    assert _renglon(html, "total_ventas") == "Total · 1 venta(s) $2,250.00"

    # La cancelada se va a su pestaña, con su propio total.
    await sesion.execute(
        text("UPDATE ventas SET estado = 'cancelada' WHERE id = :v"), {"v": dia["venta"]}
    )
    await sesion.commit()
    html = (await cliente.get("/panel/ventas?solo_revision=0")).text
    assert 'id="total_ventas"' not in html
    html = (await cliente.get("/panel/ventas?ver=canceladas")).text
    assert _renglon(html, "total_ventas") == "Total cancelado · 1 venta(s) $2,250.00"


async def test_entradas_termina_con_el_total(cliente, sesion, semilla, dia):
    for folio, estado, importe in (
        ("EN-000001", "confirmada", "1500.00"),
        ("EN-000002", "confirmada", None),
        ("EN-000003", "cancelada", "999.00"),
    ):
        entrada = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO entradas (id, folio, almacen_destino_id, motivo, estado, nota, "
                 "    importe_total, confirmada_en, confirmada_por, cancelada_en, "
                 "    cancelacion_motivo) "
                 "VALUES (:e, :f, :b, 'inicial', :estado, 'arranque', :i, now(), :quien, "
                 "        CASE WHEN :estado = 'cancelada' THEN now() END, "
                 "        CASE WHEN :estado = 'cancelada' THEN 'prueba' END)"),
            {"e": entrada, "f": folio, "b": semilla["bodega"], "estado": estado,
             "i": importe, "quien": semilla["admin"]},
        )
        await sesion.execute(
            text("INSERT INTO entrada_detalle (entrada_id, producto_id, cantidad) "
                 "VALUES (:e, :p, 12)"),
            {"e": entrada, "p": dia["producto"]},
        )
    await sesion.commit()

    await _entrar(cliente)
    html = (await cliente.get("/panel/entradas")).text
    # La sin importe no estorba; la cancelada no suma.
    assert _renglon(html, "total_entradas") == "Total · 2 entrada(s) 2 24 $1,500.00"


async def test_salidas_dice_lo_que_valia_y_el_total(cliente, sesion, semilla, dia):
    for folio, estado in (("SA-000001", "confirmada"), ("SA-000002", "cancelada")):
        salida = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO salidas (id, folio, almacen_origen_id, tipo, nota, estado, "
                 "    confirmada_en, confirmada_por, cancelada_en, cancelacion_motivo) "
                 "VALUES (:s, :f, :b, 'conteo', 'prueba', :estado, now(), :quien, "
                 "        CASE WHEN :estado = 'cancelada' THEN now() END, "
                 "        CASE WHEN :estado = 'cancelada' THEN 'prueba' END)"),
            {"s": salida, "f": folio, "b": semilla["bodega"], "estado": estado,
             "quien": semilla["admin"]},
        )
        await sesion.execute(
            text("INSERT INTO salida_detalle (salida_id, producto_id, cantidad, "
                 "        contado, existencia_al_capturar) "
                 "VALUES (:s, :p, 10, 90, 100)"),
            {"s": salida, "p": dia["producto"]},
        )
    await sesion.commit()

    await _entrar(cliente)
    html = (await cliente.get("/panel/salidas")).text
    # 10 × $12.50 en cada una; solo la confirmada suma.
    assert html.count("$125.00") >= 2
    assert _renglon(html, "total_salidas") == "Total · 1 salida(s) 1 10 $125.00"
