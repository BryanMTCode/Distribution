"""La cuenta del vendedor: lo que el Corte del día calculaba y nadie acumulaba.

Regla de la dirección (octubre 2026): **la mercancía se cobra a costo**, no a
precio de venta. Se recupera la pérdida real del inventario; no se gana margen
con el error de un empleado.

Lo que estas pruebas defienden:

1. El faltante se carga **a costo promedio**, y lo que no tiene costo **se dice**
   en vez de cobrarse en cero a escondidas.
2. La merma a cargo del vendedor (`afecta_vendedor`) también, y la que no, no.
3. El efectivo se carga solo si **alguien contó**: «entregó $0» no es «nadie hizo
   el arqueo».
4. Es un **libro**: la base no deja editar ni borrar, y un Corte carga una vez.
5. Los abonos no le fabrican saldo a favor, y solo gerencia mueve la cuenta.
6. El cobro que el cliente sí pagó y nunca llegó: **abono al cliente, cargo al
   vendedor**.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import PASSWORD_VENDEDOR, solo_texto
from tests.test_panel_liquidacion import (
    _abrir,
    _cerrar,
    _contar,
    _csrf,
    _entrar,
    sembrar_dia_de_trabajo,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def dia_de_trabajo(sesion, semilla) -> dict:
    """240 piezas cargadas, 180 vendidas de contado por $2,250: esperado 60."""
    return await sembrar_dia_de_trabajo(sesion, semilla)


async def _costo(sesion, producto, costo: str) -> None:
    await sesion.execute(
        text(
            "INSERT INTO producto_costos (producto_id, costo_promedio) VALUES (:p, :c)"
        ),
        {"p": producto, "c": Decimal(costo)},
    )
    await sesion.commit()


async def _movimientos(sesion, vendedor) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    "SELECT tipo, origen, importe, detalle, liquidacion_id, cobro_id "
                    "  FROM cuenta_vendedor WHERE vendedor_id = :v ORDER BY origen"
                ),
                {"v": vendedor},
            )
        ).mappings().all()
    ]


async def _arqueo(cliente, liq: str, entregado: str):
    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")
    return await cliente.post(
        f"/panel/liquidaciones/{liq}/efectivo",
        data={"csrf": _csrf(cliente, detalle), "efectivo_entregado": entregado},
        follow_redirects=True,
    )


async def _merma(sesion, semilla, dia, motivo: str, cantidad: str) -> None:
    merma = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO mermas (id, dispositivo_id, folio_consecutivo, folio_local, tipo,
                                almacen_id, vendedor_id, motivo_codigo,
                                fecha_dispositivo, fecha_operativa)
            VALUES (:id, :d, :folio, :folio_local, 'merma', :a, :v, :motivo, now(), :f)
            """
        ),
        {
            "id": merma,
            "d": dia["dispositivo"],
            "folio": abs(hash(str(merma))) % 100000,
            "folio_local": f"M-{str(merma)[:6]}",
            "a": semilla["camion"],
            "v": semilla["vendedor"],
            "motivo": motivo,
            "f": dia["dia"],
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO merma_detalle (id, merma_id, producto_id, cantidad_base) "
            "VALUES (:id, :m, :p, :c)"
        ),
        {"id": uuid.uuid4(), "m": merma, "p": dia["producto"], "c": Decimal(cantidad)},
    )
    # Sale del camión, como la escribiría el manejador.
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad - :c "
            " WHERE almacen_id = :a AND producto_id = :p"
        ),
        {"c": Decimal(cantidad), "a": semilla["camion"], "p": dia["producto"]},
    )
    await sesion.commit()


