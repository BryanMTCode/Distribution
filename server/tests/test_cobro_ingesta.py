"""Fase 5 · El cobro que llega del teléfono, y su aplicación FIFO.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Aquí entra **dinero**, y el dinero no se puede recontar. Una venta entrega
mercancía que se cuenta al final del día; un cobro recibe efectivo, y el efectivo
se cuadra. Si este manejador rechaza un cobro legítimo, el dinero existe en la
bolsa del vendedor y no en el sistema, y en la liquidación aparece como un
descuadre que nadie puede explicar.

Por eso §0.1 es aquí más estricto que en la venta: **nada se rechaza salvo un
payload que no describe ningún cobro posible.**

Cuatro cosas que se rompen con consecuencias de dinero:

1. **Reenviar el sobre no puede abonar dos veces.** Es la peor consecuencia
   posible de un reintento en este manejador.
2. **El FIFO** tiene que pagar primero lo más viejo, que es lo que reduce el
   riesgo real de la cartera.
3. **Cobrar de más no se rechaza**: se registra como saldo a favor y se marca.
4. **El saldo que trae el teléfono es forense, no autoridad.** Si decidiera algo,
   un equipo con el saldo viejo cobraría mal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sync.sobres import CodigoError
from app.infra.sync.manejadores import (
    MOTIVO_EXCEDE_DEUDA,
    MOTIVO_SALDO_DESFASADO,
    MOTIVO_SIN_DEUDA,
    Contexto,
    ErrorDeManejador,
    obtener_manejador,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def escenario(sesion: AsyncSession, semilla: dict) -> dict:
    return await sembrar_escenario(sesion, semilla)


async def sembrar_escenario(sesion: AsyncSession, semilla: dict) -> dict:
    """Un cliente con dos facturas abiertas: $800 vencida y $1,200 por vencer.

    Función aparte del fixture para que otros módulos la reusen
    (`test_cobros_por_confirmar.py`) sin importar un fixture por su nombre.

    El orden importa para el FIFO, y la que vence antes NO es la que se emitió
    antes: así la prueba distingue "ordenar por vencimiento" de "ordenar por
    emisión", que es el error fácil.
    """
    dispositivo = uuid.uuid4()
    cliente = uuid.uuid4()
    vieja, nueva = uuid.uuid4(), uuid.uuid4()
    hoy = datetime.now(UTC).date()

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
            "                      permite_credito, limite_credito, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'C00001', 'Abarrotes Doña Mary', :r, true, 5000, now(), now())"
        ),
        {"c": cliente, "r": semilla["ruta"]},
    )

    async def factura(venta_id, importe, emitida, vence):
        await sesion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, almacen_id, tipo,
                                    subtotal, total, fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, :folio, :folio_local, :c, :u, :a, 'credito',
                        :importe, :importe, now(), :dia)
                """
            ),
            {
                "v": venta_id,
                "d": dispositivo,
                "folio": abs(hash(str(venta_id))) % 100000,
                "folio_local": f"VEND01-{str(venta_id)[:6]}",
                "c": cliente,
                "u": semilla["vendedor"],
                "a": semilla["camion"],
                "importe": importe,
                "dia": emitida,
            },
        )
        await sesion.execute(
            text(
                """
                INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                                fecha_emision, fecha_vencimiento, estado)
                VALUES (:v, :c, :importe, :emitida, :vence, 'abierta')
                """
            ),
            {
                "v": venta_id,
                "c": cliente,
                "importe": importe,
                "emitida": emitida,
                "vence": vence,
            },
        )

    # La vencida se emitió DESPUÉS, pero vence antes: a 0 días contra 15.
    await factura(nueva, Decimal("1200.00"), hoy - timedelta(days=20), hoy + timedelta(days=15))
    await factura(vieja, Decimal("800.00"), hoy - timedelta(days=5), hoy - timedelta(days=1))
    await sesion.commit()

    return {
        "dispositivo": dispositivo,
        "cliente": cliente,
        "factura_vencida": vieja,
        "factura_por_vencer": nueva,
        "dia": hoy,
    }


