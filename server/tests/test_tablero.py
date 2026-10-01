"""El tablero de Gerencia: los modelos de lectura y lo que el teléfono recibe.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Un tablero equivocado es peor que ningún tablero: nadie verifica una cifra que
sale de una pantalla de monitoreo, se decide con ella. Las seis formas de que
este dé un número que *parece* bien:

1. **Contar documentos en vez de visitas.** Dos remisiones al mismo cliente el
   mismo día son UNA visita. La misma definición que `fact_visitas` y que la
   pantalla de efectividad; si las tres no coinciden, las tres son inservibles.

2. **Perder al vendedor que solo cobró.** Un `JOIN` desde ventas desaparecería
   del tablero al vendedor que no vendió nada — justo el que hay que ver.

3. **Dejar vivo un renglón cuyo documento se canceló.** Con
   `ON CONFLICT DO UPDATE`, el vendedor cuya única venta se canceló seguiría en
   el tablero con la cifra anterior.

4. **No detectar un día rancio.** Una venta del lunes que sincroniza el jueves
   tiene que dejar rancio el LUNES. Si se recalculara el jueves, el lunes
   quedaría subreportado para siempre (§0.3).

5. **Prorratear el avance contra el objetivo sin decir qué se espera.** 67% el
   día 10 es excelente y el día 28 es un problema, y el número es el mismo.

6. **Presentar la antigüedad sin el estado del mundo.** "Hace 2 minutos" suena
   perfecto, y si en ese minuto dos equipos no habían sincronizado, el total del
   día es un PISO. La tarjeta tiene que decirlo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.tablero import Avance, Frescura, inicio_de_mes
from app.workers.tablero import (
    dias_a_recalcular,
    recalcular_cartera,
    recalcular_dia,
    recalcular_mes,
    recalcular_todo,
)

# Sin `pytestmark = pytest.mark.asyncio`: `asyncio_mode = "auto"` ya marca las
# pruebas async, y la marca de módulo haría que las pruebas síncronas de abajo
# (la aritmética del avance y la frescura, que no tocan la base) avisaran que
# están marcadas sin ser corrutinas.


# ---------------------------------------------------------------------------
# Semilla: un día de operación con todo lo que el tablero resume
# ---------------------------------------------------------------------------
@pytest.fixture
async def jornada(sesion, semilla) -> dict:
    """Un día real: 4 clientes, 3 ventas (una partida en dos), 2 no-drops, cobros.

    Las cifras están elegidas para que cada error dé un número distinto:

      · visitas contando CLIENTES  → 4 (Mary, Puente, Esquina, Pinos)
      · visitas contando DOCUMENTOS → 5
      · ventas = $500 + $500 + $1,200 = $2,200
      · drop size sobre visitas con venta (2) → $1,100
      · drop size sobre visitas (4), que sería el error → $550
    """
    dispositivo = uuid.uuid4()
    hoy = date.today()
    ahora = datetime.now(UTC)

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          ultima_sync_push_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now(), now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )

    clientes: dict[str, uuid.UUID] = {}
    for i, nombre in enumerate(["Mary", "Puente", "Esquina", "Pinos"], start=1):
        cid = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :n, :r, now(), now())"
            ),
            {"c": cid, "cod": f"C{i:05d}", "n": nombre, "r": semilla["ruta"]},
        )
        clientes[nombre] = cid

    folio = [0]

    async def venta(a_quien, total, *, tipo="contado", dia=None, con_ruta=True, lat=None):
        folio[0] += 1
        vid = uuid.uuid4()
        await sesion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                    subtotal, total, lat, lng,
                                    fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, :f, :fl, :c, :u, :r, :a, :tipo,
                        :total, :total, :lat, :lng, :ahora, :dia)
                """
            ),
            {
                "v": vid,
                "d": dispositivo,
                "f": folio[0],
                "fl": f"VEND01-{folio[0]:06d}",
                "c": a_quien,
                "u": semilla["vendedor"],
                "r": semilla["ruta"] if con_ruta else None,
                "a": semilla["camion"],
                "tipo": tipo,
                "total": total,
                "lat": lat,
                "lng": -103.3440 if lat is not None else None,
                "ahora": ahora,
                "dia": dia or hoy,
            },
        )
        return vid

    async def no_drop(a_quien, motivo, dia=None):
        folio[0] += 1
        await sesion.execute(
            text(
                """
                INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                      vendedor_id, ruta_id, motivo_codigo, lat, lng,
                                      fecha_dispositivo, fecha_operativa)
                VALUES (:n, :d, :f, :c, :u, :r, :m, 20.6736, -103.3440, :ahora, :dia)
                """
            ),
            {
                "n": uuid.uuid4(),
                "d": dispositivo,
                "f": folio[0],
                "c": a_quien,
                "u": semilla["vendedor"],
                "r": semilla["ruta"],
                "m": motivo,
                "ahora": ahora,
                "dia": dia or hoy,
            },
        )

    async def cobro(a_quien, importe, forma="efectivo", dia=None):
        folio[0] += 1
        await sesion.execute(
            text(
                """
                INSERT INTO cobros (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, importe, forma_pago,
                                    fecha_dispositivo, fecha_operativa)
                VALUES (:id, :d, :f, :fl, :c, :u, :imp, :forma, :ahora, :dia)
                """
            ),
            {
                "id": uuid.uuid4(),
                "d": dispositivo,
                "f": folio[0],
                "fl": f"VEND01-A{folio[0]:06d}",
                "c": a_quien,
                "u": semilla["vendedor"],
                "imp": importe,
                "forma": forma,
                "ahora": ahora,
                "dia": dia or hoy,
            },
        )

    # Mary compró y el pedido se partió en DOS remisiones: una visita.
    await venta(clientes["Mary"], Decimal("500.00"), lat=20.6740)
    await venta(clientes["Mary"], Decimal("500.00"))
    await venta(clientes["Puente"], Decimal("1200.00"), tipo="credito")
    await no_drop(clientes["Esquina"], "CERRADO")            # del cliente
    await no_drop(clientes["Pinos"], "AGOTADO_EN_CAMION")    # nuestro
    await cobro(clientes["Mary"], Decimal("300.00"))
    await cobro(clientes["Puente"], Decimal("450.00"), forma="transferencia")
    await sesion.commit()

    return {
        "dispositivo": dispositivo,
        "clientes": clientes,
        "dia": hoy,
        "venta": venta,
        "no_drop": no_drop,
        "cobro": cobro,
    }