# ===========================================================================
# 1. El faltante de mercancía, a costo
# ===========================================================================
async def test_EL_FALTANTE_SE_CARGA_A_COSTO_Y_NO_A_PRECIO(
    cliente, sesion, semilla, dia_de_trabajo
):
    """Esperado 60, contados 54: faltan 6. A costo $8.50 son $51.00; a precio de
    venta ($12.50) serían $75.00, y esa diferencia es margen sobre el error de un
    empleado."""
    await _costo(sesion, dia_de_trabajo["producto"], "8.5000")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _arqueo(cliente, liq, "2250.00")
    r = await _cerrar(cliente, liq)

    movimientos = await _movimientos(sesion, semilla["vendedor"])
    assert len(movimientos) == 1
    cargo = movimientos[0]
    assert cargo["tipo"] == "cargo"
    assert cargo["origen"] == "faltante_mercancia"
    assert cargo["importe"] == Decimal("51.00")
    assert cargo["liquidacion_id"] == uuid.UUID(liq)
    # El detalle es lo que se le enseña al vendedor: de dónde salen los $51.
    renglon = cargo["detalle"][0]
    assert renglon["sku"] == "ATUN-140"
    assert Decimal(renglon["cantidad"]) == Decimal("6")
    assert Decimal(renglon["costo"]) == Decimal("8.5")
    assert Decimal(renglon["importe"]) == Decimal("51.00")

    assert "Se cargaron $51.00 a la cuenta del vendedor" in solo_texto(r)


async def test_lo_que_no_tiene_costo_no_se_cobra_en_cero_a_escondidas(
    cliente, sesion, semilla, dia_de_trabajo
):
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _arqueo(cliente, liq, "2250.00")
    r = await _cerrar(cliente, liq)

    assert await _movimientos(sesion, semilla["vendedor"]) == []
    plano = solo_texto(r)
    assert "sin costo capturado no se cobraron" in plano
    assert "Atún en agua 140 g" in plano


async def test_si_cuadra_no_se_carga_nada(cliente, sesion, semilla, dia_de_trabajo):
    await _costo(sesion, dia_de_trabajo["producto"], "8.5000")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _arqueo(cliente, liq, "2250.00")
    await _cerrar(cliente, liq)
    assert await _movimientos(sesion, semilla["vendedor"]) == []


async def test_un_sobrante_no_se_le_abona(cliente, sesion, semilla, dia_de_trabajo):
    """Casi siempre es una venta sin sincronizar: no es dinero del vendedor."""
    await _costo(sesion, dia_de_trabajo["producto"], "8.5000")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "66")
    await _arqueo(cliente, liq, "2250.00")
    await _cerrar(cliente, liq)
    assert await _movimientos(sesion, semilla["vendedor"]) == []


# ===========================================================================
# 2. La merma que el motivo dice que es suya
# ===========================================================================
async def test_la_merma_a_cargo_del_vendedor_se_carga_a_costo(
    cliente, sesion, semilla, dia_de_trabajo
):
    """«Empaque roto» tiene `afecta_vendedor`: no es faltante —el conteo la
    explica—, pero la pérdida es suya. «Caducado» no."""
    await _costo(sesion, dia_de_trabajo["producto"], "8.5000")
    await _merma(sesion, semilla, dia_de_trabajo, "ROTO", "2")
    await _merma(sesion, semilla, dia_de_trabajo, "CADUCADO", "3")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    # 60 esperados − 5 mermados = 55 arriba: cuadra.
    await _contar(cliente, liq, sesion, "55")
    await _arqueo(cliente, liq, "2250.00")
    await _cerrar(cliente, liq)

    movimientos = await _movimientos(sesion, semilla["vendedor"])
    assert [m["origen"] for m in movimientos] == ["merma_atribuible"]
    assert movimientos[0]["importe"] == Decimal("17.00")  # 2 × 8.50
    assert movimientos[0]["detalle"][0]["motivo"] == "Empaque roto"


# ===========================================================================
# 3. El efectivo, solo si alguien contó
# ===========================================================================
async def test_el_faltante_de_efectivo_se_carga(cliente, sesion, semilla, dia_de_trabajo):
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _arqueo(cliente, liq, "2000.00")
    await _cerrar(cliente, liq)

    movimientos = await _movimientos(sesion, semilla["vendedor"])
    assert [m["origen"] for m in movimientos] == ["faltante_efectivo"]
    assert movimientos[0]["importe"] == Decimal("250.00")
    assert Decimal(movimientos[0]["detalle"]["esperado"]) == Decimal("2250.00")


