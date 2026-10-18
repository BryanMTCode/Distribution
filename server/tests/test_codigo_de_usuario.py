"""Cambiar el código de un usuario desde el dashboard (ADR 0002 §132).

«Si antes se llamaban VEND000, ahora quiero poder cambiarles la cuenta a la que
yo quiera, todo desde el dashboard.»

1. **Se cambia en la ficha**, con «Cambiar código» en «Más ▾» de la lista: el
   usuario entra con el nuevo y ya no con el viejo; su camión `CAMION_<viejo>`
   se renombra con él, y queda en la auditoría.
2. **El viejo queda apartado** para esa persona: nadie más lo toma, ni al
   cambiar su código ni al darse de alta. Ella sí puede volver a él.
3. **Lo que no se acepta**: el código de otro, acentos, ñ, guiones o espacios en
   medio, uno de un solo carácter.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR
from tests.conftest import csrf_del_panel as _csrf
from tests.test_panel_equipo import _crear_usuario, _entrar

pytestmark = pytest.mark.asyncio


async def _cambiar(cliente, usuario_id, codigo: str):
    ficha = await cliente.get(f"/panel/equipo/usuarios/{usuario_id}")
    return await cliente.post(
        f"/panel/equipo/usuarios/{usuario_id}/codigo",
        data={"csrf": _csrf(cliente, ficha), "codigo": codigo},
        follow_redirects=True,
    )


async def _codigo(sesion, usuario_id) -> str:
    return (
        await sesion.execute(text("SELECT codigo FROM usuarios WHERE id = :u"), {"u": usuario_id})
    ).scalar_one()


async def _apartados(sesion) -> list[tuple[str, str]]:
    filas = await sesion.execute(
        text("SELECT a.codigo, u.codigo FROM usuarios_codigos_anteriores a "
             "  JOIN usuarios u ON u.id = a.usuario_id ORDER BY a.codigo")
    )
    return [tuple(f) for f in filas]


async def test_se_cambia_desde_la_ficha_y_entra_con_el_nuevo(cliente, sesion, semilla):
    await _entrar(cliente)
    lista = await cliente.get("/panel/equipo")
    assert f'id="cambiar_codigo_{semilla["vendedor"]}"' in lista.text
    ficha = await cliente.get(f"/panel/equipo/usuarios/{semilla['vendedor']}")
    assert 'id="forma_codigo"' in ficha.text

    r = await _cambiar(cliente, semilla["vendedor"], " juan_p ")
    assert "VEND01 ahora es JUAN_P" in r.text
    assert "Su teléfono imprime «VEND01-…»" in r.text
    assert "VEND01</span> (hasta el" in r.text
    await sesion.rollback()
    assert await _codigo(sesion, semilla["vendedor"]) == "JUAN_P"
    assert await _apartados(sesion) == [("VEND01", "JUAN_P")]
    accion = (
        await sesion.execute(
            text("SELECT datos_antes->>'codigo', datos_despues->>'codigo' FROM auditoria "
                 " WHERE accion = 'cambiar_codigo'")
        )
    ).one()
    assert tuple(accion) == ("VEND01", "JUAN_P")

    # Gerencia entra al API con el código: el nuevo sí, el viejo ya no.
    r = await _cambiar(cliente, semilla["admin"], "BRYAN")
    assert "ADMIN01 ahora es BRYAN" in r.text
    nuevo = await cliente.post(
        "/v1/auth/login", json={"codigo": "BRYAN", "password": PASSWORD_VENDEDOR}
    )
    assert nuevo.status_code == 200, nuevo.text
    viejo = await cliente.post(
        "/v1/auth/login", json={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR}
    )
    assert viejo.status_code == 401


async def test_el_camion_cambia_con_el(cliente, sesion, semilla):
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="VEND02")
    maria = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'VEND02'"))
    ).scalar_one()
    r = await _cambiar(cliente, maria, "MARIA")
    assert "Su camión ahora es CAMION_MARIA." in r.text
    await sesion.rollback()
    camion = (
        await sesion.execute(
            text("SELECT a.codigo FROM almacenes a JOIN usuarios u ON u.almacen_id = a.id "
                 " WHERE u.id = :u"),
            {"u": maria},
        )
    ).scalar_one()
    assert camion == "CAMION_MARIA"


async def test_el_viejo_queda_apartado_para_esa_persona(cliente, sesion, semilla):
    await _entrar(cliente)
    await _cambiar(cliente, semilla["vendedor"], "JUAN")
    await _crear_usuario(cliente, codigo="VEND02")
    maria = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'VEND02'"))
    ).scalar_one()

    # Nadie más toma VEND01: ni cambiando su código, ni dándose de alta.
    r = await _cambiar(cliente, maria, "VEND01")
    assert "VEND01 fue de «Juan Pérez»" in r.text
    r = await _crear_usuario(cliente, codigo="VEND01")
    assert "VEND01 fue de «Juan Pérez»" in r.text
    await sesion.rollback()
    assert await _codigo(sesion, maria) == "VEND02"
    assert (
        await sesion.execute(text("SELECT count(*) FROM usuarios WHERE codigo = 'VEND01'"))
    ).scalar_one() == 0

    # Juan sí puede volver: VEND01 deja de estar apartado y JUAN pasa a estarlo.
    r = await _cambiar(cliente, semilla["vendedor"], "VEND01")
    assert "JUAN ahora es VEND01" in r.text
    await sesion.rollback()
    assert await _apartados(sesion) == [("JUAN", "VEND01")]


async def test_lo_que_no_se_acepta(cliente, sesion, semilla):
    await _entrar(cliente)
    for codigo, motivo in (
        ("ADMIN01", "ya lo tiene «Bryan»"),
        ("JUAN-P", "de 2 a 20 letras"),
        ("PEÑA", "de 2 a 20 letras"),
        ("JOSÉ", "de 2 a 20 letras"),
        ("J", "de 2 a 20 letras"),
        ("UNCODIGOMUYLARGODEMAS", "de 2 a 20 letras"),
    ):
        r = await _cambiar(cliente, semilla["vendedor"], codigo)
        assert motivo in r.text, codigo
    r = await _cambiar(cliente, semilla["vendedor"], "vend01")
    assert "VEND01: sin cambios." in r.text
    await sesion.rollback()
    assert await _codigo(sesion, semilla["vendedor"]) == "VEND01"
    assert await _apartados(sesion) == []
    # Al dar de alta, la misma regla.
    r = await _crear_usuario(cliente, codigo="JUAN-P")
    assert "de 2 a 20 letras" in r.text
