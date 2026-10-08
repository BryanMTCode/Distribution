"""`/v1/oficina/clientes`: los clientes desde el teléfono de la oficina.

1. **Quién.** La oficina ve a todos; el vendedor no (los de su ruta los baja por
   la sincronización).
2. **Solo contado** (ADR 0002 §81): sin saldo, sin cartera, sin bloqueo. La
   ficha dice dónde está el cliente, qué compra y cómo pagó.
"""

from __future__ import annotations

import uuid
from datetime import date

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


async def test_la_lista_busca_y_filtra_los_que_no_tienen_ubicacion(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(
        text("UPDATE clientes SET lat = 23.2329, lng = -106.4062 WHERE id = :c"),
        {"c": dia["cliente"]},
    )
    await sesion.execute(
        text("INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
             "actualizado_en) VALUES (:c, 'CLI-L2', 'Abarrotes Sin Mapa', :r, now(), now())"),
        {"c": uuid.uuid4(), "r": semilla["ruta"]},
    )
    await sesion.commit()
    await _usuario(sesion, semilla, "GER01", "gerente")
    cab = await _cab(cliente, "GER01")

    cuerpo = (await cliente.get("/v1/oficina/clientes", headers=cab)).json()
    assert [c["nombre"] for c in cuerpo["clientes"]] == ["Abarrotes Sin Mapa", "La Esquina"]
    esquina = cuerpo["clientes"][1]
    assert esquina["con_ubicacion"] is True
    assert esquina["ultima_compra"] == date.today().isoformat()
    assert "saldo" not in esquina
    assert cuerpo["conteos"]["sin_ubicacion"] == 1

    sin = (await cliente.get("/v1/oficina/clientes?filtro=sin_ubicacion", headers=cab)).json()
    assert [c["nombre"] for c in sin["clientes"]] == ["Abarrotes Sin Mapa"]

    buscado = (await cliente.get("/v1/oficina/clientes?q=esquina", headers=cab)).json()
    assert [c["nombre"] for c in buscado["clientes"]] == ["La Esquina"]


async def test_la_ficha_trae_sus_compras_con_la_forma_de_pago(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)

    f = (await cliente.get(f"/v1/oficina/clientes/{dia['cliente']}", headers=cab)).json()

    assert f["nombre"] == "La Esquina"
    [venta] = f["ventas"]
    assert (venta["tipo"], venta["forma_pago"], venta["total"]) == (
        "contado", "efectivo", "2250.00"
    )
    assert f["comprado_mes"] == "2250.00"
    assert f["lat"] is None
    for ya_no in ("saldo", "cuentas", "cobros", "puede_bloquear", "bloqueado"):
        assert ya_no not in f, ya_no


async def test_bloquear_ya_no_existe(cliente, sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    cab = await _cab(cliente)
    r = await cliente.post(
        f"/v1/oficina/clientes/{dia['cliente']}/bloqueo",
        json={"bloquear": True, "motivo": "x"},
        headers=cab,
    )
    assert r.status_code in (404, 405)


async def test_un_cliente_que_no_existe_es_404(cliente, semilla):
    cab = await _cab(cliente)
    assert (
        await cliente.get(f"/v1/oficina/clientes/{uuid.uuid4()}", headers=cab)
    ).status_code == 404