def _contexto(escenario: dict, semilla: dict) -> Contexto:
    return Contexto(
        dispositivo_id=escenario["dispositivo"],
        usuario_id=semilla["vendedor"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )


def _payload(escenario: dict, **campos) -> dict:
    datos = {
        "folio_consecutivo": 1,
        "folio_local": "VEND01-000001",
        "cliente_id": str(escenario["cliente"]),
        "importe": "500.00",
        "forma_pago": "efectivo",
        "fecha_dispositivo": "2026-09-29T17:42:03.250Z",
        "fecha_operativa": escenario["dia"].isoformat(),
    }
    datos.update(campos)
    return datos


async def _aplicar(sesion, escenario, semilla, datos, cobro_id=None) -> uuid.UUID:
    manejador = obtener_manejador("cobro.crear")
    identificador = cobro_id or uuid.uuid4()
    await manejador(sesion, _contexto(escenario, semilla), identificador, datos)
    return identificador


async def _leer_cobro(sesion, cobro_id) -> dict:
    return dict(
        (
            await sesion.execute(
                text("SELECT * FROM cobros WHERE id = :id"), {"id": cobro_id}
            )
        ).mappings().one()
    )


async def _saldos(sesion, cliente) -> dict:
    filas = (
        await sesion.execute(
            text(
                "SELECT venta_id, importe_pagado, saldo, estado "
                "  FROM cuentas_por_cobrar WHERE cliente_id = :c"
            ),
            {"c": cliente},
        )
    ).mappings().all()
    return {f["venta_id"]: dict(f) for f in filas}


# ---------------------------------------------------------------------------
# Lo básico
# ---------------------------------------------------------------------------


async def test_el_cobro_se_registra_completo(sesion, semilla, escenario):
    cobro_id = await _aplicar(sesion, escenario, semilla, _payload(escenario))
    await sesion.commit()

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["importe"] == Decimal("500.00")
    assert cobro["forma_pago"] == "efectivo"
    assert cobro["estado"] == "confirmado"
    assert cobro["folio_local"] == "VEND01-000001"
    # El vendedor y el equipo salen del CONTEXTO, nunca del payload: si el
    # dispositivo pudiera declararlos, un equipo comprometido escribiría a nombre
    # de cualquier otro vendedor.
    assert cobro["vendedor_id"] == semilla["vendedor"]
    assert cobro["dispositivo_id"] == escenario["dispositivo"]


async def test_REENVIAR_EL_SOBRE_NO_ABONA_DOS_VECES(sesion, semilla, escenario):
    """Es la peor consecuencia posible de un reintento en este manejador.

    El UUID lo generó el teléfono y es la llave primaria, así que la idempotencia
    es por construcción — pero hay que comprobarla, porque el daño sería invisible:
    el cliente quedaría con saldo a favor y nadie sabría por qué.
    """
    cobro_id = uuid.uuid4()
    await _aplicar(sesion, escenario, semilla, _payload(escenario), cobro_id)
    await sesion.commit()
    await _aplicar(sesion, escenario, semilla, _payload(escenario), cobro_id)
    await sesion.commit()

    cuantos = (
        await sesion.execute(text("SELECT count(*) FROM cobros"))
    ).scalar_one()
    assert cuantos == 1

    aplicaciones = (
        await sesion.execute(
            text("SELECT count(*), COALESCE(sum(importe), 0) FROM cobros_aplicaciones")
        )
    ).one()
    assert aplicaciones[0] == 1
    assert aplicaciones[1] == Decimal("500.00")


# ---------------------------------------------------------------------------
# El FIFO
# ---------------------------------------------------------------------------


async def test_EL_FIFO_PAGA_PRIMERO_LO_QUE_VENCE_ANTES(sesion, semilla, escenario):
    """Pagar primero lo más vencido es lo que reduce el riesgo real de la cartera.

    La factura que vence antes se emitió **después**, así que esta prueba
    distingue "ordenar por vencimiento" de "ordenar por emisión" — el error fácil.
    """
    await _aplicar(sesion, escenario, semilla, _payload(escenario, importe="500.00"))
    await sesion.commit()

    saldos = await _saldos(sesion, escenario["cliente"])
    vencida = saldos[escenario["factura_vencida"]]
    por_vencer = saldos[escenario["factura_por_vencer"]]

    assert vencida["importe_pagado"] == Decimal("500.00")
    assert vencida["saldo"] == Decimal("300.00")
    assert vencida["estado"] == "parcial"
    # La otra no se tocó.
    assert por_vencer["importe_pagado"] == Decimal("0.00")
    assert por_vencer["estado"] == "abierta"


async def test_el_abono_que_sobra_pasa_a_la_siguiente_factura(
    sesion, semilla, escenario
):
    """$1,000 sobre una deuda de $800 + $1,200: liquida la primera y abona 200."""
    cobro_id = await _aplicar(
        sesion, escenario, semilla, _payload(escenario, importe="1000.00")
    )
    await sesion.commit()

    saldos = await _saldos(sesion, escenario["cliente"])
    assert saldos[escenario["factura_vencida"]]["estado"] == "liquidada"
    assert saldos[escenario["factura_vencida"]]["saldo"] == Decimal("0.00")
    assert saldos[escenario["factura_por_vencer"]]["importe_pagado"] == Decimal("200.00")
    assert saldos[escenario["factura_por_vencer"]]["estado"] == "parcial"

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["importe_aplicado"] == Decimal("1000.00")
    assert cobro["saldo_a_favor"] == Decimal("0.00")

    # Y queda el rastro de a qué se aplicó: dos renglones.
    aplicaciones = (
        await sesion.execute(
            text(
                "SELECT venta_id, importe FROM cobros_aplicaciones "
                " WHERE cobro_id = :c ORDER BY importe DESC"
            ),
            {"c": cobro_id},
        )
    ).mappings().all()
    assert len(aplicaciones) == 2
    assert aplicaciones[0]["importe"] == Decimal("800.00")
    assert aplicaciones[1]["importe"] == Decimal("200.00")


async def test_liquidar_toda_la_deuda_deja_las_dos_facturas_en_cero(
    sesion, semilla, escenario
):
    cobro_id = await _aplicar(
        sesion, escenario, semilla, _payload(escenario, importe="2000.00")
    )
    await sesion.commit()

    saldos = await _saldos(sesion, escenario["cliente"])
    assert all(f["estado"] == "liquidada" for f in saldos.values())
    assert all(f["saldo"] == Decimal("0.00") for f in saldos.values())

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["importe_aplicado"] == Decimal("2000.00")
    assert cobro["saldo_a_favor"] == Decimal("0.00")
    assert cobro["requiere_revision"] is False


# ---------------------------------------------------------------------------
# Cobrar de más
# ---------------------------------------------------------------------------


async def test_COBRAR_DE_MAS_NO_SE_RECHAZA_Y_QUEDA_COMO_SALDO_A_FAVOR(
    sesion, semilla, escenario
):
    """El cliente pudo liquidar y dejar anticipo, o el saldo del teléfono estaba
    viejo. **El dinero ya cambió de manos.**

    Rechazarlo haría que el vendedor se guardara efectivo sin documento, que es
    exactamente el problema que este manejador existe para evitar.
    """
    cobro_id = await _aplicar(
        sesion, escenario, semilla, _payload(escenario, importe="2500.00")
    )
    await sesion.commit()

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["importe"] == Decimal("2500.00")
    assert cobro["importe_aplicado"] == Decimal("2000.00")
    assert cobro["saldo_a_favor"] == Decimal("500.00")
    # Se marca para que la oficina decida si es anticipo o devolución.
    assert cobro["requiere_revision"] is True
    assert MOTIVO_EXCEDE_DEUDA in cobro["revision_motivos"]


async def test_cobrar_a_un_cliente_sin_deuda_entra_y_se_marca(
    sesion, semilla, escenario
):
    """Todo queda como saldo a favor. No es un error: el dinero entró."""
    await sesion.execute(
        text(
            "UPDATE cuentas_por_cobrar "
            "   SET estado = 'liquidada', importe_pagado = importe_original"
        )
    )
    await sesion.commit()

    cobro_id = await _aplicar(sesion, escenario, semilla, _payload(escenario))
    await sesion.commit()

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["importe_aplicado"] == Decimal("0.00")
    assert cobro["saldo_a_favor"] == Decimal("500.00")
    assert MOTIVO_SIN_DEUDA in cobro["revision_motivos"]
    # Y no se inventó ninguna aplicación.
    assert (
        await sesion.execute(text("SELECT count(*) FROM cobros_aplicaciones"))
    ).scalar_one() == 0


# ---------------------------------------------------------------------------
# El saldo del teléfono es forense
# ---------------------------------------------------------------------------


async def test_el_saldo_del_equipo_se_guarda_pero_NO_decide_nada(
    sesion, semilla, escenario
):
    """Si decidiera algo, un equipo con el saldo viejo cobraría mal.

    Se guarda para poder explicar después por qué el vendedor cobró lo que cobró.
    """
    cobro_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        _payload(escenario, importe="500.00", saldo_cache_disp="99.00"),
    )
    await sesion.commit()

    cobro = await _leer_cobro(sesion, cobro_id)
    assert cobro["saldo_cache_disp"] == Decimal("99.00")
    # El abono se aplicó por los 500 reales, no por los 99 que creía el teléfono.
    assert cobro["importe_aplicado"] == Decimal("500.00")


