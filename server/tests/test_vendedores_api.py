"""`/v1/vendedores`: cada vendedor, su camión y lo que hizo, desde la app.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
1. **Quién.** La oficina (`ventas.ver_todas`) ve a todos; el vendedor, a nadie
   —ni el camión de otro, aunque tenga `inventario.ver` para el suyo—.
2. **Lo mismo que el panel.** Las cifras salen de las mismas consultas: la venta
   del día cuenta igual aquí que en el dashboard.
3. **El camión con lo vendido restado**, y la venta con lo que se vendió.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR
from tests.test_panel_liquidacion import sembrar_dia_de_trabajo

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


async def test_el_vendedor_no_ve_a_los_demas_ni_su_camion_por_aqui(cliente, sesion, semilla):
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}

    for ruta in (
        "/v1/vendedores",
        f"/v1/vendedores/{semilla['vendedor']}",
        f"/v1/vendedores/{semilla['vendedor']}/camion",
    ):
        respuesta = await cliente.get(ruta, headers=cab)
        assert respuesta.status_code == 403, ruta
        assert "ventas.ver_todas" in respuesta.json()["detail"]


async def test_la_lista_resume_el_periodo_como_el_panel(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)  # una venta de contado de $2,250 hoy
    cab = await _gerente(cliente, sesion, semilla)

    cuerpo = (await cliente.get("/v1/vendedores?periodo=hoy", headers=cab)).json()

    assert cuerpo["periodo"]["clave"] == "hoy"
    [juan] = cuerpo["vendedores"]
    assert juan["codigo"] == "VEND01"
    assert juan["camion"] == "Camión 01"
    assert juan["ventas"] == 1
    assert juan["importe"] == "2250.00"

    # Ayer no vendió: el periodo manda.
    ayer = (await cliente.get("/v1/vendedores?periodo=ayer", headers=cab)).json()
    assert ayer["vendedores"][0]["ventas"] == 0


async def test_el_detalle_trae_todo_lo_que_hizo_y_la_venta_se_abre(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)

    cuerpo = (
        await cliente.get(f"/v1/vendedores/{semilla['vendedor']}?periodo=hoy", headers=cab)
    ).json()

    assert cuerpo["vendedor"]["nombre"] == "Juan Pérez"
    tipos = {r["tipo"]: r for r in cuerpo["resumen"]}
    assert tipos["venta"]["cuantos"] == 1
    assert tipos["venta"]["importe"] == "2250.00"
    assert "carga" in tipos
    [venta] = [m for m in cuerpo["movimientos"] if m["tipo"] == "venta"]
    assert venta["folio"] == "VEND01-000001"
    assert venta["cliente"] == "La Esquina"
    assert venta["etiqueta"] == "Ventas"
    assert venta["ref"] == str(dia["venta"])

    # Y con ese id, lo que se le vendió.
    detalle = (await cliente.get(f"/v1/vendedores/ventas/{venta['ref']}", headers=cab)).json()
    assert detalle["cliente"] == "La Esquina"
    assert detalle["total"] == "2250.00"
    assert detalle["forma_pago"] == "efectivo"
    assert detalle["pago_estado"] == "confirmado"
    [partida] = detalle["partidas"]
    assert partida["cantidad"] == "180.000"
    assert partida["unidad"] == "PZA"
    assert partida["importe"] == "2250.00"


async def test_el_filtro_por_tipo_deja_solo_ese(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)
    cuerpo = (
        await cliente.get(
            f"/v1/vendedores/{semilla['vendedor']}?periodo=hoy&tipo=carga", headers=cab
        )
    ).json()
    assert {m["tipo"] for m in cuerpo["movimientos"]} == {"carga"}
    # El resumen sigue contando todo: es también el menú del filtro.
    assert {r["tipo"] for r in cuerpo["resumen"]} >= {"venta", "carga"}


async def test_el_camion_trae_lo_cargado_menos_lo_vendido(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)  # 240 cargadas, 180 vendidas
    cab = await _cab(cliente)

    cuerpo = (
        await cliente.get(f"/v1/vendedores/{semilla['vendedor']}/camion", headers=cab)
    ).json()

    assert cuerpo["camion"] == "Camión 01"
    [renglon] = cuerpo["existencias"]
    assert renglon["producto_id"] == str(dia["producto"])
    assert renglon["cantidad"] == "60.000"
    assert cuerpo["piezas"] == "60.000"
    assert cuerpo["ultimo_contacto"] is not None


async def test_un_vendedor_que_no_existe_es_404(cliente, semilla):
    cab = await _cab(cliente)
    r = await cliente.get(f"/v1/vendedores/{uuid.uuid4()}", headers=cab)
    assert r.status_code == 404
    r = await cliente.get(f"/v1/vendedores/ventas/{uuid.uuid4()}", headers=cab)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# El tablero por periodo, en la app
# ---------------------------------------------------------------------------
async def test_el_tablero_por_periodo_dice_lo_mismo_que_el_panel(cliente, sesion, semilla):
    from app.api.admin.panel import cifras_del_periodo
    from app.api.admin.periodo import leer_periodo

    await sembrar_dia_de_trabajo(sesion, semilla)  # $2,250 de contado hoy
    cab = await _gerente(cliente, sesion, semilla)

    cuerpo = (await cliente.get("/v1/tablero/periodo?periodo=semana", headers=cab)).json()

    assert cuerpo["periodo"]["clave"] == "semana"
    # Todo es de contado (ADR 0002 §81): por forma de pago.
    assert cuerpo["cifras"]["efectivo"] == "2250.00"
    assert cuerpo["cifras"]["transferencias"] == "0.00"
    assert cuerpo["cifras"]["total"] == "2250.00"
    [juan] = cuerpo["por_vendedor"]
    assert juan["importe"] == "2250.00"
    # Las mismas cifras que el tablero del panel, de la misma función.
    panel = await cifras_del_periodo(sesion, leer_periodo("semana"))
    assert cuerpo["cifras"]["no_ventas"] == panel["cifras"]["no_ventas"]
    assert len(cuerpo["por_dia"]) == len(panel["por_dia"])


async def test_un_rango_a_mano_y_un_dia_suelto(cliente, sesion, semilla):
    from datetime import date, timedelta

    await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)

    # Solo ayer: no hubo venta, y un solo día no se desglosa por día.
    r = await cliente.get(
        f"/v1/tablero/periodo?periodo=rango&desde={ayer}&hasta={ayer}", headers=cab
    )
    cuerpo = r.json()
    assert cuerpo["periodo"]["desde"] == cuerpo["periodo"]["hasta"] == ayer.isoformat()
    assert cuerpo["cifras"]["total"] == "0.00"
    assert cuerpo["por_dia"] == []

    # Ayer y hoy: dos renglones por día, el de hoy con la venta.
    cuerpo = (
        await cliente.get(
            f"/v1/tablero/periodo?periodo=rango&desde={ayer}&hasta={hoy}", headers=cab
        )
    ).json()
    dias = {d["fecha"]: d["importe"] for d in cuerpo["por_dia"]}
    assert dias == {hoy.isoformat(): "2250.00", ayer.isoformat(): "0.00"}


async def test_el_vendedor_no_ve_el_tablero_por_periodo(cliente, sesion, semilla):
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await cliente.get("/v1/tablero/periodo", headers=cab)).status_code == 403


# ---------------------------------------------------------------------------
# La empresa: el tamaño del negocio, en el panel y en la app
# ---------------------------------------------------------------------------
async def test_el_resumen_de_la_empresa_cuenta_lo_que_hay(cliente, sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)  # 1 cliente, 1 producto, $2,250
    cab = await _gerente(cliente, sesion, semilla)

    r = (await cliente.get("/v1/tablero/empresa", headers=cab)).json()

    assert r["clientes_activos"] == 1
    assert r["vendedores"] == 1
    assert r["vendedores_con_camion"] == 1
    # El admin de la semilla y el gerente de la prueba.
    assert r["usuarios_oficina"] == 2
    assert r["rutas"] == 1
    assert r["productos"] == 1
    assert r["camiones"] == 1
    assert r["piezas_en_camiones"] == "60.000"  # 240 cargadas − 180 vendidas
    assert r["vendido_mes"] == "2250.00"
    assert r["existencias_negativas"] == 0


async def test_la_pantalla_empresa_del_panel_dice_lo_mismo(cliente, sesion, semilla):
    from tests.test_plan_visita import _entrar

    await sembrar_dia_de_trabajo(sesion, semilla)
    await _entrar(cliente)
    r = await cliente.get("/panel/empresa")
    assert r.status_code == 200
    assert 'id="resumen_clientes"' in r.text
    assert "vendedores activos" in r.text
    assert "artículos activos" in r.text
    # Y al final del tablero, que desde ADR 0002 §98 la lleva en lugar de una
    # entrada propia en el menú.
    tablero = (await cliente.get("/panel")).text
    assert 'id="empresa"' in tablero and 'id="resumen_clientes"' in tablero


async def test_el_vendedor_no_ve_el_resumen_de_la_empresa(cliente, sesion, semilla):
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await cliente.get("/v1/tablero/empresa", headers=cab)).status_code == 403
