"""Mover mercancía de la bodega al camión, y de regreso, desde el panel (ADR 0002 §135).

«Quiero en el dashboard poder mover mercancía a los camiones y viceversa.»

1. **De la bodega al camión**, en cajas o piezas: baja la bodega, sube el
   camión, y al teléfono del vendedor le llega la diferencia, de tipo
   `traspaso`, aunque el camión no trajera ese artículo.
2. **Del camión a la bodega**, igual al revés; la bodega no avisa a nadie.
3. **Lo que no se deja**: más de lo que hay (y entonces no se mueve nada, ni
   lo que sí alcanzaba), nada escrito, un dedazo. La pantalla regresa con lo
   que se escribió.
4. **Se anula por pareja** en Operaciones: la lista muestra una sola, y
   anularla regresa las dos mitades.
5. **Solo con permiso** de ver y de ajustar el inventario, y es una pestaña de
   Inventario.
6. **De un camión a otro** (§187), solo en el dashboard: a cada teléfono le
   llega su mitad; nunca al mismo camión ni más de lo que hay.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel
from tests.test_operaciones import _anular, _existencia
from tests.test_panel_ajuste_camion import _entrar

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def articulos(sesion, semilla) -> dict:
    """Sopa en caja de 24: 100 en la bodega y 30 en el camión. Refresco solo en
    la bodega, 48."""
    sopa, refresco = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) VALUES "
            "(:s, 'SOPA-70G', 'Sopa de fideo 70 g', 'PZA', 0), "
            "(:r, 'COCA600', 'Coca 600 ml', 'PZA', 0)"
        ),
        {"s": sopa, "r": refresco},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:s, 'PZA', 1, true), (:s, 'CAJA', 24, false), (:r, 'PZA', 1, true)"
        ),
        {"s": sopa, "r": refresco},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:b, :s, 100), (:c, :s, 30), (:b, :r, 48)"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "s": sopa, "r": refresco},
    )
    await sesion.commit()
    return {"sopa": sopa, "refresco": refresco}


async def _mover(cliente, semilla, *, sentido="al_camion", nota="", **renglones):
    pantalla = await cliente.get(
        "/panel/inventario/mover",
        params={"camion": str(semilla["camion"]), "bodega": str(semilla["bodega"]),
                "sentido": sentido},
    )
    assert pantalla.status_code == 200, pantalla.text
    datos = {
        "csrf": csrf_del_panel(cliente, pantalla),
        "camion": str(semilla["camion"]),
        "bodega": str(semilla["bodega"]),
        "sentido": sentido,
        "nota": nota,
        **renglones,
    }
    return await cliente.post("/panel/inventario/mover", data=datos, follow_redirects=True)


async def _deltas_al_telefono(sesion, vendedor) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text("SELECT payload FROM change_log "
                     " WHERE entidad = 'ajuste_camion' AND vendedor_id = :v ORDER BY cursor"),
                {"v": vendedor},
            )
        ).scalars()
    ]


async def test_de_la_bodega_al_camion_en_cajas_y_piezas(cliente, sesion, semilla, articulos):
    await _entrar(cliente)
    sopa, refresco = articulos["sopa"], articulos["refresco"]
    r = await _mover(
        cliente, semilla, nota="le faltó a media ruta",
        **{f"cantidad_{sopa}": "2", f"unidad_{sopa}": "CAJA",
           f"cantidad_{refresco}": "12", f"unidad_{refresco}": "PZA"},
    )
    assert r.status_code == 200
    assert "Se movieron 2 artículo(s), 60 piezas, de Bodega a Camión 01" in r.text
    assert "Juan Pérez" in r.text

    assert await _existencia(sesion, semilla["bodega"], sopa) == 52
    assert await _existencia(sesion, semilla["camion"], sopa) == 78
    assert await _existencia(sesion, semilla["bodega"], refresco) == 36
    # El camión no traía refresco: ahora sí.
    assert await _existencia(sesion, semilla["camion"], refresco) == 12

    # Al teléfono, solo lo del camión, como diferencia y de tipo traspaso.
    deltas = await _deltas_al_telefono(sesion, semilla["vendedor"])
    assert sorted((d["producto_id"], d["delta"]) for d in deltas) == sorted(
        [(str(sopa), "48.000"), (str(refresco), "12.000")]
    )
    assert {d["tipo"] for d in deltas} == {"traspaso"}
    assert all("De Bodega a Camión 01" in d["nota"] and "le faltó a media ruta" in d["nota"]
               for d in deltas)
    nulos = (
        await sesion.execute(
            text("SELECT count(*) FROM change_log "
                 " WHERE entidad = 'ajuste_camion' AND vendedor_id IS NULL")
        )
    ).scalar_one()
    assert nulos == 0

    # Las dos mitades, atadas, y el libro mayor cuadra con la caché.
    mitades = (
        await sesion.execute(
            text("SELECT a.tipo AS almacen, j.delta, j.pareja_id IS NOT NULL AS entra "
                 "  FROM ajustes_camion j JOIN almacenes a ON a.id = j.almacen_id "
                 " WHERE j.producto_id = :s ORDER BY j.folio"),
            {"s": sopa},
        )
    ).mappings().all()
    assert [(m["almacen"], m["delta"], m["entra"]) for m in mitades] == [
        ("bodega", Decimal(-48), False), ("camion", Decimal(48), True)
    ]
    # Y queda en «Lo último que se movió».
    pagina = await cliente.get("/panel/inventario/mover")
    assert "Lo último que se movió" in pagina.text and "Sopa de fideo 70 g" in pagina.text


async def test_del_camion_a_la_bodega(cliente, sesion, semilla, articulos):
    await _entrar(cliente)
    sopa = articulos["sopa"]
    pantalla = await cliente.get(
        "/panel/inventario/mover",
        params={"camion": str(semilla["camion"]), "bodega": str(semilla["bodega"]),
                "sentido": "a_bodega"},
    )
    # Lo que hay en el camión: solo la sopa, y en cajas y piezas.
    assert "Hay en Camión 01" in pantalla.text
    assert "1 CAJA + 6 PZA" in pantalla.text
    assert "Coca 600 ml" not in pantalla.text

    r = await _mover(cliente, semilla, sentido="a_bodega",
                     **{f"cantidad_{sopa}": "10", f"unidad_{sopa}": "PZA"})
    assert "de Camión 01 a Bodega" in r.text
    assert await _existencia(sesion, semilla["camion"], sopa) == 20
    assert await _existencia(sesion, semilla["bodega"], sopa) == 110
    deltas = await _deltas_al_telefono(sesion, semilla["vendedor"])
    assert [(d["delta"], d["tipo"]) for d in deltas] == [("-10.000", "traspaso")]


async def test_lo_que_no_se_deja_no_mueve_nada(cliente, sesion, semilla, articulos):
    await _entrar(cliente)
    sopa, refresco = articulos["sopa"], articulos["refresco"]

    # Más de lo que hay en un renglón: no se mueve ni el que sí alcanzaba, y la
    # pantalla regresa con lo escrito.
    r = await _mover(
        cliente, semilla,
        **{f"cantidad_{sopa}": "5", f"unidad_{sopa}": "CAJA",
           f"cantidad_{refresco}": "12", f"unidad_{refresco}": "PZA"},
    )
    assert "No alcanza en Bodega: Sopa de fideo 70 g (hay 100, mueves 120)" in r.text
    assert "No se movió nada" in r.text
    assert f'name="cantidad_{sopa}" inputmode="numeric"\n                 value="5"' in r.text
    assert await _existencia(sesion, semilla["bodega"], refresco) == 48
    assert await _existencia(sesion, semilla["camion"], refresco) == 0

    # Un dedazo, o nada escrito.
    r = await _mover(cliente, semilla, **{f"cantidad_{sopa}": "1.5", f"unidad_{sopa}": "CAJA"})
    assert "tiene que ser un número entero" in r.text
    r = await _mover(cliente, semilla)
    assert "Escribe cuánto mueves de al menos un artículo" in r.text

    await sesion.rollback()
    assert await _existencia(sesion, semilla["bodega"], sopa) == 100
    assert (await sesion.execute(text("SELECT count(*) FROM ajustes_camion"))).scalar_one() == 0


async def test_se_anula_por_pareja_en_operaciones(cliente, sesion, semilla, articulos):
    await _entrar(cliente)
    sopa = articulos["sopa"]
    await _mover(cliente, semilla, **{f"cantidad_{sopa}": "1", f"unidad_{sopa}": "CAJA"})
    await sesion.rollback()

    lista = await cliente.get("/panel/operaciones", params={"tipo": "ajuste"})
    sale, entra = (
        await sesion.execute(
            text("SELECT folio FROM ajustes_camion ORDER BY pareja_id NULLS FIRST")
        )
    ).scalars().all()
    assert sale in lista.text and entra not in lista.text

    ref = (
        await sesion.execute(text("SELECT id FROM ajustes_camion WHERE folio = :f"), {"f": sale})
    ).scalar_one()
    revision = await cliente.get(f"/panel/operaciones/ajuste/{ref}")
    assert "regresa de Camión 01 a Bodega" in revision.text
    r = await _anular(cliente, "ajuste", ref)
    assert r.status_code == 200
    await sesion.rollback()
    assert await _existencia(sesion, semilla["bodega"], sopa) == 100
    assert await _existencia(sesion, semilla["camion"], sopa) == 30
    # Al teléfono le llega la contraria del camión; a nadie la de la bodega.
    deltas = await _deltas_al_telefono(sesion, semilla["vendedor"])
    assert [(d["delta"], d["tipo"]) for d in deltas] == [
        ("24.000", "traspaso"), ("-24.000", "anulacion")
    ]


async def test_solo_con_permiso_y_es_pestana_de_inventario(cliente, sesion, semilla, articulos):
    from tests.test_panel_arranque import _usuario_de_oficina

    await _entrar(cliente)
    inventario = await cliente.get("/panel/inventario")
    assert '<a href="/panel/inventario/mover"' in inventario.text
    assert "Mover mercancía" in inventario.text

    usuario = await _usuario_de_oficina(sesion, "OFI01", "supervisor")
    await sesion.execute(
        text("INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
             "VALUES (:u, 'inventario.ajustar', false)"),
        {"u": usuario},
    )
    await sesion.commit()
    cliente.cookies.clear()
    await _entrar(cliente, "OFI01")
    assert (await cliente.get("/panel/inventario/mover")).status_code == 403
    # Ni la pestaña ni el enlace: un enlace que responde 403 es un callejón.
    pantalla = await cliente.get("/panel/inventario")
    assert pantalla.status_code == 200
    assert "/panel/inventario/mover" not in pantalla.text


async def _otro_camion(sesion, semilla) -> dict:
    """Pedro Gómez, con su Camión 02, vacío."""
    vendedor, camion = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'VEND02', 'Pedro Gómez', 'x', 'vendedor', now(), now())"
        ),
        {"u": vendedor, "s": semilla["sucursal"]},
    )
    await sesion.execute(
        text("INSERT INTO almacenes (id, codigo, nombre, tipo, responsable_id) "
             "VALUES (:a, 'CAMION_02', 'Camión 02', 'camion', :u)"),
        {"a": camion, "u": vendedor},
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"), {"a": camion, "u": vendedor}
    )
    await sesion.commit()
    return {"vendedor": vendedor, "camion": camion}


async def test_de_un_camion_a_otro_solo_en_el_dashboard(cliente, sesion, semilla, articulos):
    """«También quiero que me permita mover mercancía entre camiones de ellos, solo
    en el dashboard» (§187). Una caja de sopa de Juan a Pedro: a cada teléfono le
    llega su mitad."""
    otro = await _otro_camion(sesion, semilla)
    await _entrar(cliente)
    sopa = articulos["sopa"]

    # Sin elegir a qué camión, no hay nada que mover todavía.
    sin_destino = await cliente.get(
        "/panel/inventario/mover",
        params={"camion": str(semilla["camion"]), "sentido": "entre_camiones"},
    )
    assert 'id="elige_destino"' in sin_destino.text
    assert 'id="formulario_mover"' not in sin_destino.text
    # Y el camión de origen no sale para elegir como destino.
    assert "A Camión 01" not in sin_destino.text and "A Camión 02" in sin_destino.text

    pantalla = await cliente.get(
        "/panel/inventario/mover",
        params={"camion": str(semilla["camion"]), "sentido": "entre_camiones",
                "camion_destino": str(otro["camion"])},
    )
    assert "de Camión 01 a Camión 02" in pantalla.text
    assert "Hay en Camión 01" in pantalla.text
    r = await cliente.post(
        "/panel/inventario/mover",
        data={"csrf": csrf_del_panel(cliente, pantalla), "camion": str(semilla["camion"]),
              "camion_destino": str(otro["camion"]), "sentido": "entre_camiones",
              "bodega": "", "nota": "", f"cantidad_{sopa}": "1", f"unidad_{sopa}": "CAJA"},
        follow_redirects=True,
    )
    assert "Se movieron 1 artículo(s), 24 piezas, de Camión 01 a Camión 02" in r.text
    assert "A los teléfonos de Juan Pérez y Pedro Gómez" in r.text
    assert await _existencia(sesion, semilla["camion"], sopa) == 6
    assert await _existencia(sesion, otro["camion"], sopa) == 24
    assert await _existencia(sesion, semilla["bodega"], sopa) == 100
    assert [(d["delta"], d["tipo"]) for d in
            await _deltas_al_telefono(sesion, semilla["vendedor"])] == [("-24.000", "traspaso")]
    assert [(d["delta"], d["tipo"]) for d in
            await _deltas_al_telefono(sesion, otro["vendedor"])] == [("24.000", "traspaso")]


async def test_entre_camiones_no_se_deja_mas_de_lo_que_hay_ni_el_mismo(
    cliente, sesion, semilla, articulos
):
    otro = await _otro_camion(sesion, semilla)
    await _entrar(cliente)
    sopa = articulos["sopa"]
    pantalla = await cliente.get(
        "/panel/inventario/mover",
        params={"camion": str(semilla["camion"]), "sentido": "entre_camiones",
                "camion_destino": str(otro["camion"])},
    )
    datos = {"csrf": csrf_del_panel(cliente, pantalla), "camion": str(semilla["camion"]),
             "sentido": "entre_camiones", "bodega": "", "nota": "",
             f"cantidad_{sopa}": "2", f"unidad_{sopa}": "CAJA"}
    mucho = await cliente.post("/panel/inventario/mover",
                               data={**datos, "camion_destino": str(otro["camion"])})
    assert "No alcanza en Camión 01" in mucho.text
    mismo = await cliente.post("/panel/inventario/mover",
                               data={**datos, "camion_destino": str(semilla["camion"])})
    assert "Elige dos camiones distintos" in mismo.text
    assert await _existencia(sesion, semilla["camion"], sopa) == 30
    assert await _existencia(sesion, otro["camion"], sopa) == 0