async def test_sin_arqueo_no_se_carga_efectivo_y_se_dice(
    cliente, sesion, semilla, dia_de_trabajo
):
    """`efectivo_entregado` nace en 0: sin arqueo parecería que no entregó nada y
    se le cargarían los $2,250 completos."""
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    r = await _cerrar(cliente, liq)

    assert await _movimientos(sesion, semilla["vendedor"]) == []
    assert "no se capturó el arqueo" in solo_texto(r)


async def test_el_cierre_recalcula_el_efectivo_esperado(
    cliente, sesion, semilla, dia_de_trabajo
):
    """Un cobro en efectivo que sincronizó DESPUÉS del arqueo entra a la cuenta:
    con el esperado del arqueo, esos $100 se quedarían sin cobrar a nadie."""
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "60")
    await _arqueo(cliente, liq, "2250.00")

    await sesion.execute(
        text(
            """
            INSERT INTO cobros (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, importe, forma_pago, estado,
                                fecha_dispositivo, fecha_operativa, importe_aplicado,
                                saldo_a_favor)
            VALUES (:id, :d, 90, 'VEND01-C00090', :c, :v, 100, 'efectivo', 'confirmado',
                    now(), :f, 0, 100)
            """
        ),
        {
            "id": uuid.uuid4(),
            "d": dia_de_trabajo["dispositivo"],
            "c": dia_de_trabajo["cliente"],
            "v": semilla["vendedor"],
            "f": dia_de_trabajo["dia"],
        },
    )
    await sesion.commit()
    await _cerrar(cliente, liq)

    movimientos = await _movimientos(sesion, semilla["vendedor"])
    assert [m["origen"] for m in movimientos] == ["faltante_efectivo"]
    assert movimientos[0]["importe"] == Decimal("100.00")


# ===========================================================================
# 4. Es un libro
# ===========================================================================
async def _un_cargo(sesion, semilla, importe="50.00") -> uuid.UUID:
    fila = (
        await sesion.execute(
            text(
                "INSERT INTO cuenta_vendedor (vendedor_id, tipo, origen, importe, "
                "                             fecha, concepto, registrado_por) "
                "VALUES (:v, 'cargo', 'cargo_manual', :i, CURRENT_DATE, 'prueba', :a) "
                "RETURNING id"
            ),
            {"v": semilla["vendedor"], "i": Decimal(importe), "a": semilla["admin"]},
        )
    ).scalar_one()
    await sesion.commit()
    return fila


async def test_la_base_no_deja_editar_un_movimiento(sesion, semilla):
    cargo = await _un_cargo(sesion, semilla)
    with pytest.raises(DBAPIError, match="append-only"):
        await sesion.execute(
            text("UPDATE cuenta_vendedor SET importe = 1 WHERE id = :id"), {"id": cargo}
        )
    await sesion.rollback()


async def test_la_base_no_deja_borrar_un_movimiento(sesion, semilla):
    cargo = await _un_cargo(sesion, semilla)
    with pytest.raises(DBAPIError, match="append-only"):
        await sesion.execute(text("DELETE FROM cuenta_vendedor WHERE id = :id"), {"id": cargo})
    await sesion.rollback()


async def test_un_corte_carga_cada_concepto_una_vez(
    cliente, sesion, semilla, dia_de_trabajo
):
    await _costo(sesion, dia_de_trabajo["producto"], "8.5000")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _arqueo(cliente, liq, "2250.00")
    await _cerrar(cliente, liq)
    with pytest.raises(IntegrityError, match="uq_cuenta_vendedor_por_corte"):
        await sesion.execute(
            text(
                "INSERT INTO cuenta_vendedor (vendedor_id, tipo, origen, importe, fecha, "
                "                             concepto, liquidacion_id) "
                "VALUES (:v, 'cargo', 'faltante_mercancia', 51, CURRENT_DATE, 'otra vez', :l)"
            ),
            {"v": semilla["vendedor"], "l": uuid.UUID(liq)},
        )
    await sesion.rollback()


async def test_la_cuenta_no_viaja_al_telefono(cliente, sesion, semilla, dia_de_trabajo):
    """Trae costos, y el costo no sale de la oficina (ADR 0002 §41)."""
    await _costo(sesion, dia_de_trabajo["producto"], "8.5000")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _arqueo(cliente, liq, "2000.00")
    await _cerrar(cliente, liq)
    assert len(await _movimientos(sesion, semilla["vendedor"])) == 2

    fugas = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM change_log "
                " WHERE entidad LIKE '%cuenta%' OR payload::text LIKE '%8.5%'"
            )
        )
    ).scalar_one()
    assert fugas == 0


