"""El tablero por periodo, los movimientos de cada vendedor y la bitácora de sincronización.

Lo que se defiende:

1. «Esta semana» empieza en lunes y significa lo mismo en todas las pantallas; un
   periodo mal escrito cae en hoy y uno al revés se endereza.
2. El tablero cuenta del periodo elegido las cifras de dinero, y deja de «ahora»
   lo que se atiende ahora.
3. La línea de tiempo del vendedor junta lo que estaba en ocho pantallas, cada cosa
   con su enlace, y el filtro por tipo filtra.
4. Cada bajada que entregó algo queda en la bitácora con su desglose; un teléfono
   con todo subido y todo bajado sale «al día», y uno al que le falta algo dice qué.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.api.admin.periodo import leer_periodo
from tests.conftest import solo_texto
from tests.test_alcance_de_ruta import _cabecera, _hasta_el_final
from tests.test_panel_liquidacion import sembrar_dia_de_trabajo
from tests.test_plan_visita import _entrar

# ---------------------------------------------------------------------------
# El periodo
# ---------------------------------------------------------------------------

MIERCOLES = date(2026, 10, 7)


@pytest.mark.parametrize(
    ("clave", "inicio", "fin"),
    [
        ("hoy", MIERCOLES, MIERCOLES),
        ("ayer", date(2026, 10, 6), date(2026, 10, 6)),
        ("semana", date(2026, 10, 5), MIERCOLES),  # lunes
        ("semana_pasada", date(2026, 9, 28), date(2026, 10, 4)),
        ("mes", date(2026, 10, 1), MIERCOLES),
        ("mes_pasado", date(2026, 9, 1), date(2026, 9, 30)),
    ],
)
def test_cada_periodo_dice_lo_mismo_en_todas_partes(clave, inicio, fin):
    p = leer_periodo(clave, hoy=MIERCOLES)
    assert (p.inicio, p.fin) == (inicio, fin)


def test_lo_que_no_se_entiende_cae_en_hoy_y_un_rango_al_reves_se_endereza():
    assert leer_periodo("cualquier cosa", hoy=MIERCOLES).clave == "hoy"
    p = leer_periodo("rango", "2026-10-07", "2026-10-01", hoy=MIERCOLES)
    assert (p.inicio, p.fin) == (date(2026, 10, 1), MIERCOLES)
    # Fechas escritas sin elegir «Personalizado»: es lo que la persona quiso.
    assert leer_periodo("", "2026-10-01", "2026-10-03", hoy=MIERCOLES).clave == "rango"


def test_todo_va_del_primer_dia_con_datos_hasta_hoy_sin_tope():
    """«Que venga uno que abarque todos los periodos, uno general» (ADR 0002 §96)."""
    p = leer_periodo("todo", hoy=MIERCOLES, primer_dia=date(2024, 3, 1))
    assert (p.clave, p.etiqueta, p.inicio, p.fin) == ("todo", "Todo", date(2024, 3, 1), MIERCOLES)
    assert p.dias > 366  # sin el tope del rango a mano
    assert p.como_parametros() == "periodo=todo"
    assert p.descripcion.startswith("desde el principio")
    # Con la base en blanco no hay primer día: es hoy.
    vacio = leer_periodo("todo", hoy=MIERCOLES)
    assert (vacio.inicio, vacio.fin) == (MIERCOLES, MIERCOLES)


def test_un_rango_de_varios_anios_se_recorta_a_uno():
    p = leer_periodo("rango", "2020-01-01", "2026-10-07", hoy=MIERCOLES)
    assert p.dias == 366
    assert p.fin == MIERCOLES


# ---------------------------------------------------------------------------
# El tablero por periodo
# ---------------------------------------------------------------------------


async def _venta_de_ayer(sesion, semilla, dia: dict) -> None:
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (gen_random_uuid(), :d, 2, 'VEND01-000002', :c, :u, :a, 'credito',
                    999.00, 999.00, now() - interval '1 day', CURRENT_DATE - 1)
            """
        ),
        {"d": dia["dispositivo"], "c": dia["cliente"], "u": semilla["vendedor"],
         "a": semilla["camion"]},
    )
    await sesion.commit()


