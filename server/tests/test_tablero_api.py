"""`/v1/tablero`: lo que el teléfono del gerente recibe, y quién puede pedirlo.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
1. **El alcance.** La UI oculta, el servidor prohíbe. Un vendedor con su token y
   `curl` no puede leer la venta de todas las rutas ni la cartera completa.

2. **El dinero viaja como string.** Si saliera como número JSON, Dart lo
   recibiría como `double` IEEE-754 (contracts/README.md §1.4). En una cartera
   que se arrastra meses eso son centavos perdidos — y aquí además son cifras
   que alguien compara con el arqueo de la liquidación.

3. **El tablero nunca calculado no se ve "recién calculado".** `minutos = 0` y
   `minutos = None` son dos cosas distintas: la segunda significa que el worker
   no ha corrido y hay que arreglarlo.

4. **Un día futuro no devuelve ceros.** Devolverlos diría "no se vendió nada" de
   un día que todavía no ocurrió.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.workers.tablero import recalcular_todo
from tests.conftest import PASSWORD_VENDEDOR

pytestmark = pytest.mark.asyncio


async def _cab(cliente, codigo: str = "ADMIN01") -> dict:
    r = await cliente.post(
        "/v1/auth/login", json={"codigo": codigo, "password": PASSWORD_VENDEDOR}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _gerente(cliente, sesion, semilla) -> dict:
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'GER01', 'Bryan Gerencia', :h, 'gerente', now(), now())"
        ),
        {"u": uuid.uuid4(), "s": semilla["sucursal"], "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()
    return await _cab(cliente, "GER01")


@pytest.fixture
async def dia_con_operacion(sesion, semilla) -> dict:
    """Una venta de contado, una a crédito, un no-drop y un cobro en efectivo."""
    dispositivo = uuid.uuid4()
    hoy = date.today()
    ahora = datetime.now(UTC)

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          ultima_sync_push_en) "
            "VALUES (:d, :u, 'POCO M5s', 'activo', now(), now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    clientes = {}
    for i, nombre in enumerate(["Mary", "Puente"], start=1):
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

    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                subtotal, total, lat, lng, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :r, :a, 'contado',
                    1234.56, 1234.56, 20.6736, -103.3440, :t, :dia)
            """
        ),
        {
            "v": uuid.uuid4(), "d": dispositivo, "c": clientes["Mary"],
            "u": semilla["vendedor"], "r": semilla["ruta"], "a": semilla["camion"],
            "t": ahora, "dia": hoy,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                  vendedor_id, ruta_id, motivo_codigo, lat, lng,
                                  fecha_dispositivo, fecha_operativa)
            VALUES (:n, :d, 2, :c, :u, :r, 'AGOTADO_EN_CAMION', 20.67, -103.34, :t, :dia)
            """
        ),
        {
            "n": uuid.uuid4(), "d": dispositivo, "c": clientes["Puente"],
            "u": semilla["vendedor"], "r": semilla["ruta"], "t": ahora, "dia": hoy,
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cobros (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, importe, forma_pago,
                                fecha_dispositivo, fecha_operativa)
            VALUES (:id, :d, 3, 'VEND01-A000003', :c, :u, 500.25, 'efectivo', :t, :dia)
            """
        ),
        {
            "id": uuid.uuid4(), "d": dispositivo, "c": clientes["Mary"],
            "u": semilla["vendedor"], "t": ahora, "dia": hoy,
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO objetivos_ruta (ruta_id, periodo, objetivo_venta) "
            "VALUES (:r, date_trunc('month', CURRENT_DATE)::date, 100000)"
        ),
        {"r": semilla["ruta"]},
    )
    await sesion.commit()
    await recalcular_todo(sesion)
    return {"clientes": clientes, "dia": hoy, "dispositivo": dispositivo}