async def test_un_saldo_del_equipo_muy_desfasado_se_marca(sesion, semilla, escenario):
    """Una diferencia grande significa que el equipo llevaba horas sin
    sincronizar, y explica por qué el vendedor cobró lo que cobró."""
    cobro_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        _payload(escenario, saldo_cache_disp="10000.00"),
    )
    await sesion.commit()

    cobro = await _leer_cobro(sesion, cobro_id)
    assert MOTIVO_SALDO_DESFASADO in cobro["revision_motivos"]


async def test_un_desfase_pequeno_no_molesta(sesion, semilla, escenario):
    """El saldo del teléfono SIEMPRE va a diferir un poco: es una caché. Marcar
    cada diferencia convertiría la bandera en ruido."""
    cobro_id = await _aplicar(
        sesion, escenario, semilla, _payload(escenario, saldo_cache_disp="2050.00")
    )
    await sesion.commit()

    cobro = await _leer_cobro(sesion, cobro_id)
    assert MOTIVO_SALDO_DESFASADO not in cobro["revision_motivos"]


# ---------------------------------------------------------------------------
# Lo único que se rechaza
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("importe", ["0.00", "-500.00"])
async def test_un_importe_que_no_es_un_cobro_se_rechaza(
    sesion, semilla, escenario, importe
):
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(sesion, escenario, semilla, _payload(escenario, importe=importe))
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO
    assert "no es un cobro" in str(e.value)