# ---------------------------------------------------------------------------
# El modelo de lectura del día
# ---------------------------------------------------------------------------
async def test_el_dia_se_resume_por_vendedor(sesion, semilla, jornada):
    await recalcular_dia(sesion, jornada["dia"])
    fila = (
        await sesion.execute(
            text("SELECT * FROM tablero_dia WHERE fecha = :f AND vendedor_id = :v"),
            {"f": jornada["dia"], "v": semilla["vendedor"]},
        )
    ).mappings().one()

    assert Decimal(fila["venta_total"]) == Decimal("2200.00")
    assert Decimal(fila["venta_contado"]) == Decimal("1000.00")
    assert Decimal(fila["venta_credito"]) == Decimal("1200.00")
    assert fila["documentos_venta"] == 3
    assert Decimal(fila["cobrado_total"]) == Decimal("750.00")
    # El efectivo se separa porque es el único que entra al arqueo.
    assert Decimal(fila["cobrado_efectivo"]) == Decimal("300.00")


async def test_una_visita_es_un_cliente_no_un_documento(sesion, semilla, jornada):
    """Dos remisiones a Mary son UNA visita. Con documentos saldrían 5 y 3."""
    await recalcular_dia(sesion, jornada["dia"])
    fila = (
        await sesion.execute(
            text("SELECT visitas, visitas_con_venta FROM tablero_dia "
                 " WHERE fecha = :f AND vendedor_id = :v"),
            {"f": jornada["dia"], "v": semilla["vendedor"]},
        )
    ).mappings().one()
    assert fila["visitas"] == 4
    assert fila["visitas_con_venta"] == 2