# ---------------------------------------------------------------------------
# Alcance
# ---------------------------------------------------------------------------
async def test_un_vendedor_no_puede_ver_el_tablero(cliente, sesion, semilla):
    """La cartera completa y la venta de todas las rutas no son suyas.

    El rol 'vendedor' no tiene `tablero.ver` (migración 0021), y el guardia está
    en el servidor: esconder el botón en la app no cambia lo que `curl` puede
    leer con un token válido.
    """
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
        "dispositivo_id": str(dispositivo),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}

    respuesta = await cliente.get("/v1/tablero", headers=cab)
    assert respuesta.status_code == 403
    assert "tablero.ver" in respuesta.json()["detail"]


async def test_sin_token_no_hay_tablero(cliente, semilla):
    assert (await cliente.get("/v1/tablero")).status_code == 401


async def test_un_gerente_si_puede(cliente, sesion, semilla, dia_con_operacion):
    cab = await _gerente(cliente, sesion, semilla)
    r = await cliente.get("/v1/tablero", headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["venta"]["total"] == "1234.56"


# ---------------------------------------------------------------------------
# Las cifras
# ---------------------------------------------------------------------------
async def test_el_dinero_viaja_como_cadena_con_dos_decimales(
    cliente, sesion, semilla, dia_con_operacion
):
    """Un número JSON llegaría a Dart como double. Ver contracts/README.md §1.4."""
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    for valor in (
        cuerpo["venta"]["total"],
        cuerpo["venta"]["contado"],
        cuerpo["venta"]["credito"],
        cuerpo["venta"]["ticket_promedio"],
        cuerpo["visitas"]["drop_size"],
        cuerpo["cobranza"]["cobrado_hoy"],
        cuerpo["cobranza"]["saldo_vencido"],
    ):
        assert isinstance(valor, str), valor
        assert valor.count(".") == 1 and len(valor.split(".")[1]) == 2, valor

    # Las cantidades llevan TRES decimales, no dos: una merma puede ser 0.500.
    assert cuerpo["mermas"]["unidades"].split(".")[1] == "000"


async def test_el_tablero_resume_el_dia(cliente, sesion, semilla, dia_con_operacion):
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()

    assert cuerpo["venta"]["total"] == "1234.56"
    assert cuerpo["venta"]["documentos"] == 1
    assert cuerpo["venta"]["ticket_promedio"] == "1234.56"
    # Dos clientes visitados, uno compró: 50%.
    assert cuerpo["visitas"]["visitas"] == 2
    assert cuerpo["visitas"]["con_venta"] == 1
    assert cuerpo["visitas"]["efectividad"] == "50.0"
    # El no-drop es 'AGOTADO_EN_CAMION': categoría 'producto', o sea NUESTRO.
    assert cuerpo["visitas"]["no_drops_nuestros"] == 1
    assert cuerpo["cobranza"]["cobrado_efectivo"] == "500.25"


async def test_el_drop_size_se_mide_sobre_las_visitas_que_vendieron(
    cliente, sesion, semilla, dia_con_operacion
):
    """Sobre las 2 visitas daría $617.28, que es otra métrica con otro nombre."""
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    assert cuerpo["visitas"]["drop_size"] == "1234.56"


async def test_el_desglose_por_vendedor_cuadra_con_el_total(
    cliente, sesion, semilla, dia_con_operacion
):
    """El grano por vendedor es COMPLETO: su suma ES el día.

    Si no cuadrara, el modelo de lectura estaría mal elegido — habría documentos
    que no caben en ningún renglón.
    """
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    suma = sum(Decimal(v["venta"]) for v in cuerpo["vendedores"])
    assert suma == Decimal(cuerpo["venta"]["total"])


async def test_el_avance_trae_el_objetivo_y_lo_esperado(
    cliente, sesion, semilla, dia_con_operacion
):
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    avance = cuerpo["avance"]
    assert avance["dias_del_mes"] in (28, 29, 30, 31)
    ruta = next(r for r in avance["rutas"] if r["codigo"] == "R04")
    assert ruta["objetivo"] == "100000.00"
    assert ruta["venta_mes"] == "1234.56"
    # 1234.56 de 100000 el día N: siempre atrás salvo el día 1.
    assert ruta["logrado"] == "1.2"
    assert ruta["esperado"] is not None
    assert ruta["semaforo"] in ("adelante", "cerca", "atras")


async def test_el_esperado_se_prorratea_contra_hoy_no_contra_el_dia_pedido(
    cliente, sesion, semilla, dia_con_operacion
):
    """`tablero_mes_ruta` guarda el mes A LA FECHA, no hasta el día pedido.

    Si hoy es 20 y alguien abre el tablero del día 5, `venta_mes` sigue trayendo
    los veinte días. Prorratear contra el día 5 compararía veinte días de venta
    con cinco de objetivo, y toda ruta se vería adelantadísima.
    """
    hoy = date.today()
    if hoy.day == 1:
        pytest.skip("el día 1 no hay un 'día anterior del mismo mes' que pedir")

    anterior = hoy.replace(day=hoy.day - 1)
    cab = await _cab(cliente)
    de_hoy = (await cliente.get("/v1/tablero", headers=cab)).json()["avance"]
    de_ayer = (
        await cliente.get(f"/v1/tablero?fecha={anterior.isoformat()}", headers=cab)
    ).json()["avance"]

    assert de_hoy["dia_del_mes"] == hoy.day
    assert de_ayer["dia_del_mes"] == hoy.day, (
        "el día del mes del prorrateo tiene que ser HOY aunque se pida otro día"
    )
    ruta_hoy = next(r for r in de_hoy["rutas"] if r["codigo"] == "R04")
    ruta_ayer = next(r for r in de_ayer["rutas"] if r["codigo"] == "R04")
    assert ruta_hoy["esperado"] == ruta_ayer["esperado"]


async def test_una_ruta_sin_objetivo_aparece_sin_barra(
    cliente, sesion, semilla, dia_con_operacion
):
    """Con un `JOIN` desaparecería justo la ruta a la que falta ponerle meta."""
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R09', 'Ruta 9')"),
        {"r": uuid.uuid4()},
    )
    await sesion.commit()
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    nueva = next(r for r in cuerpo["avance"]["rutas"] if r["codigo"] == "R09")
    assert nueva["objetivo"] is None
    assert nueva["logrado"] is None
    assert nueva["semaforo"] == "sin_objetivo"