# ===========================================================================
# 5. La pantalla, los abonos y quién puede
# ===========================================================================
async def test_el_corte_cerrado_dice_cuanto_se_cargo(cliente, sesion, semilla, dia_de_trabajo):
    await _costo(sesion, dia_de_trabajo["producto"], "8.5000")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "54")
    await _arqueo(cliente, liq, "2000.00")
    await _cerrar(cliente, liq)

    r = await cliente.get(f"/panel/liquidaciones/{liq}")
    assert 'id="cargos_del_corte"' in r.text
    assert "Se le cargaron $301.00 al vendedor" in solo_texto(r)


async def test_la_lista_ordena_por_lo_que_deben(cliente, sesion, semilla):
    await _un_cargo(sesion, semilla, "420.00")
    await _entrar(cliente)
    r = await cliente.get("/panel/vendedores/cuenta")
    assert r.status_code == 200
    plano = solo_texto(r)
    assert "Juan Pérez" in plano
    assert "$420.00" in plano


async def _registrar(cliente, vendedor, **campos):
    pagina = await cliente.get(f"/panel/vendedores/cuenta/{vendedor}")
    return await cliente.post(
        f"/panel/vendedores/cuenta/{vendedor}/movimiento",
        data={"csrf": _csrf(cliente, pagina), **campos},
        follow_redirects=True,
    )


async def test_el_descuento_de_nomina_baja_el_saldo(cliente, sesion, semilla):
    await _un_cargo(sesion, semilla, "420.00")
    await _entrar(cliente)
    r = await _registrar(
        cliente,
        semilla["vendedor"],
        origen="descuento_nomina",
        importe="200",
        concepto="Quincena del 15/10",
    )
    assert "Ahora debe $220.00" in solo_texto(r)
    # Y el saldo corrido de la tabla termina donde debe.
    assert "$220.00" in solo_texto(r)


async def test_un_abono_no_le_fabrica_saldo_a_favor(cliente, sesion, semilla):
    await _un_cargo(sesion, semilla, "100.00")
    await _entrar(cliente)
    r = await _registrar(
        cliente, semilla["vendedor"], origen="pago", importe="1000", concepto="Pagó"
    )
    assert "no puede dejarle saldo a favor" in solo_texto(r)
    assert len(await _movimientos(sesion, semilla["vendedor"])) == 1


async def test_condonar_exige_el_porque(cliente, sesion, semilla):
    await _un_cargo(sesion, semilla, "100.00")
    await _entrar(cliente)
    r = await _registrar(
        cliente, semilla["vendedor"], origen="condonacion", importe="100", concepto="  "
    )
    assert "Escribe el concepto" in solo_texto(r)

    r = await _registrar(
        cliente,
        semilla["vendedor"],
        origen="condonacion",
        importe="100",
        concepto="La caja llegó rota desde la bodega",
    )
    assert "Ahora debe $0.00" in solo_texto(r)
    fila = (
        await sesion.execute(
            text(
                "SELECT tipo, registrado_por FROM cuenta_vendedor "
                " WHERE origen = 'condonacion'"
            )
        )
    ).mappings().one()
    assert fila["tipo"] == "condonacion"
    assert fila["registrado_por"] == semilla["admin"]


async def test_un_cargo_a_mano_sube_el_saldo(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await _registrar(
        cliente,
        semilla["vendedor"],
        origen="cargo_manual",
        importe="80",
        concepto="Llanta ponchada por manejar en la banqueta",
    )
    assert "Ahora debe $80.00" in solo_texto(r)


async def _usuario(sesion, codigo: str, rol: str) -> None:
    from app.core.seguridad import hashear_password

    sucursal = (await sesion.execute(text("SELECT id FROM sucursales LIMIT 1"))).scalar_one()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, :c, :c, :h, :r, now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": sucursal,
            "c": codigo,
            "h": hashear_password(PASSWORD_VENDEDOR),
            "r": rol,
        },
    )
    await sesion.commit()


