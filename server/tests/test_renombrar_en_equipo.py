"""Renombrar rutas, bodegas, camiones y listas desde la lista (ADR 0002 §94).

Pedido de la dirección (octubre 2026): «quiero poder editar el nombre de las
rutas, camiones, almacenes y eso». En el panel, no en la app. El nombre se
cambia en la misma tabla de Usuarios y rutas; el código no, que es la llave.
Lo desactivado va plegado al final de su tabla (§93).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel as _csrf
from tests.conftest import solo_texto
from tests.test_panel_editar_estructura import _entrar

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("tipo", "tabla", "clave"),
    [("rutas", "rutas", "ruta"), ("almacenes", "almacenes", "camion"),
     ("almacenes", "almacenes", "bodega"), ("listas", "listas_precios", None)],
)
async def test_se_renombra_desde_la_lista(cliente, sesion, semilla, tipo, tabla, clave):
    await _entrar(cliente)
    if clave is None:
        id_, codigo = (
            await sesion.execute(text("SELECT id, codigo FROM listas_precios LIMIT 1"))
        ).one()
    else:
        id_ = semilla[clave]
        codigo = (
            await sesion.execute(text(f"SELECT codigo FROM {tabla} WHERE id = :i"), {"i": id_})
        ).scalar_one()
    antes = (
        await sesion.execute(text(f"SELECT nombre FROM {tabla} WHERE id = :i"), {"i": id_})
    ).scalar_one()
    try:
        await _renombrar_y_comprobar(cliente, sesion, tipo, tabla, id_, codigo)
    finally:
        # La lista de precios la siembra la migración y la comparten todas las
        # pruebas (el contrato de deltas la lee): se deja como estaba.
        await sesion.rollback()
        await sesion.execute(
            text(f"UPDATE {tabla} SET nombre = :n WHERE id = :i"), {"n": antes, "i": id_}
        )
        await sesion.commit()


async def _renombrar_y_comprobar(cliente, sesion, tipo, tabla, id_, codigo) -> None:
    r = await cliente.get("/panel/equipo")
    assert f'id="renombrar_{tipo}_{codigo}"' in r.text

    r = await cliente.post(
        f"/panel/equipo/{tipo}/{id_}/nombre",
        data={"csrf": _csrf(cliente, r), "nombre": "  Ruta   del  Norte "},
        follow_redirects=True,
    )
    assert f"{codigo}: ahora se llama «Ruta del Norte»." in solo_texto(r)
    nombre, codigo_despues = (
        await sesion.execute(text(f"SELECT nombre, codigo FROM {tabla} WHERE id = :i"),
                             {"i": id_})
    ).one()
    assert (nombre, codigo_despues) == ("Ruta del Norte", codigo)
    accion = (
        await sesion.execute(
            text("SELECT accion FROM auditoria WHERE entidad_id = :i ORDER BY ocurrido_en DESC"),
            {"i": id_},
        )
    ).scalars().first()
    assert accion == "renombrar"


async def test_sin_nombre_no_se_cambia_nada(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/equipo")
    r = await cliente.post(
        f"/panel/equipo/rutas/{semilla['ruta']}/nombre",
        data={"csrf": _csrf(cliente, r), "nombre": "   "},
        follow_redirects=True,
    )
    assert "La ruta necesita un nombre." in solo_texto(r)


async def test_lo_desactivado_va_plegado_al_final(cliente, sesion, semilla):
    await sesion.execute(
        text("UPDATE almacenes SET activo = false WHERE id = :a"), {"a": semilla["bodega"]}
    )
    await sesion.commit()
    await _entrar(cliente)
    html = (await cliente.get("/panel/equipo")).text
    codigo = (
        await sesion.execute(
            text("SELECT codigo FROM almacenes WHERE id = :a"), {"a": semilla["bodega"]}
        )
    ).scalar_one()
    plegadas = html.split('id="almacenes_desactivadas"')[1].split("</details>")[0]
    assert codigo in plegadas
    assert f'id="renombrar_almacenes_{codigo}"' not in html
