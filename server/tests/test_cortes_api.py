"""`/v1/cortes`: el corte del día desde la app.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
1. **Quién.** Admin, supervisor y gerente cortan; el vendedor no, ni con su
   token y `curl`.
2. **Las mismas reglas que el panel.** El conteo vacío vale cero; no se cierra con
   el teléfono atrasado; sin dato de sincronización hay que confirmarlo; cerrar dos
   veces no ajusta dos veces.
3. **Lo que importa al final:** el cierre deja el camión en lo contado, le carga a
   la cuenta del vendedor lo que falta y publica la carga liquidada al teléfono.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR
from tests.test_panel_liquidacion import _reportar_cola, sembrar_dia_de_trabajo

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


async def _abrir(cliente, cab, dia) -> dict:
    r = await cliente.post("/v1/cortes", json={"carga_id": str(dia["carga"])}, headers=cab)
    assert r.status_code == 200, r.text
    return r.json()


async def _existencia(sesion, almacen, producto):
    return (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a AND producto_id = :p"),
            {"a": almacen, "p": producto},
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Quién
# ---------------------------------------------------------------------------
async def test_el_vendedor_no_corta_ni_con_su_token(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR,
        "dispositivo_id": str(dia["dispositivo"]),
    })
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}

    for respuesta in (
        await cliente.get("/v1/cortes", headers=cab),
        await cliente.post("/v1/cortes", json={"carga_id": str(dia["carga"])}, headers=cab),
    ):
        assert respuesta.status_code == 403
        assert "inventario.liquidar" in respuesta.json()["detail"]


@pytest.mark.parametrize("rol", ["gerente", "supervisor"])
async def test_los_puestos_de_arriba_si_cortan(cliente, sesion, semilla, rol):
    """El gerente, desde la migración 0045; el supervisor, desde siempre."""
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _usuario(sesion, semilla, "ARRIBA01", rol)
    cab = await _cab(cliente, "ARRIBA01")

    lista = (await cliente.get("/v1/cortes", headers=cab)).json()
    [pendiente] = lista["por_cortar"]
    assert pendiente["carga_id"] == str(dia["carga"])
    assert pendiente["vendedor"] == "Juan Pérez"

    corte = await _abrir(cliente, cab, dia)
    assert corte["abierto"] is True


# ---------------------------------------------------------------------------
# El corte, de punta a punta
# ---------------------------------------------------------------------------
async def test_abrir_trae_lo_cargado_lo_vendido_y_lo_esperado(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)  # 240 cargadas, 180 vendidas
    cab = await _cab(cliente)

    corte = await _abrir(cliente, cab, dia)

    [r] = corte["renglones"]
    assert r["cargada"] == "240.000"
    assert r["vendida"] == "180.000"
    assert r["esperado"] == "60.000"
    # El conteo nace en CERO, no en lo esperado: cerrar sin contar no cuadra.
    assert r["contada"] == "0.000"
    assert corte["efectivo_esperado"] == "2250.00"

    # Abrir otra vez lleva al mismo corte.
    assert (await _abrir(cliente, cab, dia))["id"] == corte["id"]


async def test_contar_arqueo_y_cerrar_cuadrado(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _reportar_cola(sesion, dia["dispositivo"], 0)
    cab = await _cab(cliente)
    corte = await _abrir(cliente, cab, dia)
    renglon = corte["renglones"][0]["id"]

    r = await cliente.post(
        f"/v1/cortes/{corte['id']}/conteo", json={"contados": {renglon: "60"}}, headers=cab
    )
    assert r.json()["renglones"][0]["diferencia"] == "0.000"

    r = await cliente.post(
        f"/v1/cortes/{corte['id']}/arqueo", json={"efectivo": "2250.00"}, headers=cab
    )
    assert r.json()["mensaje"].startswith("Arqueo cuadrado")
    assert r.json()["arqueo_hecho"] is True

    # Con cero reportado hoy, no hace falta confirmar a mano.
    r = await cliente.post(f"/v1/cortes/{corte['id']}/cerrar", json={}, headers=cab)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "cerrada"
    assert cuerpo["abierto"] is False
    assert "Cuadró producto por producto" in cuerpo["mensaje"]
    # La mercancía se queda arriba: el camión sigue con sus 60.
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 60
    estado = (
        await sesion.execute(text("SELECT estado FROM cargas WHERE id = :c"), {"c": dia["carga"]})
    ).scalar_one()
    assert estado == "liquidada"


async def test_un_faltante_deja_el_camion_en_lo_contado_y_se_le_carga(
    cliente, sesion, semilla
):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _reportar_cola(sesion, dia["dispositivo"], 0)
    cab = await _cab(cliente)
    corte = await _abrir(cliente, cab, dia)
    renglon = corte["renglones"][0]["id"]
    await cliente.post(
        f"/v1/cortes/{corte['id']}/conteo", json={"contados": {renglon: "55"}}, headers=cab
    )

    r = await cliente.post(f"/v1/cortes/{corte['id']}/cerrar", json={}, headers=cab)

    assert "faltante de 5 unidades" in r.json()["mensaje"]
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 55


async def test_un_renglon_que_no_viene_vale_cero(cliente, sesion, semilla):
    """Como en el panel: lo que no se anotó es lo que no está arriba."""
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)
    corte = await _abrir(cliente, cab, dia)
    r = await cliente.post(
        f"/v1/cortes/{corte['id']}/conteo", json={"contados": {}}, headers=cab
    )
    assert r.json()["renglones"][0]["contada"] == "0.000"
    assert r.json()["renglones"][0]["diferencia"] == "-60.000"


async def test_un_conteo_que_no_se_entiende_no_guarda_nada(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)
    corte = await _abrir(cliente, cab, dia)
    renglon = corte["renglones"][0]["id"]
    r = await cliente.post(
        f"/v1/cortes/{corte['id']}/conteo", json={"contados": {renglon: "-3"}}, headers=cab
    )
    assert r.status_code == 409
    assert "negativo" in r.json()["detail"]


async def test_con_el_telefono_atrasado_no_se_cierra(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _reportar_cola(sesion, dia["dispositivo"], 3)
    cab = await _cab(cliente)
    corte = await _abrir(cliente, cab, dia)
    assert any("3 operación(es)" in b for b in corte["bloqueos"])

    r = await cliente.post(
        f"/v1/cortes/{corte['id']}/cerrar", json={"confirmo_sincronizado": True}, headers=cab
    )
    assert r.status_code == 409
    assert "No se puede cerrar" in r.json()["detail"]


async def test_sin_dato_de_sincronizacion_hay_que_confirmarlo(cliente, sesion, semilla):
    """El equipo nunca reportó su cola: se pide la palabra de quien cierra."""
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)
    corte = await _abrir(cliente, cab, dia)
    assert corte["respaldo"]["respaldado"] is False

    r = await cliente.post(f"/v1/cortes/{corte['id']}/cerrar", json={}, headers=cab)
    assert r.status_code == 409
    assert "Confirma que el teléfono" in r.json()["detail"]

    r = await cliente.post(
        f"/v1/cortes/{corte['id']}/cerrar", json={"confirmo_sincronizado": True}, headers=cab
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "cerrada"


async def test_cerrar_dos_veces_no_ajusta_dos_veces(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await _reportar_cola(sesion, dia["dispositivo"], 0)
    cab = await _cab(cliente)
    corte = await _abrir(cliente, cab, dia)
    renglon = corte["renglones"][0]["id"]
    await cliente.post(
        f"/v1/cortes/{corte['id']}/conteo", json={"contados": {renglon: "50"}}, headers=cab
    )
    await cliente.post(f"/v1/cortes/{corte['id']}/cerrar", json={}, headers=cab)

    r = await cliente.post(f"/v1/cortes/{corte['id']}/cerrar", json={}, headers=cab)
    assert r.status_code == 409
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 50

    # Y aparece entre los cerrados, ya no entre los pendientes.
    lista = (await cliente.get("/v1/cortes", headers=cab)).json()
    assert lista["por_cortar"] == []
    assert lista["cortes"][0]["estado"] == "cerrada"


async def test_un_corte_que_no_existe_es_404(cliente, semilla):
    cab = await _cab(cliente)
    assert (await cliente.get(f"/v1/cortes/{uuid.uuid4()}", headers=cab)).status_code == 404
    r = await cliente.post("/v1/cortes", json={"carga_id": str(uuid.uuid4())}, headers=cab)
    assert r.status_code == 404
