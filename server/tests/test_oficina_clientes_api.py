"""`/v1/oficina/clientes`: los clientes desde el teléfono de la oficina.

1. **Quién.** La oficina ve a todos; el vendedor no (su cartera la baja por
   `/v1/clientes`, solo la de su ruta). Bloquear exige `clientes.administrar`.
2. **El estado de cuenta.** Lo que debe, lo vencido y desde cuándo, de la misma
   vista que usa el panel.
3. **Bloquear pide motivo** y no impide el contado.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

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


async def _usuario(sesion, semilla, codigo: str, rol: str) -> None:
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, :c, :n, :h, :r, now(), now())"
        ),
        {"u": uuid.uuid4(), "s": semilla["sucursal"], "c": codigo, "n": f"Prueba {rol}",
         "h": hashear_password(PASSWORD_VENDEDOR), "r": rol},
    )
    await sesion.commit()


async def _venta_a_credito_vencida(sesion, semilla, dia, *, importe="500.00") -> uuid.UUID:
    """Una venta a crédito de hace 20 días, con 7 de plazo: vencida hace 13."""
    venta = uuid.uuid4()
    hace = date.today() - timedelta(days=20)
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 9, 'VEND01-000009', :c, :u, :a, 'credito',
                    :i, :i, now() - interval '20 days', :f)
            """
        ),
        {"v": venta, "d": dia["dispositivo"], "c": dia["cliente"],
         "u": semilla["vendedor"], "a": semilla["camion"], "i": importe, "f": hace},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                            importe_pagado, fecha_emision,
                                            fecha_vencimiento, estado)
            VALUES (:v, :c, :i, 100.00, :f, :vence, 'parcial')
            """
        ),
        {"v": venta, "c": dia["cliente"], "i": importe, "f": hace,
         "vence": hace + timedelta(days=7)},
    )
    await sesion.commit()
    return venta


async def test_el_vendedor_no_ve_a_todos_los_clientes(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
        "dispositivo_id": str(dia["dispositivo"]),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await cliente.get("/v1/oficina/clientes", headers=cab)
    assert r.status_code == 403
    assert "ventas.ver_todas" in r.json()["detail"]


async def test_la_lista_pone_primero_lo_que_urge_cobrar(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _venta_a_credito_vencida(sesion, semilla, dia)
    await sesion.execute(
        text("INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
             "actualizado_en) VALUES (:c, 'CLI-L2', 'Abarrotes Al Corriente', :r, now(), now())"),
        {"c": uuid.uuid4(), "r": semilla["ruta"]},
    )
    await sesion.commit()
    await _usuario(sesion, semilla, "GER01", "gerente")
    cab = await _cab(cliente, "GER01")

    cuerpo = (await cliente.get("/v1/oficina/clientes", headers=cab)).json()
    primero = cuerpo["clientes"][0]
    assert primero["nombre"] == "La Esquina"
    assert primero["saldo"] == "400.00"
    assert primero["saldo_vencido"] == "400.00"
    assert primero["ultima_compra"] == date.today().isoformat()
    assert cuerpo["conteos"]["vencidos"] == 1

    # El filtro de vencidos deja solo a quien debe.
    vencidos = (
        await cliente.get("/v1/oficina/clientes?filtro=vencidos", headers=cab)
    ).json()
    assert [c["nombre"] for c in vencidos["clientes"]] == ["La Esquina"]

    # Y la búsqueda.
    buscado = (await cliente.get("/v1/oficina/clientes?q=corriente", headers=cab)).json()
    assert [c["nombre"] for c in buscado["clientes"]] == ["Abarrotes Al Corriente"]


async def test_la_ficha_trae_su_estado_de_cuenta_y_sus_compras(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    venta = await _venta_a_credito_vencida(sesion, semilla, dia)
    cab = await _cab(cliente)

    f = (await cliente.get(f"/v1/oficina/clientes/{dia['cliente']}", headers=cab)).json()

    assert f["nombre"] == "La Esquina"
    assert f["saldo"] == "400.00"
    [cuenta] = f["cuentas"]
    assert cuenta["venta_id"] == str(venta)
    assert cuenta["vencida"] is True
    assert cuenta["dias_vencida"] == 13
    assert cuenta["saldo"] == "400.00"
    # Sus compras, la más reciente primero: la de contado de hoy y la de crédito.
    assert [v["tipo"] for v in f["ventas"]] == ["contado", "credito"]
    # La de hace 20 días puede caer en el año pasado si hoy es principio de enero.
    assert f["comprado_anio"] in ("2750.00", "2250.00")
    assert f["puede_bloquear"] is True


async def test_bloquear_pide_motivo_y_desbloquear_no(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)
    ruta = f"/v1/oficina/clientes/{dia['cliente']}/bloqueo"

    r = await cliente.post(ruta, json={"bloquear": True}, headers=cab)
    assert r.status_code == 422
    assert "por qué" in r.json()["detail"]

    r = await cliente.post(ruta, json={"bloquear": True, "motivo": "Debe tres notas"},
                           headers=cab)
    assert r.json()["bloqueado"] is True
    assert r.json()["bloqueo_motivo"] == "Debe tres notas"
    assert "contado" in r.json()["mensaje"]

    r = await cliente.post(ruta, json={"bloquear": False}, headers=cab)
    assert r.json()["bloqueado"] is False
    assert r.json()["bloqueo_motivo"] is None


async def test_el_gerente_ve_pero_no_bloquea(cliente, sesion, semilla):
    """Bloquear es `clientes.administrar`, como en el panel: el gerente no lo tiene."""
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _usuario(sesion, semilla, "GER01", "gerente")
    cab = await _cab(cliente, "GER01")

    f = (await cliente.get(f"/v1/oficina/clientes/{dia['cliente']}", headers=cab)).json()
    assert f["puede_bloquear"] is False
    r = await cliente.post(
        f"/v1/oficina/clientes/{dia['cliente']}/bloqueo",
        json={"bloquear": True, "motivo": "x"},
        headers=cab,
    )
    assert r.status_code == 403


async def test_un_cliente_que_no_existe_es_404(cliente, semilla):
    cab = await _cab(cliente)
    assert (
        await cliente.get(f"/v1/oficina/clientes/{uuid.uuid4()}", headers=cab)
    ).status_code == 404