async def test_una_segunda_vuelta_que_termino_en_venta_cuenta_como_vendida(
    sesion, semilla, jornada
):
    """Se pasó dos veces por Esquina: la primera cerrado, la segunda compró.

    Es el caso que un `count` de no-drops contaría como visita perdida y como
    visita vendida a la vez, inflando el total a 5 visitas sobre 4 clientes.
    """
    await jornada["venta"](jornada["clientes"]["Esquina"], Decimal("200.00"))
    await sesion.commit()
    await recalcular_dia(sesion, jornada["dia"])

    fila = (
        await sesion.execute(
            text("SELECT visitas, visitas_con_venta, no_drops FROM tablero_dia "
                 " WHERE fecha = :f AND vendedor_id = :v"),
            {"f": jornada["dia"], "v": semilla["vendedor"]},
        )
    ).mappings().one()
    assert fila["visitas"] == 4
    assert fila["visitas_con_venta"] == 3
    # El no-drop sigue existiendo como documento: la visita perdida ocurrió.
    assert fila["no_drops"] == 2


async def test_los_no_drops_nuestros_se_separan_de_los_del_cliente(
    sesion, semilla, jornada
):
    await recalcular_dia(sesion, jornada["dia"])
    fila = (
        await sesion.execute(
            text("SELECT no_drops, no_drops_nuestros FROM tablero_dia "
                 " WHERE fecha = :f AND vendedor_id = :v"),
            {"f": jornada["dia"], "v": semilla["vendedor"]},
        )
    ).mappings().one()
    assert fila["no_drops"] == 2
    # 'CERRADO' es del cliente; 'AGOTADO_EN_CAMION' es de la carga: nuestro.
    assert fila["no_drops_nuestros"] == 1


