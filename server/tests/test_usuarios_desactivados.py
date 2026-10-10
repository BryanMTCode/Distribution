"""Los usuarios desactivados, en su propia lista (ADR 0002 §93).

Pedido de la dirección (octubre 2026): «que los usuarios que estén desactivados
no aparezcan en el dashboard; que aparezcan en una distinta, pero no ahí con
todos, igual en la app».

1. **Usuarios y rutas** lista solo a los activos y dice cuántos desactivados hay;
   «Desactivados» los lista aparte, y desde ahí se reactivan.
2. **Vendedores** —en el panel y en la app (`/v1/vendedores`)— muestra a los
   activos; los desactivados, con `ver=desactivados` / `desactivados=true`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel as _csrf
from tests.conftest import solo_texto
from tests.test_oficina_clientes_api import _cab
from tests.test_panel_editar_estructura import _entrar

pytestmark = pytest.mark.asyncio


async def _desactivar_al_vendedor(cliente, semilla) -> None:
    r = await cliente.get("/panel/equipo")
    r = await cliente.post(
        f"/panel/equipo/usuarios/{semilla['vendedor']}/activo",
        data={"csrf": _csrf(cliente, r)},
        follow_redirects=True,
    )
    assert "Ahora está en «Desactivados»" in solo_texto(r)


async def test_usuarios_y_rutas_no_mezcla_a_los_desactivados(cliente, sesion, semilla):
    await _entrar(cliente)
    antes = (await cliente.get("/panel/equipo")).text
    assert "Ningún usuario desactivado." in solo_texto_html(antes)

    await _desactivar_al_vendedor(cliente, semilla)
    html = (await cliente.get("/panel/equipo")).text
    tabla = html.split("<h2>Usuarios</h2>")[1].split("</table>")[0]
    assert "VEND01" not in tabla
    assert "ADMIN01" in tabla
    assert "ver los 1 desactivado(s)" in solo_texto_html(html)

    aparte = await cliente.get("/panel/equipo/desactivados")
    assert aparte.status_code == 200
    assert 'id="desactivado_VEND01"' in aparte.text
    assert 'id="desactivado_ADMIN01"' not in aparte.text

    # Se reactiva desde ahí y se vuelve a la misma lista, ya sin él.
    r = await cliente.post(
        f"/panel/equipo/usuarios/{semilla['vendedor']}/activo",
        data={"csrf": _csrf(cliente, aparte), "activo": "1", "volver": "desactivados"},
        follow_redirects=True,
    )
    assert str(r.url).split("?")[0].endswith("/panel/equipo/desactivados")
    assert "Usuario activado" in solo_texto(r)
    assert 'id="desactivado_VEND01"' not in r.text
    activo = (
        await sesion.execute(
            text("SELECT activo FROM usuarios WHERE id = :u"), {"u": semilla["vendedor"]}
        )
    ).scalar_one()
    assert activo is True


async def test_vendedores_activos_y_desactivados_por_separado(cliente, semilla):
    await _entrar(cliente)
    assert "VEND01" in solo_texto(await cliente.get("/panel/vendedores"))

    await _desactivar_al_vendedor(cliente, semilla)
    activos = await cliente.get("/panel/vendedores")
    assert "VEND01" not in solo_texto(activos)
    assert "Desactivados (1)" in solo_texto(activos)
    aparte = solo_texto(await cliente.get("/panel/vendedores?ver=desactivados"))
    assert "VEND01" in aparte and "desactivado" in aparte


async def test_la_app_pide_a_los_desactivados_aparte(cliente, semilla):
    await _entrar(cliente)
    await _desactivar_al_vendedor(cliente, semilla)
    cab = await _cab(cliente)

    activos = (await cliente.get("/v1/vendedores", headers=cab)).json()
    assert [v["codigo"] for v in activos["vendedores"]] == []
    assert activos["desactivados"] == 1

    aparte = (await cliente.get("/v1/vendedores?desactivados=true", headers=cab)).json()
    assert [(v["codigo"], v["activo"]) for v in aparte["vendedores"]] == [("VEND01", False)]


def solo_texto_html(html: str) -> str:
    """`solo_texto` para un HTML ya leído."""

    class _R:
        text = html

    return solo_texto(_R())