async def test_una_forma_de_pago_inventada_se_rechaza(sesion, semilla, escenario):
    """El arqueo de la liquidación suma solo el efectivo. Una forma de pago libre
    dejaría cobros fuera de la suma y el cuadre fallaría por un dato que se ve
    bien."""
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(
            sesion, escenario, semilla, _payload(escenario, forma_pago="bitcoin")
        )
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO


async def test_el_dinero_como_numero_JSON_se_rechaza(sesion, semilla, escenario):
    """Un `float` en el payload es un error, no una advertencia
    (contracts/README.md §1.4): el dinero viaja como string."""
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(sesion, escenario, semilla, _payload(escenario, importe=500.0))
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO


async def test_un_cobro_de_un_cliente_que_no_existe_se_rechaza(
    sesion, semilla, escenario
):
    """Un cobro sin cliente no se puede aplicar ni auditar."""
    with pytest.raises(ErrorDeManejador) as e:
        await _aplicar(
            sesion, escenario, semilla, _payload(escenario, cliente_id=str(uuid.uuid4()))
        )
    assert e.value.codigo == CodigoError.CONFLICTO_DE_DATOS


async def test_un_cobro_de_otra_ruta_se_rechaza(sesion, semilla, escenario):
    """El alcance por ruta se verifica aquí, no en la UI."""
    ctx = Contexto(
        dispositivo_id=escenario["dispositivo"],
        usuario_id=semilla["vendedor"],
        rutas=(uuid.uuid4(),),
    )
    manejador = obtener_manejador("cobro.crear")
    with pytest.raises(ErrorDeManejador) as e:
        await manejador(sesion, ctx, uuid.uuid4(), _payload(escenario))
    assert e.value.codigo == CodigoError.CONFLICTO_DE_DATOS


# ---------------------------------------------------------------------------
# El delta que devuelve el saldo nuevo al teléfono
# ---------------------------------------------------------------------------


async def test_EL_COBRO_PUBLICA_EL_DELTA_DE_CARTERA(sesion, semilla, escenario):
    """Sin esto el teléfono seguiría mostrando la deuda completa.

    El disparador de la migración 0011 vive en `cuentas_por_cobrar`, así que es la
    aplicación FIFO la que lo dispara — no el INSERT del cobro. Si algún día el
    FIFO se moviera a un job nocturno, este delta dejaría de salir al cobrar y el
    vendedor vería el saldo viejo el resto de la ruta.
    """
    await sesion.execute(text("DELETE FROM change_log WHERE entidad = 'cartera'"))
    await sesion.commit()

    await _aplicar(sesion, escenario, semilla, _payload(escenario, importe="1000.00"))
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT ruta_id, payload FROM change_log "
                " WHERE entidad = 'cartera' AND entidad_id = :c "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"c": escenario["cliente"]},
        )
    ).mappings().first()
    assert fila is not None, "el cobro no publicó el saldo nuevo"
    # Acotado a la ruta: el saldo de un cliente solo le importa a su equipo.
    assert fila["ruta_id"] == semilla["ruta"]
    assert Decimal(str(fila["payload"]["saldo"])) == Decimal("1000.00")


async def test_la_vista_de_cartera_refleja_el_abono(sesion, semilla, escenario):
    """`v_cartera_cliente` es de donde sale el saldo del delta y del panel."""
    await _aplicar(sesion, escenario, semilla, _payload(escenario, importe="1500.00"))
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT saldo, disponible, facturas_abiertas, facturas_vencidas "
                "  FROM v_cartera_cliente WHERE cliente_id = :c"
            ),
            {"c": escenario["cliente"]},
        )
    ).mappings().one()

    assert fila["saldo"] == Decimal("500.00")
    assert fila["disponible"] == Decimal("4500.00")
    assert fila["facturas_abiertas"] == 1
    # La vencida se liquidó con los primeros 800.
    assert fila["facturas_vencidas"] == 0