async def test_el_vendedor_que_solo_cobro_aparece(sesion, semilla, jornada):
    """Un JOIN desde ventas lo desaparecería: es el que hay que ver."""
    otro = uuid.uuid4()
    dispositivo = uuid.uuid4()
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'VEND02', 'Luis', :h, 'vendedor', now(), now())"
        ),
        {"u": otro, "s": semilla["sucursal"], "h": hashear_password("Vendedor2026")},
    )
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'Equipo de Luis', 'activo', now())"
        ),
        {"d": dispositivo, "u": otro},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cobros (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, importe, forma_pago,
                                fecha_dispositivo, fecha_operativa)
            VALUES (:id, :d, 1, 'VEND02-A000001', :c, :u, 900, 'efectivo', now(), :dia)
            """
        ),
        {
            "id": uuid.uuid4(),
            "d": dispositivo,
            "c": jornada["clientes"]["Pinos"],
            "u": otro,
            "dia": jornada["dia"],
        },
    )
    await sesion.commit()
    await recalcular_dia(sesion, jornada["dia"])

    fila = (
        await sesion.execute(
            text("SELECT venta_total, cobrado_total, visitas FROM tablero_dia "
                 " WHERE fecha = :f AND vendedor_id = :v"),
            {"f": jornada["dia"], "v": otro},
        )
    ).mappings().one()
    assert Decimal(fila["venta_total"]) == Decimal("0.00")
    assert Decimal(fila["cobrado_total"]) == Decimal("900.00")
    # Un cobro NO es una visita: la visita es una venta o un no-drop.
    assert fila["visitas"] == 0


async def test_un_dia_sin_operacion_deja_renglon_en_cero_para_hoy(sesion, semilla):
    """"Nadie ha vendido" y "el tablero no se ha calculado" son distintos.

    Sin el sello, el tablero de las 7 de la mañana no podría distinguirlos, y la
    segunda es una falla del worker que hay que ver.
    """
    hoy = date.today()
    await recalcular_dia(sesion, hoy)
    fila = (
        await sesion.execute(
            text("SELECT * FROM tablero_dia WHERE fecha = :f AND vendedor_id = :v"),
            {"f": hoy, "v": semilla["vendedor"]},
        )
    ).mappings().one()
    assert Decimal(fila["venta_total"]) == Decimal("0.00")
    assert fila["visitas"] == 0
    assert fila["calculado_en"] is not None


async def test_un_dia_pasado_no_se_sella_con_vendedores_sin_actividad(sesion, semilla):
    """Sellar un día viejo inventaría ceros de vendedores que entraron después."""
    anteayer = date.today() - timedelta(days=2)
    await recalcular_dia(sesion, anteayer)
    cuantos = (
        await sesion.execute(
            text("SELECT count(*) FROM tablero_dia WHERE fecha = :f"), {"f": anteayer}
        )
    ).scalar_one()
    assert cuantos == 0


async def test_cancelar_la_unica_venta_borra_el_renglon(sesion, semilla, jornada):
    """Con `ON CONFLICT DO UPDATE` quedaría el renglón con la cifra anterior."""
    await recalcular_dia(sesion, jornada["dia"])
    antes = (
        await sesion.execute(
            text("SELECT venta_total FROM tablero_dia WHERE fecha = :f AND vendedor_id = :v"),
            {"f": jornada["dia"], "v": semilla["vendedor"]},
        )
    ).scalar_one()
    assert Decimal(antes) == Decimal("2200.00")

    await sesion.execute(
        text("UPDATE ventas SET estado = 'cancelada' WHERE fecha_operativa = :f"),
        {"f": jornada["dia"]},
    )
    await sesion.commit()
    await recalcular_dia(sesion, jornada["dia"])

    fila = (
        await sesion.execute(
            text("SELECT venta_total, visitas FROM tablero_dia "
                 " WHERE fecha = :f AND vendedor_id = :v"),
            {"f": jornada["dia"], "v": semilla["vendedor"]},
        )
    ).mappings().one()
    # Sigue existiendo porque hubo no-drops y cobros, pero sin venta.
    assert Decimal(fila["venta_total"]) == Decimal("0.00")
    assert fila["visitas"] == 2   # los dos no-drops


# ---------------------------------------------------------------------------
# Detección de días rancios
# ---------------------------------------------------------------------------
async def test_una_venta_que_llega_tarde_deja_rancio_SU_dia(sesion, semilla, jornada):
    """El caso que un ETL incremental por `creado_en` arruinaría.

    Una venta del lunes que sincroniza hoy tiene que dejar rancio el LUNES. Si
    marcara hoy, el lunes quedaría subreportado para siempre y nada lo avisaría.
    """
    lunes = date.today() - timedelta(days=3)
    await recalcular_dia(sesion, lunes)      # el lunes se calcula vacío
    await sesion.execute(
        text("INSERT INTO tablero_dia (fecha, vendedor_id, calculado_en) "
             "VALUES (:f, :v, now()) ON CONFLICT DO NOTHING"),
        {"f": lunes, "v": semilla["vendedor"]},
    )
    await sesion.commit()

    # Ahora llega la venta del lunes: `fecha_servidor` es ahora, `fecha_operativa`
    # es el lunes.
    await jornada["venta"](jornada["clientes"]["Mary"], Decimal("777.00"), dia=lunes)
    await sesion.commit()

    sucios = await dias_a_recalcular(sesion)
    assert lunes in sucios, f"el lunes tenía que salir rancio; salieron {sucios}"


async def test_hoy_entra_siempre_aunque_nada_cambie(sesion, semilla):
    """La antigüedad que muestra la tarjeta es `calculado_en`.

    Si hoy no se recalculara por no haber cambios, el tablero diría "hace 3 h"
    con cifras correctas, y quien lo lea va a creer que el sistema está caído.
    """
    sucios = await dias_a_recalcular(sesion)
    assert date.today() in sucios


async def test_un_dia_ya_calculado_y_sin_cambios_no_vuelve_a_salir(
    sesion, semilla, jornada
):
    ayer = date.today() - timedelta(days=1)
    await jornada["venta"](jornada["clientes"]["Mary"], Decimal("100.00"), dia=ayer)
    await sesion.commit()

    assert ayer in await dias_a_recalcular(sesion)
    await recalcular_dia(sesion, ayer)
    await sesion.commit()
    assert ayer not in await dias_a_recalcular(sesion)


async def test_cancelar_una_venta_deja_rancio_el_dia(sesion, semilla, jornada):
    """Cancelar NO toca `ventas.fecha_servidor`.

    Sin la rama de `ventas_cancelaciones` en la detección, el total del día
    nunca bajaría: el tablero seguiría contando una venta que ya no existe.
    """
    ayer = date.today() - timedelta(days=1)
    venta_id = await jornada["venta"](
        jornada["clientes"]["Mary"], Decimal("640.00"), dia=ayer
    )
    await sesion.commit()
    await recalcular_dia(sesion, ayer)
    await sesion.commit()
    assert ayer not in await dias_a_recalcular(sesion)

    await sesion.execute(
        text(
            "INSERT INTO ventas_cancelaciones (id, venta_id, motivo, usuario_id) "
            "VALUES (:id, :v, 'el cliente devolvió todo', :u)"
        ),
        {"id": uuid.uuid4(), "v": venta_id, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text("UPDATE ventas SET estado = 'cancelada' WHERE id = :v"), {"v": venta_id}
    )
    await sesion.commit()

    assert ayer in await dias_a_recalcular(sesion)


# ---------------------------------------------------------------------------
# El mes por ruta y la cartera
# ---------------------------------------------------------------------------
async def test_el_mes_por_ruta_suma_y_cuenta_dias(sesion, semilla, jornada):
    hoy = date.today()
    periodo = inicio_de_mes(hoy)
    # Otra venta el mismo mes, otro día (si cae en el mes anterior, no cuenta).
    otro_dia = hoy - timedelta(days=1)
    if inicio_de_mes(otro_dia) == periodo:
        await jornada["venta"](jornada["clientes"]["Pinos"], Decimal("800.00"), dia=otro_dia)
        await sesion.commit()
        esperado_venta = Decimal("3000.00")
        esperado_dias = 2
    else:
        esperado_venta = Decimal("2200.00")
        esperado_dias = 1

    await recalcular_mes(sesion, periodo)
    fila = (
        await sesion.execute(
            text("SELECT * FROM tablero_mes_ruta WHERE periodo = :p AND ruta_id = :r"),
            {"p": periodo, "r": semilla["ruta"]},
        )
    ).mappings().one()
    assert Decimal(fila["venta_mes"]) == esperado_venta
    assert fila["dias_con_venta"] == esperado_dias


async def test_el_mes_por_ruta_deja_fuera_lo_que_no_trae_ruta(sesion, semilla, jornada):
    """Y por eso el tablero lo cuenta aparte en vez de callarlo.

    `ventas.ruta_id` es nullable. Si el grano por ruta fuera el único, el total
    del mes no cuadraría con la suma de las barras sin explicación.
    """
    periodo = inicio_de_mes(date.today())
    await jornada["venta"](
        jornada["clientes"]["Mary"], Decimal("999.00"), con_ruta=False
    )
    await sesion.commit()
    await recalcular_mes(sesion, periodo)

    de_la_ruta = (
        await sesion.execute(
            text("SELECT venta_mes FROM tablero_mes_ruta WHERE periodo = :p AND ruta_id = :r"),
            {"p": periodo, "r": semilla["ruta"]},
        )
    ).scalar_one()
    assert Decimal(de_la_ruta) == Decimal("2200.00")   # sin los $999

    # El grano por VENDEDOR sí los incluye: es el completo, y de ahí sale el total.
    await recalcular_dia(sesion, date.today())
    total = (
        await sesion.execute(
            text("SELECT sum(venta_total) FROM tablero_dia WHERE fecha = :f"),
            {"f": date.today()},
        )
    ).scalar_one()
    assert Decimal(total) == Decimal("3199.00")


async def test_la_cartera_se_reparte_en_tramos_de_antiguedad(sesion, semilla, jornada):
    hoy = date.today()
    venta_id = await jornada["venta"](
        jornada["clientes"]["Mary"], Decimal("1000.00"), tipo="credito"
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                            importe_pagado, fecha_emision,
                                            fecha_vencimiento, estado)
            VALUES (:v, :c, 1000, 0, :emision, :vence, 'abierta')
            """
        ),
        {
            "v": venta_id,
            "c": jornada["clientes"]["Mary"],
            "emision": hoy - timedelta(days=30),
            "vence": hoy - timedelta(days=20),
        },
    )
    await sesion.commit()
    await recalcular_cartera(sesion)

    fila = (
        await sesion.execute(text("SELECT * FROM tablero_cartera WHERE id"))
    ).mappings().one()
    assert Decimal(fila["saldo_total"]) == Decimal("1000.00")
    assert Decimal(fila["saldo_vencido"]) == Decimal("1000.00")
    assert Decimal(fila["vencido_16_30"]) == Decimal("1000.00")
    assert Decimal(fila["vencido_1_15"]) == Decimal("0.00")
    assert fila["clientes_vencidos"] == 1