async def test_la_venta_sin_ruta_se_muestra_no_se_esconde(
    cliente, sesion, semilla, dia_con_operacion
):
    """Es la diferencia entre el total del mes y la suma de las barras."""
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 99, 'VEND01-000099', :c, :u, NULL, :a, 'contado',
                    777.00, 777.00, now(), CURRENT_DATE)
            """
        ),
        {
            "v": uuid.uuid4(), "d": dia_con_operacion["dispositivo"],
            "c": dia_con_operacion["clientes"]["Mary"], "u": semilla["vendedor"],
            "a": semilla["camion"],
        },
    )
    await sesion.commit()
    await recalcular_todo(sesion)

    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    assert cuerpo["avance"]["venta_sin_ruta"] == "777.00"
    assert cuerpo["avance"]["documentos_sin_ruta"] == 1


# ---------------------------------------------------------------------------
# Frescura
# ---------------------------------------------------------------------------
async def test_un_tablero_nunca_calculado_lo_dice(cliente, sesion, semilla):
    """`minutos = None`, no 0. Un cero se lee como "recién calculado"."""
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    assert cuerpo["frescura"]["calculado_en"] is None
    assert cuerpo["frescura"]["minutos"] is None
    assert cuerpo["frescura"]["confiable"] is False
    assert "no se ha calculado" in cuerpo["frescura"]["advertencia"]


async def test_un_equipo_sin_sincronizar_vuelve_la_cifra_un_piso(
    cliente, sesion, semilla, dia_con_operacion
):
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en, "
            "                          ultima_sync_push_en) "
            "VALUES (:d, :u, 'Equipo rezagado', 'activo', now(), NULL)"
        ),
        {"d": uuid.uuid4(), "u": semilla["admin"]},
    )
    await sesion.commit()

    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    assert cuerpo["frescura"]["equipos_sin_sincronizar"] == 1
    assert cuerpo["frescura"]["confiable"] is False
    assert "piso" in cuerpo["frescura"]["advertencia"]


async def test_cada_bloque_trae_su_propia_marca(
    cliente, sesion, semilla, dia_con_operacion
):
    """La cartera es un SALDO: su antigüedad no es la del día operativo."""
    cuerpo = (await cliente.get("/v1/tablero", headers=await _cab(cliente))).json()
    assert cuerpo["venta"]["calculado_en"] is not None
    assert cuerpo["cobranza"]["calculado_en"] is not None
    assert cuerpo["mermas"]["calculado_en"] is not None


# ---------------------------------------------------------------------------
# La fecha
# ---------------------------------------------------------------------------
async def test_un_dia_futuro_no_devuelve_ceros(cliente, sesion, semilla):
    """Devolverlos diría "no se vendió nada" de un día que no ha ocurrido."""
    manana = date.today() + timedelta(days=1)
    r = await cliente.get(
        f"/v1/tablero?fecha={manana.isoformat()}", headers=await _cab(cliente)
    )
    assert r.status_code == 422
    assert "no ha ocurrido" in r.json()["detail"]


async def test_un_dia_pasado_sin_operacion_devuelve_ceros_no_un_error(
    cliente, sesion, semilla
):
    """Es información: ese día nadie vendió."""
    hace_un_mes = date.today() - timedelta(days=30)
    r = await cliente.get(
        f"/v1/tablero?fecha={hace_un_mes.isoformat()}", headers=await _cab(cliente)
    )
    assert r.status_code == 200
    assert r.json()["venta"]["total"] == "0.00"
    assert r.json()["visitas"]["efectividad"] == "0.0"


# ---------------------------------------------------------------------------
# El mapa
# ---------------------------------------------------------------------------
async def test_el_mapa_trae_ventas_y_no_drops(cliente, sesion, semilla, dia_con_operacion):
    cuerpo = (await cliente.get("/v1/tablero/mapa", headers=await _cab(cliente))).json()
    clases = {p["clase"] for p in cuerpo["puntos"]}
    assert clases == {"venta", "no_drop"}
    assert cuerpo["recortados"] is False

    no_drop = next(p for p in cuerpo["puntos"] if p["clase"] == "no_drop")
    # El motivo viene con nombre legible: 'AGOTADO_EN_CAMION' no dice nada en
    # una pantalla de teléfono.
    assert no_drop["motivo"] == "No traigo lo que pidió"
    assert no_drop["importe"] is None


async def test_una_venta_sin_gps_no_aparece_en_el_mapa(
    cliente, sesion, semilla, dia_con_operacion
):
    """Y eso es información: significa que el GPS no respondió.

    Dibujarla en el centro del mapa, o en 0,0, sería inventar una ubicación.
    """
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 50, 'VEND01-000050', :c, :u, :r, :a, 'contado',
                    10.00, 10.00, now(), CURRENT_DATE)
            """
        ),
        {
            "v": uuid.uuid4(), "d": dia_con_operacion["dispositivo"],
            "c": dia_con_operacion["clientes"]["Mary"], "u": semilla["vendedor"],
            "r": semilla["ruta"], "a": semilla["camion"],
        },
    )
    await sesion.commit()
    cuerpo = (await cliente.get("/v1/tablero/mapa", headers=await _cab(cliente))).json()
    assert len([p for p in cuerpo["puntos"] if p["clase"] == "venta"]) == 1


async def test_el_mapa_tambien_exige_el_permiso(cliente, sesion, semilla):
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
        "dispositivo_id": str(dispositivo),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await cliente.get("/v1/tablero/mapa", headers=cab)).status_code == 403
