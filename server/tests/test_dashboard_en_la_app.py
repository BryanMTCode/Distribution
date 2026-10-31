"""Las pantallas del dashboard dentro de la app de la oficina (ADR 0002 §194).

«Solo quiero poder mover el almacén, agregar productos, ese tipo de cosas» desde
la app, sin actualizar la app de los vendedores. La app pone en Almacén los
botones que el servidor le dice; cada uno abre esa pantalla del dashboard con un
boleto que la app pide con su token:

1. **Los botones los decide el servidor**, solo para el administrador, y cada
   uno lleva a una pantalla del dashboard que existe.
2. **El boleto abre la sesión del dashboard** —la cookie de siempre— y lleva a
   la pantalla que se pidió, solo si es del dashboard.
3. **Sirve una vez y un minuto**; el de un usuario dado de baja no sirve. Ni la
   gerencia ni el vendedor lo piden.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.infra.boletos_del_panel import destino_seguro
from tests.test_cortes_api import _cab, _usuario
from tests.test_sync_pull import _cab_vendedor

pytestmark = pytest.mark.asyncio


async def _boleto(cliente, codigo: str = "ADMIN01") -> str:
    r = await cliente.post("/v1/panel/boleto", headers=await _cab(cliente, codigo))
    assert r.status_code == 200, r.text
    assert r.json()["entrar"] == "/panel/entrar/app"
    return r.json()["boleto"]


async def test_con_el_boleto_se_abre_el_dashboard_sin_contrasena(cliente, semilla):  # noqa: ARG001
    boleto = await _boleto(cliente)
    cliente.cookies.clear()

    r = await cliente.post("/panel/entrar/app", data={"boleto": boleto},
                           follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/panel"
    assert "dsd_panel" in r.headers["set-cookie"]
    assert "HttpOnly" in r.headers["set-cookie"]

    tablero = await cliente.get("/panel")
    assert tablero.status_code == 200 and "Tablero de operación" in tablero.text


async def test_sirve_una_sola_vez(cliente, semilla):  # noqa: ARG001
    boleto = await _boleto(cliente)
    await cliente.post("/panel/entrar/app", data={"boleto": boleto}, follow_redirects=False)
    cliente.cookies.clear()

    r = await cliente.post("/panel/entrar/app", data={"boleto": boleto},
                           follow_redirects=False)
    assert r.status_code == 200 and "ya venció" in r.text
    assert "dsd_panel" not in r.headers.get("set-cookie", "")


async def test_vence_al_minuto(cliente, sesion, semilla):  # noqa: ARG001
    boleto = await _boleto(cliente)
    await sesion.execute(
        text("UPDATE boletos_panel SET expira_en = :e"),
        {"e": datetime.now(UTC) - timedelta(seconds=1)},
    )
    await sesion.commit()
    cliente.cookies.clear()

    r = await cliente.post("/panel/entrar/app", data={"boleto": boleto},
                           follow_redirects=False)
    assert r.status_code == 200 and "ya venció" in r.text


async def test_el_de_un_usuario_dado_de_baja_no_sirve(cliente, sesion, semilla):
    boleto = await _boleto(cliente)
    await sesion.execute(
        text("UPDATE usuarios SET activo = false WHERE id = :u"), {"u": semilla["admin"]}
    )
    await sesion.commit()
    cliente.cookies.clear()

    r = await cliente.post("/panel/entrar/app", data={"boleto": boleto},
                           follow_redirects=False)
    assert r.status_code == 200 and "ya venció" in r.text


async def test_los_botones_los_decide_el_servidor(cliente, semilla):  # noqa: ARG001
    from app.api.v1.panel import ATAJOS

    r = await cliente.get("/v1/panel/atajos", headers=await _cab(cliente))
    assert r.status_code == 200
    atajos = r.json()
    assert [a["titulo"] for a in atajos] == [a.titulo for a in ATAJOS]
    assert atajos[0] == {
        "titulo": "Mover mercancía", "icono": "mover", "ruta": "/panel/inventario/mover",
        "descripcion": "De la bodega a un camión, de regreso, o de un camión a otro",
    }

    # Cada botón lleva a una pantalla del dashboard que existe y abre.
    cliente.cookies.clear()
    boleto = await _boleto(cliente)
    await cliente.post("/panel/entrar/app", data={"boleto": boleto}, follow_redirects=False)
    for a in atajos:
        pantalla = await cliente.get(a["ruta"])
        assert pantalla.status_code == 200, a["ruta"]


async def test_ni_la_gerencia_ni_el_vendedor(cliente, sesion, semilla):
    await _usuario(sesion, semilla, "GER07", "gerente")
    gerente = await _cab(cliente, "GER07")
    assert (await cliente.get("/v1/panel/atajos", headers=gerente)).json() == []
    assert (await cliente.post("/v1/panel/boleto", headers=gerente)).status_code == 403

    vendedor = await _cab_vendedor(cliente, sesion, semilla)
    assert (await cliente.get("/v1/panel/atajos", headers=vendedor)).json() == []
    assert (await cliente.post("/v1/panel/boleto", headers=vendedor)).status_code == 403

    assert (await cliente.post("/v1/panel/boleto")).status_code == 401


async def test_el_excel_en_el_navegador_y_solo_pantallas_del_dashboard(cliente, semilla):  # noqa: ARG001
    """El Excel se baja en el navegador del teléfono: el boleto va en la URL y
    lleva a la pantalla que se pidió."""
    boleto = await _boleto(cliente)
    cliente.cookies.clear()
    r = await cliente.get(
        "/panel/entrar/app",
        params={"boleto": boleto, "siguiente": "/panel/reporte/excel?periodo=hoy"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/reporte/excel?periodo=hoy"
    excel = await cliente.get(r.headers["location"])
    assert excel.status_code == 200 and excel.content.startswith(b"PK")

    # A otro sitio, nunca.
    for malo in ("https://otro.com/panel", "//otro.com/panel", "/panelx", "/v1/sync",
                 "/panel\\..\\otro", ""):
        assert destino_seguro(malo) == "/panel", malo
    assert destino_seguro("/panel/dinero?periodo=mes") == "/panel/dinero?periodo=mes"