# ---------------------------------------------------------------------------
# La corrida completa
# ---------------------------------------------------------------------------
async def test_la_corrida_completa_deja_el_refresco_con_el_estado_del_mundo(
    sesion, semilla, jornada
):
    # Un segundo equipo que NO ha sincronizado hoy, y que reportó cola pendiente.
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          ultima_sync_push_en, cola_pendiente, cola_reportada_en) "
            "VALUES (:d, :u, 'Equipo rezagado', 'activo', now(), NULL, 3, now())"
        ),
        {"d": uuid.uuid4(), "u": semilla["admin"]},
    )
    await sesion.commit()

    resultado = await recalcular_todo(sesion)
    assert resultado["equipos_sin_sincronizar"] == 1
    assert resultado["cola_reportada"] == 3

    fila = (
        await sesion.execute(text("SELECT * FROM tablero_refrescos WHERE id"))
    ).mappings().one()
    assert fila["equipos_sin_sincronizar"] == 1
    assert fila["dias_recalculados"] >= 1
    assert fila["duracion_ms"] is not None


async def test_la_corrida_completa_es_idempotente(sesion, semilla, jornada):
    """Correrla dos veces no duplica nada: el día se borra y se vuelve a insertar."""
    await recalcular_todo(sesion)
    primera = (
        await sesion.execute(
            text("SELECT sum(venta_total) FROM tablero_dia WHERE fecha = :f"),
            {"f": jornada["dia"]},
        )
    ).scalar_one()
    await recalcular_todo(sesion)
    segunda = (
        await sesion.execute(
            text("SELECT sum(venta_total) FROM tablero_dia WHERE fecha = :f"),
            {"f": jornada["dia"]},
        )
    ).scalar_one()
    assert Decimal(primera) == Decimal(segunda) == Decimal("2200.00")


