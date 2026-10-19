"""Ajustar el inventario de una bodega o un camión contando, de un jalón (ADR 0002 §142).

«Desde el dashboard quiero poder modificar el inventario de los camiones y el
almacén de forma arbitraria, para hacer ajustes estos días que apenas empezaré
la operación.»

1. **Se escribe lo que hay**, en cajas y piezas, de los artículos que se
   contaron —todo el catálogo a la vista, aunque no tenga existencia— y cada uno
   queda en lo contado, con un ajuste de conteo por artículo. Lo que cuadra y lo
   que se deja en blanco no se tocan.
2. **El del camión le llega al teléfono** como diferencia; y si su teléfono tiene
   operaciones sin subir, la pantalla lo avisa.
3. **Lo que no se deja** no guarda nada y regresa con lo escrito: sin nota, un
   dedazo, nada escrito, todo cuadra.
4. **Solo con permiso** de ver y ajustar el inventario; es una pestaña de
   Inventario.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel
from tests.test_mover_mercancia import _deltas_al_telefono, articulos  # noqa: F401
from tests.test_operaciones import _existencia
from tests.test_panel_ajuste_camion import _entrar

pytestmark = pytest.mark.asyncio


async def _contar(cliente, almacen, *, nota="Conteo para arrancar la operación", **renglones):
    pantalla = await cliente.get("/panel/inventario/ajustar", params={"almacen": str(almacen)})
    assert pantalla.status_code == 200, pantalla.text
    datos = {"csrf": csrf_del_panel(cliente, pantalla), "almacen": str(almacen), "nota": nota,
             **renglones}
    return await cliente.post("/panel/inventario/ajustar", data=datos, follow_redirects=True)


async def test_la_bodega_queda_en_lo_contado(cliente, sesion, semilla, articulos):  # noqa: F811
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    chicle = (await sesion.execute(text(
        "INSERT INTO productos (sku, nombre, unidad_base, tasa_iva) "
        "VALUES ('CHICLE', 'Chicle menta', 'PZA', 0) RETURNING id"))).scalar_one()
    await sesion.commit()
    await _entrar(cliente)

    pagina = await cliente.get("/panel/inventario/ajustar")
    # Abre en la bodega, con todo el catálogo: también lo que no tiene existencia.
    for sku in ("SOPA-70G", "COCA600", "CHICLE"):
        assert f'id="contar_{sku}"' in pagina.text, sku
    assert "4 CAJA + 4 PZA" in pagina.text  # las 100 sopas, en caja de 24
    assert f'name="cajas_{sopa}"' in pagina.text
    assert f'name="cajas_{chicle}"' not in pagina.text  # sin caja: solo piezas

    r = await _contar(
        cliente, semilla["bodega"],
        **{f"cajas_{sopa}": "3", f"piezas_{sopa}": "5",   # 77: faltan 23
           f"piezas_{refresco}": "48",                     # cuadra
           f"piezas_{chicle}": "200"},                     # no había
    )
    assert "Se ajustaron 2 artículo(s) en Bodega: 200 piezas de más y 23 de menos" in r.text
    assert "1 ya cuadraban y no se tocaron" in r.text
    assert await _existencia(sesion, semilla["bodega"], sopa) == 77
    assert await _existencia(sesion, semilla["bodega"], refresco) == 48
    assert await _existencia(sesion, semilla["bodega"], chicle) == 200
    ajustes = (
        await sesion.execute(text(
            "SELECT p.sku, j.tipo, j.delta, j.contado, j.nota FROM ajustes_camion j "
            "  JOIN productos p ON p.id = j.producto_id ORDER BY p.sku"))
    ).all()
    assert [(a.sku, a.tipo, a.delta, a.contado) for a in ajustes] == [
        ("CHICLE", "conteo", Decimal(200), Decimal(200)),
        ("SOPA-70G", "conteo", Decimal(-23), Decimal(77)),
    ]
    assert {a.nota for a in ajustes} == {"Conteo para arrancar la operación"}
    # La bodega no avisa a ningún teléfono.
    assert await _deltas_al_telefono(sesion, semilla["vendedor"]) == []
    # Y queda en «Lo último que se ajustó».
    pagina = await cliente.get("/panel/inventario/ajustar")
    assert "Lo último que se ajustó en Bodega" in pagina.text


async def test_el_del_camion_le_llega_al_telefono(cliente, sesion, semilla, articulos):  # noqa: F811
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    await _entrar(cliente)
    pagina = await cliente.get(
        "/panel/inventario/ajustar", params={"almacen": str(semilla["camion"])}
    )
    assert "le llega al teléfono de Juan Pérez" in pagina.text

    r = await _contar(cliente, semilla["camion"],
                      **{f"cajas_{sopa}": "1", f"piezas_{refresco}": "6"})
    assert "Al teléfono de Juan Pérez le llega al sincronizar" in r.text
    assert await _existencia(sesion, semilla["camion"], sopa) == 24
    assert await _existencia(sesion, semilla["camion"], refresco) == 6
    deltas = await _deltas_al_telefono(sesion, semilla["vendedor"])
    assert sorted((d["producto_id"], d["delta"], d["tipo"]) for d in deltas) == sorted(
        [(str(sopa), "-6.000", "conteo"), (str(refresco), "6.000", "conteo")]
    )


async def test_con_el_telefono_atrasado_lo_avisa(cliente, sesion, semilla, articulos):  # noqa: F811
    await sesion.execute(text(
        "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, cola_pendiente) "
        "VALUES (gen_random_uuid(), :v, 'Moto G54', 'activo', 3)"), {"v": semilla["vendedor"]})
    await sesion.commit()
    await _entrar(cliente)
    pagina = await cliente.get(
        "/panel/inventario/ajustar", params={"almacen": str(semilla["camion"])}
    )
    assert "reportó 3" in pagina.text and "que sincronice antes de contar" in pagina.text


async def test_lo_que_no_se_deja_no_guarda_nada(cliente, sesion, semilla, articulos):  # noqa: F811
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    await _entrar(cliente)

    r = await _contar(cliente, semilla["bodega"], nota="corto", **{f"piezas_{sopa}": "10"})
    assert "Escribe la nota del conteo" in r.text
    # Regresa con lo escrito, para no capturarlo otra vez.
    assert f'name="piezas_{sopa}" inputmode="numeric"\n                 value="10"' in r.text

    r = await _contar(cliente, semilla["bodega"],
                      **{f"piezas_{sopa}": "10", f"cajas_{refresco}": "1.5"})
    assert "No se guardó nada" in r.text and "tiene que ser un número entero" in r.text
    r = await _contar(cliente, semilla["bodega"], **{f"cajas_{refresco}": "2"})
    assert "no tiene caja" in r.text
    r = await _contar(cliente, semilla["bodega"])
    assert "Escribe lo que contaste de al menos un artículo" in r.text
    r = await _contar(cliente, semilla["bodega"], **{f"cajas_{sopa}": "4", f"piezas_{sopa}": "4"})
    assert "ya cuadra con el sistema" in r.text

    await sesion.rollback()
    assert await _existencia(sesion, semilla["bodega"], sopa) == 100
    assert (await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))).scalar_one() == 0


async def test_solo_con_permiso_y_es_pestana_de_inventario(cliente, sesion, semilla, articulos):  # noqa: F811
    from tests.test_panel_arranque import _usuario_de_oficina

    await _entrar(cliente)
    inventario = await cliente.get("/panel/inventario")
    assert '<a href="/panel/inventario/ajustar"' in inventario.text

    usuario = await _usuario_de_oficina(sesion, "OFI01", "supervisor")
    await sesion.execute(
        text("INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
             "VALUES (:u, 'inventario.ajustar', false)"),
        {"u": usuario},
    )
    await sesion.commit()
    cliente.cookies.clear()
    await _entrar(cliente, "OFI01")
    assert (await cliente.get("/panel/inventario/ajustar")).status_code == 403
    assert "/panel/inventario/ajustar" not in (await cliente.get("/panel/inventario")).text
