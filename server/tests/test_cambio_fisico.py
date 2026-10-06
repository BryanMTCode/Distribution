"""El cambio físico: fresco por caducado, sin dinero de por medio (migración 0040).

Regla de la dirección (octubre 2026): el producto fresco sale del camión
**legalmente**, el malo regresa como merma, y al vendedor **no se le descuadra el
arqueo ni se le exige un cobro**.

Lo que se rompe si esto falla:

1. Sin el documento, el fresco que se le dio al cliente aparece en el Corte como
   faltante y se le carga al vendedor a costo.
2. Si el cambio se cargara como merma «a su cargo», se le cobraría un producto
   que se echó a perder en la tienda del cliente.
3. Si exigiera dinero, el arqueo esperaría un cobro que no existió.
4. Sin cliente, un «cambio» es la forma perfecta de sacar mercancía sin rastro.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.sync.sobres import CodigoError
from app.infra.sync.manejadores import Contexto, ErrorDeManejador, obtener_manejador
from tests.conftest import solo_texto
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
    """240 piezas cargadas, 180 vendidas de contado por $2,250: quedan 60."""
    return await sembrar_dia_de_trabajo(sesion, semilla)


def _contexto(dia: dict, semilla: dict) -> Contexto:
    return Contexto(
        dispositivo_id=dia["dispositivo"],
        usuario_id=semilla["vendedor"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )


async def _registrar(sesion, semilla, dia, *, tipo="cambio", motivo="CADUCADO",
                     cantidad="4", cliente=True, folio=1) -> uuid.UUID:
    merma_id = uuid.uuid4()
    await obtener_manejador("merma.crear")(
        sesion,
        _contexto(dia, semilla),
        merma_id,
        {
            "folio_consecutivo": folio,
            "folio_local": f"VEND01-M{folio:05d}",
            "tipo": tipo,
            "motivo_codigo": motivo,
            "cliente_id": str(dia["cliente"]) if cliente else None,
            "fecha_dispositivo": f"{dia['dia'].isoformat()}T17:00:00.000Z",
            "fecha_operativa": dia["dia"].isoformat(),
            "detalle": [
                {"id": str(uuid.uuid4()), "producto_id": str(dia["producto"]),
                 "cantidad_base": cantidad},
            ],
        },
    )
    await sesion.commit()
    return merma_id


async def _en_camion(sesion, semilla, producto) -> Decimal:
    return Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = :p"
                ),
                {"a": semilla["camion"], "p": producto},
            )
        ).scalar_one()
    )


# ===========================================================================
# 1. El documento
# ===========================================================================
async def test_el_fresco_sale_del_camion_con_documento(sesion, semilla, dia_de_trabajo):
    antes = await _en_camion(sesion, semilla, dia_de_trabajo["producto"])
    merma_id = await _registrar(sesion, semilla, dia_de_trabajo, cantidad="4")

    assert await _en_camion(sesion, semilla, dia_de_trabajo["producto"]) == antes - 4
    fila = (
        await sesion.execute(
            text("SELECT tipo, cliente_id FROM mermas WHERE id = :id"), {"id": merma_id}
        )
    ).mappings().one()
    assert fila["tipo"] == "cambio"
    assert fila["cliente_id"] == dia_de_trabajo["cliente"]
    # Y el movimiento en el libro mayor, documentado como la merma que es.
    movimiento = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, cantidad FROM movimientos_inventario "
                " WHERE documento_id = :d"
            ),
            {"d": merma_id},
        )
    ).mappings().one()
    assert movimiento["tipo"] == "merma"
    assert movimiento["almacen_origen_id"] == semilla["camion"]
    assert movimiento["cantidad"] == Decimal("4")


async def test_el_malo_va_al_almacen_de_merma(sesion, semilla, dia_de_trabajo):
    almacen_merma = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo) "
            "VALUES (:a, 'MERMA_01', 'Almacén de merma', 'merma')"
        ),
        {"a": almacen_merma},
    )
    await sesion.commit()
    await _registrar(sesion, semilla, dia_de_trabajo, cantidad="3")
    en_merma = (
        await sesion.execute(
            text(
                "SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"
            ),
            {"a": almacen_merma, "p": dia_de_trabajo["producto"]},
        )
    ).scalar_one()
    assert en_merma == Decimal("3")


async def test_un_cambio_sin_cliente_se_rechaza(sesion, semilla, dia_de_trabajo):
    """Sin a quién se le cambió, es mercancía que salió sin rastro."""
    with pytest.raises(ErrorDeManejador) as e:
        await _registrar(sesion, semilla, dia_de_trabajo, cliente=False)
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO
    await sesion.rollback()


async def test_la_base_exige_cliente_en_un_cambio(sesion, semilla, dia_de_trabajo):
    with pytest.raises(IntegrityError, match="devolucion_requiere_cliente"):
        await sesion.execute(
            text(
                """
                INSERT INTO mermas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    tipo, almacen_id, vendedor_id, motivo_codigo,
                                    fecha_dispositivo, fecha_operativa)
                VALUES (:id, :d, 99, 'X-99', 'cambio', :a, :v, 'CADUCADO', now(),
                        CURRENT_DATE)
                """
            ),
            {
                "id": uuid.uuid4(),
                "d": dia_de_trabajo["dispositivo"],
                "a": semilla["camion"],
                "v": semilla["vendedor"],
            },
        )
    await sesion.rollback()


# ===========================================================================
# 2. El Corte del día: ni faltante, ni cargo, ni cobro
# ===========================================================================
async def _costo(sesion, producto) -> None:
    await sesion.execute(
        text("INSERT INTO producto_costos (producto_id, costo_promedio) VALUES (:p, 8.5)"),
        {"p": producto},
    )
    await sesion.commit()


async def test_EL_CAMBIO_NO_ES_FALTANTE_NI_SE_LE_CARGA(
    cliente, sesion, semilla, dia_de_trabajo
):
    """Quedaban 60; se cambiaron 4 y se cuentan 56: cuadra, y nada a su cuenta.

    Con un motivo que en una merma SÍ se le cobraría («Empaque roto»): el
    producto se echó a perder en la tienda, no en su camión.
    """
    await _costo(sesion, dia_de_trabajo["producto"])
    await _registrar(sesion, semilla, dia_de_trabajo, motivo="ROTO", cantidad="4")

    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "56")
    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")
    await cliente.post(
        f"/panel/liquidaciones/{liq}/efectivo",
        data={"csrf": _csrf(cliente, detalle), "efectivo_entregado": "2250.00"},
    )
    await _cerrar(cliente, liq)

    renglon = (
        await sesion.execute(
            text(
                "SELECT cant_merma, diferencia FROM liquidacion_detalle "
                " WHERE liquidacion_id = :l"
            ),
            {"l": uuid.UUID(liq)},
        )
    ).mappings().one()
    assert renglon["cant_merma"] == Decimal("4")
    assert renglon["diferencia"] == Decimal("0")
    cargos = (
        await sesion.execute(
            text("SELECT count(*) FROM cuenta_vendedor WHERE vendedor_id = :v"),
            {"v": semilla["vendedor"]},
        )
    ).scalar_one()
    assert cargos == 0


async def test_sin_registrarlo_el_fresco_seria_faltante_suyo(
    cliente, sesion, semilla, dia_de_trabajo
):
    """El contraste: los mismos 4 entregados SIN documento se le cobran a costo."""
    await _costo(sesion, dia_de_trabajo["producto"])
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    await _contar(cliente, liq, sesion, "56")
    detalle = await cliente.get(f"/panel/liquidaciones/{liq}")
    await cliente.post(
        f"/panel/liquidaciones/{liq}/efectivo",
        data={"csrf": _csrf(cliente, detalle), "efectivo_entregado": "2250.00"},
    )
    await _cerrar(cliente, liq)
    importe = (
        await sesion.execute(
            text("SELECT importe FROM cuenta_vendedor WHERE origen = 'faltante_mercancia'")
        )
    ).scalar_one()
    assert importe == Decimal("34.00")  # 4 × 8.50


async def test_el_cambio_no_mueve_el_arqueo(cliente, sesion, semilla, dia_de_trabajo):
    """Ni un peso esperado de más: el cliente no pagó nada."""
    await _registrar(sesion, semilla, dia_de_trabajo, cantidad="4")
    await _entrar(cliente)
    liq = await _abrir(cliente, dia_de_trabajo["carga"])
    r = await cliente.get(f"/panel/liquidaciones/{liq}")
    assert "$2,250.00" in solo_texto(r)
    esperado = (
        await sesion.execute(
            text("SELECT efectivo_esperado FROM liquidaciones WHERE id = :l"),
            {"l": uuid.UUID(liq)},
        )
    ).scalar_one()
    assert esperado == Decimal("2250.00")


# ===========================================================================
# 3. Dónde lo ve la oficina
# ===========================================================================
async def test_efectividad_muestra_a_quien_se_le_cambia(
    cliente, sesion, semilla, dia_de_trabajo
):
    await _registrar(sesion, semilla, dia_de_trabajo, cantidad="4")
    await _entrar(cliente)
    dia = dia_de_trabajo["dia"].isoformat()
    r = await cliente.get(f"/panel/efectividad?desde={dia}&hasta={dia}")
    plano = solo_texto(r)
    assert "Cambios físicos" in plano
    assert "La Esquina" in plano
    assert "Atún en agua 140 g" in plano
    assert "nunca se le cobra al vendedor" in plano