# ---------------------------------------------------------------------------
# Aritmética del avance
# ---------------------------------------------------------------------------
def test_el_avance_se_compara_contra_lo_esperado_a_prorrata():
    """67% el día 10 va adelante; el mismo 67% el día 28 va atrás."""
    dia_10 = Avance(
        venta=Decimal("67000"), objetivo=Decimal("100000"), dia_del_mes=10, dias_del_mes=30
    )
    dia_28 = Avance(
        venta=Decimal("67000"), objetivo=Decimal("100000"), dia_del_mes=28, dias_del_mes=30
    )
    assert dia_10.logrado == dia_28.logrado == Decimal("67.0")
    assert dia_10.semaforo == "adelante"
    assert dia_28.semaforo == "atras"


def test_una_ruta_sin_objetivo_no_inventa_un_avance():
    avance = Avance(venta=Decimal("5000"), objetivo=None, dia_del_mes=15, dias_del_mes=30)
    assert avance.logrado is None
    assert avance.diferencia is None
    assert avance.semaforo == "sin_objetivo"


def test_un_objetivo_en_cero_no_da_avance_infinito():
    """Es la razón de que un objetivo vacío BORRE el renglón en vez de guardar 0."""
    avance = Avance(venta=Decimal("5000"), objetivo=Decimal("0"), dia_del_mes=15,
                    dias_del_mes=30)
    assert avance.logrado is None


def test_el_tramo_de_cerca_evita_pintar_rojo_por_dos_puntos():
    """Un tablero que pinta rojo a los dos puntos enseña a ignorar el rojo."""
    assert Avance(
        venta=Decimal("48000"), objetivo=Decimal("100000"), dia_del_mes=15, dias_del_mes=30
    ).semaforo == "cerca"
    assert Avance(
        venta=Decimal("30000"), objetivo=Decimal("100000"), dia_del_mes=15, dias_del_mes=30
    ).semaforo == "atras"


