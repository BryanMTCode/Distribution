"""Fase 7 · La devolución de mercancía del camión a la bodega.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Antes de este documento, bajar 18 cajas de un camión eran dos ajustes
independientes: uno que restaba del camión y otro que sumaba a la bodega. Las dos
cifras acababan bien y **no quedaba nada que atara los dos lados**. El día que
alguien preguntara «¿quién las bajó y quién las recibió?», no había qué leer.

Lo que se rompe con consecuencias de campo:

1. **Que la declaración del vendedor suba la bodega.** Sería la única operación
   del sistema donde la palabra de una persona mueve dos almacenes, y un faltante
   se podría cubrir escribiendo una devolución que nunca se entregó. La mercancía
   va a TRÁNSITO y la bodega sube cuando alguien cuenta.
2. **Que un reenvío baje el camión dos veces.** El sobre se reintenta por diseño:
   la segunda vez tiene que no hacer nada.
3. **Que se rechace por falta de existencia.** La mercancía ya bajó (§0.1).
   Rechazarla la dejaría como faltante del vendedor, que es lo contrario de lo que
   este documento existe para hacer.
4. **Que el origen venga del payload.** Un dispositivo que pudiera declarar el
   almacén de salida vaciaría el camión de otro vendedor.
5. **Que un traspaso se le cobre en la liquidación.** Es el punto completo: la
   mercancía ya no está arriba y el vendedor no la debe.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sync.sobres import CodigoError
from app.infra.sync.manejadores import Contexto, ErrorDeManejador, obtener_manejador

pytestmark = pytest.mark.asyncio

MOMENTO = "2026-10-06T19:15:00.000Z"
DIA = "2026-10-06"


@pytest.fixture
async def escenario(sesion: AsyncSession, semilla: dict) -> dict:
    """Un camión con 48 piezas de atún y el dispositivo del vendedor."""
    dispositivo = uuid.uuid4()
    producto = uuid.uuid4()

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA', 0.0000)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 48)"
        ),
        {"a": semilla["camion"], "p": producto},
    )
    await sesion.commit()

    return {"dispositivo": dispositivo, "producto": producto}


def _contexto(escenario: dict, semilla: dict, **campos) -> Contexto:
    base = {
        "dispositivo_id": escenario["dispositivo"],
        "usuario_id": semilla["vendedor"],
        "rutas": (semilla["ruta"],),
        "almacen_id": semilla["camion"],
    }
    base.update(campos)
    return Contexto(**base)


def _traspaso(escenario: dict, **campos) -> dict:
    datos = {
        "dispositivo_id": str(escenario["dispositivo"]),
        "almacen_origen_id": None,
        "observaciones": "Lo que no se vendió de la ruta 4",
        "fecha_dispositivo": MOMENTO,
        "fecha_operativa": DIA,
        "detalle": [{"producto_id": str(escenario["producto"]), "cantidad": "18.000"}],
    }
    datos.update(campos)
    return datos


async def _aplicar(sesion, escenario, semilla, datos, entidad_id=None, ctx=None):
    manejador = obtener_manejador("traspaso.crear")
    identificador = entidad_id or uuid.uuid4()
    await manejador(sesion, ctx or _contexto(escenario, semilla), identificador, datos)
    return identificador


async def _existencia(sesion, almacen, producto) -> Decimal | None:
    return (
        await sesion.execute(
            text(
                "SELECT cantidad FROM existencias "
                " WHERE almacen_id = :a AND producto_id = :p"
            ),
            {"a": almacen, "p": producto},
        )
    ).scalar_one_or_none()


async def _transito(sesion) -> dict | None:
    fila = (
        await sesion.execute(
            text("SELECT id, codigo, nombre, sucursal_id FROM almacenes WHERE tipo = 'transito'")
        )
    ).mappings().first()
    return dict(fila) if fila else None


# ---------------------------------------------------------------------------
# El camión baja, la bodega NO sube
# ---------------------------------------------------------------------------


async def test_la_mercancia_sale_del_camion(sesion, semilla, escenario):
    await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    # 48 − 18 = 30.
    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "30.000"
    )


async def test_la_bodega_no_sube_con_la_sola_palabra_del_vendedor(
    sesion, semilla, escenario
):
    """La prueba que justifica que exista el tránsito.

    Si esto fallara, un vendedor con un faltante de 18 cajas podría capturar una
    devolución de 18, no entregar nada, y cuadrar: su camión baja y la bodega sube
    sin que nadie haya contado una sola caja.
    """
    await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    assert await _existencia(sesion, semilla["bodega"], escenario["producto"]) is None

    transito = await _transito(sesion)
    assert transito is not None
    assert await _existencia(sesion, transito["id"], escenario["producto"]) == Decimal(
        "18.000"
    )


async def test_el_documento_ata_los_dos_almacenes(sesion, semilla, escenario):
    """Un solo renglón de libro mayor, con origen y destino, no dos ajustes sueltos."""
    traspaso = await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    movimientos = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, almacen_destino_id, cantidad, "
                "       documento_tipo, usuario_id, dispositivo_id "
                "  FROM movimientos_inventario WHERE documento_id = :d"
            ),
            {"d": traspaso},
        )
    ).mappings().all()

    assert len(movimientos) == 1
    m = movimientos[0]
    transito = await _transito(sesion)
    assert m["tipo"] == "traspaso"
    assert m["documento_tipo"] == "traspaso"
    assert m["almacen_origen_id"] == semilla["camion"]
    assert m["almacen_destino_id"] == transito["id"]
    assert m["cantidad"] == Decimal("18.000")
    # Quién lo bajó, que es la mitad de la pregunta que este documento contesta.
    assert m["usuario_id"] == semilla["vendedor"]
    assert m["dispositivo_id"] == escenario["dispositivo"]


async def test_nace_propuesto_con_folio_del_servidor(sesion, semilla, escenario):
    """El folio lo pone el servidor: un traspaso no se le entrega a un cliente."""
    traspaso = await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT folio, estado, solicitado_por, dispositivo_id, observaciones, "
                "       fecha_operativa, resuelto_por, resuelto_en "
                "  FROM traspasos WHERE id = :t"
            ),
            {"t": traspaso},
        )
    ).mappings().one()

    assert fila["estado"] == "propuesto"
    assert fila["folio"].startswith("TR-")
    assert fila["solicitado_por"] == semilla["vendedor"]
    assert fila["dispositivo_id"] == escenario["dispositivo"]
    assert fila["observaciones"] == "Lo que no se vendió de la ruta 4"
    assert str(fila["fecha_operativa"]) == DIA
    # Nadie lo ha recibido todavía. Es la diferencia entre declarado y entregado.
    assert fila["resuelto_por"] is None
    assert fila["resuelto_en"] is None


# ---------------------------------------------------------------------------
# Idempotencia: el sobre se reintenta por diseño
# ---------------------------------------------------------------------------


async def test_reenviar_el_sobre_no_baja_el_camion_dos_veces(sesion, semilla, escenario):
    identificador = uuid.uuid4()
    await _aplicar(sesion, escenario, semilla, _traspaso(escenario), identificador)
    await sesion.commit()
    await _aplicar(sesion, escenario, semilla, _traspaso(escenario), identificador)
    await sesion.commit()

    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "30.000"
    )
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM movimientos_inventario WHERE documento_id = :d"),
            {"d": identificador},
        )
    ).scalar_one() == 1


async def test_dos_renglones_del_mismo_producto_son_uno(sesion, semilla, escenario):
    """`traspaso_detalle` es único por producto, y dos renglones no dicen nada nuevo."""
    await _aplicar(
        sesion,
        escenario,
        semilla,
        _traspaso(
            escenario,
            detalle=[
                {"producto_id": str(escenario["producto"]), "cantidad": "6.000"},
                {"producto_id": str(escenario["producto"]), "cantidad": "12.000"},
            ],
        ),
    )
    await sesion.commit()

    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "30.000"
    )


# ---------------------------------------------------------------------------
# §0.1 · la mercancía ya bajó
# ---------------------------------------------------------------------------


async def test_sin_existencia_suficiente_se_registra_igual(sesion, semilla, escenario):
    """El camión queda en negativo, y eso es una señal honesta.

    Rechazar el documento no devolvería la mercancía al camión: la dejaría como
    faltante del vendedor y haría que dejara de registrar devoluciones.
    """
    await _aplicar(
        sesion,
        escenario,
        semilla,
        _traspaso(
            escenario,
            detalle=[{"producto_id": str(escenario["producto"]), "cantidad": "60.000"}],
        ),
    )
    await sesion.commit()

    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "-12.000"
    )


async def test_un_producto_que_el_camion_no_tenia_tampoco_se_rechaza(
    sesion, semilla, escenario
):
    otro = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'GALL-200', 'Galletas 200 g', 'PZA', 0.1600)"
        ),
        {"p": otro},
    )
    await _aplicar(
        sesion,
        escenario,
        semilla,
        _traspaso(escenario, detalle=[{"producto_id": str(otro), "cantidad": "3.000"}]),
    )
    await sesion.commit()

    assert await _existencia(sesion, semilla["camion"], otro) == Decimal("-3.000")


# ---------------------------------------------------------------------------
# El origen sale del token, nunca del payload
# ---------------------------------------------------------------------------


async def test_el_payload_no_puede_elegir_de_que_camion_sale(sesion, semilla, escenario):
    """Un dispositivo que declarara el origen vaciaría el camión de otro vendedor."""
    ajeno = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo, responsable_id) "
            "VALUES (:a, 'CAMION_02', 'Camión 02', 'camion', :v)"
        ),
        {"a": ajeno, "v": semilla["admin"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 100)"
        ),
        {"a": ajeno, "p": escenario["producto"]},
    )
    await sesion.commit()

    await _aplicar(
        sesion, escenario, semilla, _traspaso(escenario, almacen_origen_id=str(ajeno))
    )
    await sesion.commit()

    # El camión ajeno quedó intacto; bajó el del token.
    assert await _existencia(sesion, ajeno, escenario["producto"]) == Decimal("100.000")
    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "30.000"
    )


async def test_sin_almacen_en_el_token_ni_en_el_payload_se_rechaza(
    sesion, semilla, escenario
):
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            _traspaso(escenario),
            ctx=_contexto(escenario, semilla, almacen_id=None),
        )
    assert e.value.codigo == CodigoError.CONFLICTO_DE_DATOS


async def test_un_traspaso_sin_renglones_se_rechaza(sesion, semilla, escenario):
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(sesion, escenario, semilla, _traspaso(escenario, detalle=[]))
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO


async def test_un_renglon_en_cero_se_rechaza(sesion, semilla, escenario):
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            _traspaso(
                escenario,
                detalle=[{"producto_id": str(escenario["producto"]), "cantidad": "0"}],
            ),
        )
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO


# ---------------------------------------------------------------------------
# El almacén de tránsito, que nadie configuró
# ---------------------------------------------------------------------------


async def test_el_transito_se_crea_si_no_existe(sesion, semilla, escenario):
    """Rechazar por falta de configuración haría de una omisión de la oficina un
    faltante del vendedor."""
    assert await _transito(sesion) is None

    await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    transito = await _transito(sesion)
    assert transito is not None


async def test_el_transito_es_de_la_sucursal_del_camion(sesion, semilla, escenario):
    """Juntar el tránsito de dos sucursales haría imposible leer qué falta por
    recibir en cada bodega."""
    await sesion.execute(
        text("UPDATE almacenes SET sucursal_id = :s WHERE id = :c"),
        {"s": semilla["sucursal"], "c": semilla["camion"]},
    )
    await sesion.commit()

    await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    transito = await _transito(sesion)
    assert transito["sucursal_id"] == semilla["sucursal"]
    assert transito["codigo"] == "TRANSITO_MATRIZ"


async def test_dos_traspasos_reusan_el_mismo_transito(sesion, semilla, escenario):
    await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()
    await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    cuantos = (
        await sesion.execute(
            text("SELECT count(*) FROM almacenes WHERE tipo = 'transito'")
        )
    ).scalar_one()
    assert cuantos == 1
    transito = await _transito(sesion)
    assert await _existencia(sesion, transito["id"], escenario["producto"]) == Decimal(
        "36.000"
    )


# ---------------------------------------------------------------------------
# La liquidación: lo que ya no está arriba no se le debe
# ---------------------------------------------------------------------------


async def test_lo_devuelto_no_se_le_cobra_al_vendedor(sesion, semilla, escenario):
    """El punto completo de esta función, medido donde duele: el cierre del día.

    La ecuación del cierre no tiene término para «traspasado», y aun así cuadra:
    `inicial` se deduce del saldo vivo del camión, así que bajar las existencias
    baja `inicial` y baja `esperado` en la misma cantidad (ver `saldo_inicial`).

    El vendedor amaneció con 48, devolvió 18 a la bodega y trae 30 arriba. Si
    `esperado` dijera 48, el cierre le cobraría 18 piezas que entregó.
    """
    from app.api.admin.liquidaciones import _con_inicial, _renglones_calculados
    from app.domain.liquidacion import esperado_en_camion

    carga = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, fecha_operativa, estado) "
            "VALUES (:c, 'CG-000900', :b, :cam, :v, :d, 'en_ruta')"
        ),
        {
            "c": carga,
            "b": semilla["bodega"],
            "cam": semilla["camion"],
            "v": semilla["vendedor"],
            "d": DIA,
        },
    )
    await sesion.commit()

    await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    await sesion.commit()

    renglones = _con_inicial(await _renglones_calculados(sesion, carga))
    renglon = next(r for r in renglones if r["producto_id"] == escenario["producto"])

    esperado = esperado_en_camion(
        renglon["inicial"],
        Decimal(renglon["cargada"]),
        Decimal(renglon["vendida"]),
        Decimal(renglon["merma"]),
        Decimal(renglon["devuelta"]),
    )
    # Lo que el sistema espera encontrar arriba es lo que de verdad hay: 30.
    assert esperado == Decimal("30.000")
    # Y contar 30 no produce faltante.
    assert Decimal("30.000") - esperado == Decimal("0.000")


async def test_un_nombre_de_transito_tomado_por_una_bodega_se_rechaza(
    sesion, semilla, escenario
):
    """El agujero que el tránsito existe para cerrar, por la puerta del nombre.

    `almacenes.codigo` es único en toda la tabla. Si alguien bautizó una BODEGA como
    `TRANSITO_MATRIZ`, el `ON CONFLICT (codigo)` del alta devolvería esa bodega y la
    mercancía del camión entraría derecho a ella sin que nadie contara nada. Se va a
    cuarentena con un mensaje que dice qué renombrar.
    """
    await sesion.execute(
        text("UPDATE almacenes SET sucursal_id = :s WHERE id = :c"),
        {"s": semilla["sucursal"], "c": semilla["camion"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO almacenes (codigo, nombre, tipo, sucursal_id) "
            "VALUES ('TRANSITO_MATRIZ', 'Una bodega mal bautizada', 'bodega', :s)"
        ),
        {"s": semilla["sucursal"]},
    )
    await sesion.commit()

    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(sesion, escenario, semilla, _traspaso(escenario))
    assert e.value.codigo == CodigoError.CONFLICTO_DE_DATOS
    assert "renómbralo" in e.value.mensaje


async def test_un_origen_que_no_es_camion_se_rechaza(sesion, semilla, escenario):
    """Este documento describe UNA cosa: mercancía que baja de un camión.

    Si el almacén del token fuera una bodega, el traspaso quedaría parado en tránsito
    sin pantalla que lo reciba —la lista de recepción solo mira orígenes de tipo
    camión— y la mercancía desaparecería de los dos lados sin que nadie lo notara. Un
    sobre en cuarentena se ve; un traspaso varado, no.
    """
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            _traspaso(escenario),
            ctx=_contexto(escenario, semilla, almacen_id=semilla["bodega"]),
        )
    assert e.value.codigo == CodigoError.CONFLICTO_DE_DATOS
    assert "no es un camión" in e.value.mensaje
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM almacenes WHERE tipo = 'transito'")
        )
    ).scalar_one() == 0, "ni se crea el tránsito para un origen que no procede"
