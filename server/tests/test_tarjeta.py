"""Lo que vale la empresa, y la tarjeta de la empresa (ADR 0002 §190).

«Quiero en el dashboard tener la cuenta del almacén, más los camiones, más la
tarjeta de débito de la empresa: las tres visiones distintas y el total. Y las
compras poder agregarlas de la tarjeta: a la tarjeta, la venta y las salidas.»

1. **Lo que vale**: la bodega, los camiones y la tarjeta por separado, y el total;
   también en el Tablero. Sin el saldo inicial, la tarjeta lo pide.
2. **La tarjeta empieza** con lo que dice el banco, y desde ahí **entra solo** el
   efectivo de cada corte y el mayoreo, y **sale solo** la compra pagada con la
   tarjeta. Lo de antes de empezar no cuenta.
3. **A mano**, el dinero que entra o sale por otra cosa, y el ajuste con el banco.
   Lo capturado por error se quita.
4. **Lo eliminado deja de contar**: un corte anulado o un mayoreo cancelado.
5. **Solo el administrador** la ve.

El día de siempre: la bodega con 760 atunes y el camión con 60, a $12.50 la
pieza: $9,500 y $750. El vendedor vendió $2,250.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from tests.test_compras import _comprar, _csrf, _entrar, _id_proveedor, _producto, _proveedor
from tests.test_panel_liquidacion import sembrar_dia_de_trabajo
from tests.test_panel_objetivos import _crear_usuario

pytestmark = pytest.mark.asyncio


def _plano(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _tarjeta_de(html: str, ident: str) -> str:
    m = re.search(rf'id="{ident}"[^>]*>(.*?)</(?:a|div)>\s*</(?:a|div)>', html, re.S)
    assert m, f"no está {ident}"
    return _plano(m.group(1))


@pytest.fixture
async def dia(sesion, semilla) -> dict:
    datos = await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(
        text("INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio) "
             "VALUES (:l, :p, 'PZA', 12.50)"),
        {"l": semilla["lista_precios"], "p": datos["producto"]},
    )
    await sesion.commit()
    return datos


async def _post(cliente, ruta: str, **datos):
    r = await cliente.post(ruta, data={"csrf": _csrf(cliente), **datos},
                           follow_redirects=True)
    assert r.status_code == 200, r.text
    return r


async def _saldo(cliente) -> str:
    return _tarjeta_de((await cliente.get("/panel/dinero/tarjeta")).text, "saldo_tarjeta")


async def _cerrar_el_corte(sesion, semilla, dia, entrega: str) -> uuid.UUID:
    from app.api.admin.liquidaciones import abrir_corte, cerrar_corte, guardar_arqueo

    lq = await abrir_corte(sesion, dia["carga"])
    await guardar_arqueo(sesion, lq, entrega, "")
    await cerrar_corte(sesion, lq, quien=semilla["admin"], confirmo_sincronizado=True,
                       conteo_automatico=True)
    await sesion.commit()
    return lq


async def test_lo_que_vale_la_empresa_sin_tarjeta_todavia(cliente, semilla, dia):  # noqa: ARG001
    await _entrar(cliente)
    html = (await cliente.get("/panel/dinero")).text

    assert _tarjeta_de(html, "vale_almacen") == "$9,500.00 Almacén"
    assert _tarjeta_de(html, "vale_camiones") == "$750.00 Camiones"
    assert _tarjeta_de(html, "vale_tarjeta") == "— Tarjeta: falta su saldo inicial"
    assert _tarjeta_de(html, "vale_total") == "$10,250.00 Total"
    assert "Camión 01 Juan Pérez $750.00" in _plano(html)

    # La tarjeta pide con cuánto empieza.
    assert 'id="forma_empezar"' in (await cliente.get("/panel/dinero/tarjeta")).text


async def test_la_tarjeta_empieza_y_se_le_suman_los_cortes(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="1,000")
    assert await _saldo(cliente) == "$1,000.00 debe haber hoy"

    # El vendedor entregó $2,200 en su corte: entran solos.
    lq = await _cerrar_el_corte(sesion, semilla, dia, "2200")
    html = (await cliente.get("/panel/dinero/tarjeta")).text
    assert _tarjeta_de(html, "saldo_tarjeta") == "$3,200.00 debe haber hoy"
    assert f'href="/panel/liquidaciones/{lq}"' in html
    assert "Corte de un vendedor" in _plano(html)

    html = (await cliente.get("/panel/dinero")).text
    assert _tarjeta_de(html, "vale_tarjeta") == "$3,200.00 Tarjeta"
    # El tablero dice lo mismo.
    tablero = _plano((await cliente.get("/panel")).text)
    assert "Lo que vale la empresa" in tablero and "$3,200.00 Tarjeta" in tablero

    # Si el corte se anula, deja de contar.
    await sesion.execute(
        text("UPDATE liquidaciones SET estado = 'anulada' WHERE id = :l"), {"l": lq}
    )
    await sesion.commit()
    assert await _saldo(cliente) == "$1,000.00 debe haber hoy"


async def test_lo_de_antes_de_empezar_no_cuenta(cliente, sesion, semilla, dia):
    await _cerrar_el_corte(sesion, semilla, dia, "2250")
    await _entrar(cliente)
    await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="500")
    assert await _saldo(cliente) == "$500.00 debe haber hoy"
    # Y no se empieza dos veces.
    r = await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="900")
    assert "ya tiene su saldo inicial" in r.text
    assert await _saldo(cliente) == "$500.00 debe haber hoy"


async def test_el_dinero_que_entra_o_sale_a_mano_y_se_quita(cliente, sesion, semilla, dia):  # noqa: ARG001
    await _entrar(cliente)
    await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="1000")
    await _post(cliente, "/panel/dinero/tarjeta/movimiento", tipo="salida", importe="300",
                concepto="Gasolina del camión 1", fecha="")
    await _post(cliente, "/panel/dinero/tarjeta/movimiento", tipo="entrada", importe="100",
                concepto="Préstamo del dueño", fecha="")
    assert await _saldo(cliente) == "$800.00 debe haber hoy"

    # Sin concepto no se guarda.
    r = await _post(cliente, "/panel/dinero/tarjeta/movimiento", tipo="salida",
                    importe="50", concepto="", fecha="")
    assert "en qué se fue" in r.text
    assert await _saldo(cliente) == "$800.00 debe haber hoy"

    # La entrada fue un error: se quita, y se queda con el motivo.
    entrada = (
        await sesion.execute(text("SELECT id FROM movimientos_tarjeta WHERE tipo = 'entrada'"))
    ).scalar_one()
    await _post(cliente, f"/panel/dinero/tarjeta/{entrada}/quitar", motivo="Se capturó dos veces")
    assert await _saldo(cliente) == "$700.00 debe haber hoy"
    quitado = (
        await sesion.execute(
            text("SELECT motivo_quitado FROM movimientos_tarjeta WHERE id = :i"), {"i": entrada}
        )
    ).scalar_one()
    assert quitado == "Se capturó dos veces"


async def test_asi_esta_hoy_ajusta_al_banco(cliente, sesion, semilla, dia):  # noqa: ARG001
    await _entrar(cliente)
    await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="1000")
    r = await _post(cliente, "/panel/dinero/tarjeta/asi-esta", saldo="940.50")
    assert "Se ajustó la diferencia (−$59.50)" in r.text
    assert await _saldo(cliente) == "$940.50 debe haber hoy"
    concepto = (
        await sesion.execute(text("SELECT concepto FROM movimientos_tarjeta WHERE tipo = 'ajuste'"))
    ).scalar_one()
    assert concepto == "El banco dice $940.50; el sistema decía $1,000.00"

    r = await _post(cliente, "/panel/dinero/tarjeta/asi-esta", saldo="940.50")
    assert "Ya cuadraba con el banco" in r.text


async def test_la_compra_pagada_con_la_tarjeta_sale_de_ella(cliente, sesion, semilla):
    await _producto(sesion)
    await _entrar(cliente)
    await _proveedor(cliente)
    entrada, _ = await _comprar(cliente, semilla, sesion, proveedor=await _id_proveedor(sesion))
    await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="5000")

    # $2,960 de la factura: $1,000 con la tarjeta y lo demás en efectivo.
    for importe, forma in (("1000", "tarjeta"), ("1960", "efectivo")):
        r = await cliente.post(
            f"/panel/compras/cuenta/{entrada}/pago",
            data={"csrf": _csrf(cliente), "importe": importe, "forma_pago": forma,
                  "referencia": "", "fecha": "", "nota": ""},
            follow_redirects=False,
        )
        assert r.status_code == 303 and "error" not in r.headers["location"]

    html = (await cliente.get("/panel/dinero/tarjeta")).text
    assert _tarjeta_de(html, "saldo_tarjeta") == "$4,000.00 debe haber hoy"
    assert "Compra pagada con la tarjeta" in _plano(html)
    assert f'href="/panel/compras/cuenta/{entrada}"' in html


async def test_el_mayoreo_entra_y_el_cancelado_no(cliente, sesion, semilla, dia):
    await _entrar(cliente)
    await _post(cliente, "/panel/dinero/tarjeta/empezar", saldo="0")
    for folio, estado in (("MY-000001", "confirmada"), ("MY-000002", "cancelada")):
        await sesion.execute(
            text("INSERT INTO ventas_mayoreo (folio, cliente_id, almacen_id, fecha, total, "
                 "    estado, motivo_cancelacion, vendida_por) "
                 "VALUES (:f, :c, :b, CURRENT_DATE, 1500, :e, "
                 "        CASE WHEN :e = 'cancelada' THEN 'prueba' END, :q)"),
            {"f": folio, "c": dia["cliente"], "b": semilla["bodega"], "e": estado,
             "q": semilla["admin"]},
        )
    await sesion.commit()
    assert await _saldo(cliente) == "$1,500.00 debe haber hoy"


async def test_solo_el_administrador_la_ve(cliente, sesion, semilla, dia):  # noqa: ARG001
    await _crear_usuario(sesion, semilla, "GER09", "gerente")
    await _entrar(cliente, "GER09")
    assert (await cliente.get("/panel/dinero")).status_code == 403
    assert (await cliente.get("/panel/dinero/tarjeta")).status_code == 403
    tablero = (await cliente.get("/panel")).text
    assert "Lo que vale la empresa" not in tablero
    assert 'href="/panel/dinero"' not in tablero
    # Y su importe no se puede tocar.
    r = await cliente.post("/panel/dinero/tarjeta/empezar",
                           data={"csrf": _csrf(cliente), "saldo": "1"},
                           follow_redirects=False)
    assert r.status_code == 403
    assert (await sesion.execute(text("SELECT count(*) FROM movimientos_tarjeta"))).scalar() == 0