# ---------------------------------------------------------------------------
# Frescura
# ---------------------------------------------------------------------------
def test_la_frescura_no_es_confiable_con_un_equipo_rezagado():
    """Dos minutos de antigüedad y un teléfono sin subir: las cifras son un piso."""
    fresca = Frescura(
        minutos=2, equipos_sin_sincronizar=0, cola_reportada=0, ops_en_cuarentena=0
    )
    assert fresca.confiable
    assert fresca.advertencia is None

    con_rezago = Frescura(
        minutos=2, equipos_sin_sincronizar=2, cola_reportada=0, ops_en_cuarentena=0
    )
    assert not con_rezago.confiable
    assert "piso" in con_rezago.advertencia


def test_la_advertencia_enumera_todos_los_motivos():
    """Callar uno deja a quien lee creyendo que ya sabe todo lo que falta."""
    fresca = Frescura(
        minutos=200, equipos_sin_sincronizar=1, cola_reportada=4, ops_en_cuarentena=2
    )
    aviso = fresca.advertencia
    assert "3 h" in aviso
    assert "1 equipo" in aviso
    assert "4 operación" in aviso
    assert "cuarentena" in aviso


# ---------------------------------------------------------------------------
# El enganche con la sincronización
# ---------------------------------------------------------------------------
async def test_un_lote_aceptado_encola_el_recalculo(sesion, semilla):
    """Y uno rechazado no: no hay nada nuevo que recalcular."""
    from app.infra.sync.ingesta import procesar_lote
    from app.infra.sync.manejadores import Contexto
    from tests.ayudas_sync import operacion_cliente, sobre

    dispositivo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    ctx = Contexto(
        dispositivo_id=dispositivo,
        usuario_id=semilla["vendedor"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )
    resultado = await procesar_lote(
        sesion, ctx, uuid.uuid4(), [sobre(operacion_cliente("Tienda nueva"))]
    )
    assert resultado.aceptadas == 1

    encolados = (
        await sesion.execute(
            text("SELECT count(*) FROM jobs WHERE tipo = 'recalcular_tablero'")
        )
    ).scalar_one()
    assert encolados == 1


async def test_ocho_camiones_a_la_vez_encolan_un_solo_recalculo(sesion, semilla):
    """`clave_unica` fija: el job averigua solo qué días recalcular.

    Con una clave por fecha, ocho equipos subiendo operación de tres días
    distintos encolarían hasta veinticuatro recálculos del mismo trabajo.
    """
    from app.infra.sync.ingesta import procesar_lote
    from app.infra.sync.manejadores import Contexto
    from tests.ayudas_sync import operacion_cliente, sobre

    for i in range(3):
        dispositivo = uuid.uuid4()
        usuario = uuid.uuid4()
        from app.core.seguridad import hashear_password

        await sesion.execute(
            text(
                "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
                "                      rol_codigo, almacen_id, creado_en, actualizado_en) "
                "VALUES (:u, :s, :cod, :nom, :h, 'vendedor', :a, now(), now())"
            ),
            {
                "u": usuario, "s": semilla["sucursal"], "cod": f"VND{i}",
                "nom": f"Vendedor {i}", "h": hashear_password("Vendedor2026"),
                "a": semilla["camion"],
            },
        )
        await sesion.execute(
            text(
                "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
                "VALUES (:d, :u, :e, 'activo', now())"
            ),
            {"d": dispositivo, "u": usuario, "e": f"Equipo {i}"},
        )
        await sesion.commit()
        ctx = Contexto(
            dispositivo_id=dispositivo,
            usuario_id=usuario,
            rutas=(semilla["ruta"],),
            almacen_id=semilla["camion"],
        )
        await procesar_lote(
            sesion, ctx, uuid.uuid4(), [sobre(operacion_cliente(f"Tienda {i}"))]
        )

    encolados = (
        await sesion.execute(
            text("SELECT count(*) FROM jobs WHERE tipo = 'recalcular_tablero'")
        )
    ).scalar_one()
    assert encolados == 1