@pytest.mark.asyncio
async def test_el_tablero_cuenta_el_periodo_elegido(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)  # una venta de $2,250 hoy
    await _venta_de_ayer(sesion, semilla, dia)          # y una de $999 ayer
    await _entrar(cliente)

    hoy = solo_texto(await cliente.get("/panel"))
    assert "ventas recibidas hoy" in hoy
    assert "$2,250.00 en total" in hoy

    ayer = solo_texto(await cliente.get("/panel?periodo=ayer"))
    assert "ventas recibidas · ayer" in ayer
    assert "$999.00 en total" in ayer
    assert "efectivo cobrado · ayer" in ayer

    semana = await cliente.get("/panel?periodo=rango&desde="
                               f"{date.today() - timedelta(days=1)}&hasta={date.today()}")
    texto = solo_texto(semana)
    assert "$3,249.00 en total" in texto
    # El desglose por vendedor lleva a sus movimientos con el mismo periodo.
    assert 'id="tabla_por_vendedor"' in semana.text
    assert f'/panel/vendedores/{semilla["vendedor"]}?periodo=rango' in semana.text
    # Y por día, con el día flojo y el bueno.
    assert 'id="tabla_por_dia"' in semana.text


async def test_todo_suma_desde_la_primera_venta(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)  # $2,250 hoy
    await _venta_de_ayer(sesion, semilla, dia)          # y $999 ayer
    await _entrar(cliente)
    todo = await cliente.get("/panel?periodo=todo")
    texto = solo_texto(todo)
    assert "$3,249.00 en total" in texto
    assert "desde el principio" in texto.lower()
    assert '<option value="todo" selected>Todo</option>' in todo.text
    # La app pide lo mismo.
    from tests.test_oficina_clientes_api import _cab

    cab = await _cab(cliente)
    datos = (await cliente.get("/v1/tablero/periodo?periodo=todo", headers=cab)).json()
    assert datos["periodo"]["clave"] == "todo"
    assert ["todo", "Todo"] in datos["periodos"]


@pytest.mark.asyncio
async def test_lo_que_se_atiende_ahora_no_cambia_con_el_periodo(cliente, sesion, semilla):
    await _entrar(cliente)
    for periodo in ("hoy", "mes_pasado"):
        html = (await cliente.get(f"/panel?periodo={periodo}")).text
        # Un solo bloque de pendientes, el mismo con cualquier periodo (§98).
        assert '<h2 id="pendientes">Pendientes</h2>' in html