async def test_el_supervisor_ve_pero_no_mueve(cliente, sesion, semilla):
    await _un_cargo(sesion, semilla, "100.00")
    await _usuario(sesion, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    pagina = await cliente.get(f"/panel/vendedores/cuenta/{semilla['vendedor']}")
    assert pagina.status_code == 200
    assert "forma_movimiento" not in pagina.text
    r = await cliente.post(
        f"/panel/vendedores/cuenta/{semilla['vendedor']}/movimiento",
        data={
            "csrf": _csrf(cliente),
            "origen": "condonacion",
            "importe": "100",
            "concepto": "me cae bien",
        },
    )
    assert r.status_code == 403
    assert len(await _movimientos(sesion, semilla["vendedor"])) == 1


async def test_gerencia_mueve_la_cuenta(cliente, sesion, semilla):
    await _un_cargo(sesion, semilla, "100.00")
    await _usuario(sesion, "GER01", "gerente")
    await _entrar(cliente, "GER01")
    await _registrar(
        cliente, semilla["vendedor"], origen="pago", importe="100", concepto="Pagó"
    )
    assert len(await _movimientos(sesion, semilla["vendedor"])) == 2


async def test_solo_gerencia_mueve_y_ningun_vendedor_ve(sesion, semilla):
    """El vendedor ni siquiera entra al panel; aun así, el permiso no es suyo."""
    filas = (
        await sesion.execute(
            text(
                "SELECT rol_codigo, permiso_codigo FROM roles_permisos "
                " WHERE permiso_codigo LIKE 'vendedores.cuenta%' "
                " ORDER BY rol_codigo, permiso_codigo"
            )
        )
    ).all()
    assert [tuple(f) for f in filas] == [
        ("gerente", "vendedores.cuenta_mover"),
        ("gerente", "vendedores.cuenta_ver"),
        ("supervisor", "vendedores.cuenta_ver"),
    ]


# ===========================================================================
# 6. El cliente sí pagó; el dinero no llegó
# ===========================================================================
async def test_el_cobro_no_entregado_abona_al_cliente_y_carga_al_vendedor(
    cliente, sesion, semilla
):
    from tests.test_cobro_ingesta import _aplicar, _payload, _saldos, sembrar_escenario

    escenario = await sembrar_escenario(sesion, semilla)
    cobro_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        _payload(escenario, importe="800.00", forma_pago="transferencia", referencia="X1"),
    )
    await sesion.commit()

    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/cobranza/{cobro_id}")
    assert "forma_no_entregado" in pagina.text
    r = await cliente.post(
        f"/panel/cobranza/{cobro_id}/no-entregado",
        data={
            "csrf": _csrf(cliente, pagina),
            "motivo": "El cliente enseñó su recibo; la transferencia nunca existió",
        },
        follow_redirects=True,
    )
    assert "se le cargaron al vendedor" in solo_texto(r)

    # Al cliente se le abonó: pagó de buena fe.
    saldos = await _saldos(sesion, escenario["cliente"])
    assert saldos[escenario["factura_vencida"]]["estado"] == "liquidada"
    # Al vendedor se le cargó.
    movimientos = await _movimientos(sesion, semilla["vendedor"])
    assert [m["origen"] for m in movimientos] == ["cobro_no_entregado"]
    assert movimientos[0]["importe"] == Decimal("800.00")
    assert movimientos[0]["cobro_id"] == cobro_id

    # Y no se puede hacer dos veces.
    r = await cliente.post(
        f"/panel/cobranza/{cobro_id}/no-entregado",
        data={"csrf": _csrf(cliente), "motivo": "otra vez"},
        follow_redirects=True,
    )
    assert "sigue por confirmar" in solo_texto(r)
    assert len(await _movimientos(sesion, semilla["vendedor"])) == 1


async def test_fecha_del_movimiento_por_omision_es_hoy(cliente, sesion, semilla):
    await _entrar(cliente)
    await _registrar(
        cliente, semilla["vendedor"], origen="cargo_manual", importe="10", concepto="x"
    )
    fecha = (await sesion.execute(text("SELECT fecha FROM cuenta_vendedor"))).scalar_one()
    assert isinstance(fecha, date)