# ---------------------------------------------------------------------------
# Los movimientos de un vendedor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_linea_de_tiempo_junta_lo_que_estaba_en_ocho_pantallas(
    cliente, sesion, semilla
):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(
        text(
            """
            INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                  vendedor_id, motivo_codigo, lat, lng,
                                  fecha_dispositivo, fecha_operativa)
            VALUES (gen_random_uuid(), :d, 1, :c, :u, 'DUENO_AUSENTE', 19.4, -99.1,
                    now(), CURRENT_DATE)
            """
        ),
        {"d": dia["dispositivo"], "c": dia["cliente"], "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO cuenta_vendedor (vendedor_id, tipo, origen, importe, fecha, "
            "                             concepto, registrado_por) "
            "VALUES (:u, 'cargo', 'cargo_manual', 150, CURRENT_DATE, 'Faltante de envases', :a)"
        ),
        {"u": semilla["vendedor"], "a": semilla["admin"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO ajustes_camion (folio, almacen_id, producto_id, tipo, "
            "    existencia_al_capturar, contado, delta, nota, usuario_id) "
            "VALUES ('AC-000001', :a, :p, 'conteo', 60, 58, -2, 'Conteo de la mañana', :u)"
        ),
        {"a": semilla["camion"], "p": dia["producto"], "u": semilla["admin"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await cliente.get(f"/panel/vendedores/{semilla['vendedor']}")
    assert r.status_code == 200
    html = r.text
    texto = solo_texto(r)

    assert f'/panel/ventas/{dia["venta"]}' in html
    assert f'/panel/cargas/{dia["carga"]}' in html
    assert "Visita sin venta" in texto
    assert "Cargo · Faltante de envases" in texto
    assert "Atún en agua 140 g -2" in texto
    # La lista es también un acceso a su cuenta y a sus sincronizaciones.
    assert f'/panel/vendedores/cuenta/{semilla["vendedor"]}' in html
    assert f'/panel/sincronizaciones?usuario={semilla["vendedor"]}' in html

    solo_ventas = await cliente.get(f"/panel/vendedores/{semilla['vendedor']}?tipo=venta")
    tabla = solo_ventas.text[solo_ventas.text.index('id="tabla_movimientos"'):]
    assert "Visita sin venta" not in tabla
    assert "VEND01-000001" in tabla


@pytest.mark.asyncio
async def test_la_lista_de_vendedores_resume_el_periodo(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    await _entrar(cliente)
    r = await cliente.get("/panel/vendedores")
    assert r.status_code == 200
    assert "$2,250.00" in solo_texto(r)
    assert f'/panel/vendedores/{semilla["vendedor"]}?periodo=hoy' in r.text


@pytest.mark.asyncio
async def test_un_vendedor_que_no_existe_lo_dice(cliente, semilla):
    await _entrar(cliente)
    r = await cliente.get(f"/panel/vendedores/{uuid.uuid4()}")
    assert r.status_code == 200
    assert "Ese vendedor no existe" in r.text


# ---------------------------------------------------------------------------
# Las sincronizaciones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cada_bajada_queda_con_lo_que_entrego(cliente, sesion, semilla):
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, nombre_comercial, ruta_id, creado_en, actualizado_en) "
            "VALUES (gen_random_uuid(), 'Abarrotes Lupita', :r, now(), now())"
        ),
        {"r": semilla["ruta"]},
    )
    await sesion.commit()
    await _hasta_el_final(cliente, cab)

    fila = (
        await sesion.execute(
            text("SELECT cambios, entidades FROM sync_bajadas ORDER BY id DESC LIMIT 1")
        )
    ).mappings().one()
    assert fila["entidades"].get("cliente") == 1
    assert fila["cambios"] >= 1

    await _entrar(cliente)
    r = await cliente.get("/panel/sincronizaciones")
    assert "↓ Bajada" in r.text
    assert "1 clientes" in solo_texto(r)


@pytest.mark.asyncio
async def test_un_pull_vacio_no_deja_renglon_pero_si_dice_que_el_telefono_vive(
    cliente, sesion, semilla
):
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    cursor, _ = await _hasta_el_final(cliente, cab)
    antes = (await sesion.execute(text("SELECT count(*) FROM sync_bajadas"))).scalar_one()
    await sesion.execute(text("UPDATE dispositivos SET ultima_sync_pull_en = NULL"))
    await sesion.commit()

    await cliente.get(f"/v1/sync/pull?cursor={cursor}", headers=cab)

    assert (await sesion.execute(text("SELECT count(*) FROM sync_bajadas"))).scalar_one() == antes
    vivo = (
        await sesion.execute(text("SELECT ultima_sync_pull_en FROM dispositivos"))
    ).scalar_one()
    assert vivo is not None


@pytest.mark.asyncio
async def test_el_telefono_al_dia_y_el_que_debe_algo(cliente, sesion, semilla):
    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    await _hasta_el_final(cliente, cab)
    await sesion.execute(
        text("UPDATE dispositivos SET cola_pendiente = 0, ultima_sync_push_en = now()")
    )
    await sesion.commit()

    await _entrar(cliente)
    html = (await cliente.get("/panel/sincronizaciones")).text
    inicio = html.index('id="estado_telefonos"')
    estado = html[inicio : html.index("</table>", inicio)]
    assert "Al día" in estado

    # La oficina cambia algo que le toca: hasta que lo baje, le falta.
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, nombre_comercial, ruta_id, creado_en, actualizado_en) "
            "VALUES (gen_random_uuid(), 'Fonda Doña Mary', :r, now(), now())"
        ),
        {"r": semilla["ruta"]},
    )
    await sesion.commit()
    html = (await cliente.get("/panel/sincronizaciones")).text
    assert "1 cambio(s) por bajar" in html


@pytest.mark.asyncio
async def test_la_subida_muestra_cada_operacion_y_lo_rechazado(cliente, sesion, semilla):
    from tests.ayudas_sync import a_json, operacion_cliente, sobre

    cab = await _cabecera(cliente, sesion, semilla["vendedor"], "VEND01")
    buena = sobre(operacion_cliente("Abarrotes Lupita"))
    mala_op = operacion_cliente("Sin nombre")
    mala_op.datos.pop("nombre_comercial")
    mala = sobre(mala_op, secuencia=2)
    r = await cliente.post("/v1/sync/push", json=a_json([buena, mala]), headers=cab)
    assert r.json()["rechazadas"] == 1

    await _entrar(cliente)
    lista = await cliente.get("/panel/sincronizaciones")
    assert "↑ Subida" in lista.text
    assert "1 rechazada(s)" in solo_texto(lista)
    problemas = await cliente.get("/panel/sincronizaciones?problemas=1&direccion=subida")
    assert "↑ Subida" in problemas.text

    lote = (await sesion.execute(text("SELECT id FROM sync_lotes"))).scalar_one()
    detalle = await cliente.get(f"/panel/sincronizaciones/lote/{lote}")
    assert detalle.status_code == 200
    texto = solo_texto(detalle)
    assert "Alta de cliente" in texto
    assert "Aceptada" in texto and "Rechazada" in texto
    assert "/panel/cuarentena/" in detalle.text
    assert "/panel/clientes/" in detalle.text
