"""Editar y eliminar la estructura: usuarios, rutas, almacenes, listas, proveedores
y motivos (octubre 2026).

La regla es la de clientes y productos (ADR 0002 §52): eliminar funciona siempre
y decide qué es seguro —se borra si nada lo usa, se da de baja si tiene
historia, se niega si dejaría algo roto en la calle—. Lo que estas pruebas
defienden es justo esa tercera rama, que es donde se rompería la operación:

· una ruta con clientes no se borra (se quedarían fuera de todos los teléfonos);
· un almacén con mercancía no se borra (desaparecería del inventario);
· un camión con mercancía no cambia de responsable (su teléfono la vería en cero);
· la lista por omisión no se borra (los clientes sin lista cotizan con ella);
· el último administrador no se quita a sí mismo.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, solo_texto
from tests.test_panel_cobranza import _csrf

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _referencia_intacta(sesion):
    """Las listas de precios y los motivos son datos de REFERENCIA: el conftest no
    los vacía entre pruebas (los siembra una migración). Estas pruebas los editan,
    así que los dejan como estaban: un CADUCADO que se quedara «se le cobra» haría
    fallar la cuenta del vendedor en otro archivo, por una razón que no estaría ahí.
    """
    yield
    await sesion.rollback()
    await sesion.execute(
        text(
            "UPDATE clientes SET lista_precios_id = NULL WHERE lista_precios_id IN "
            "(SELECT id FROM listas_precios WHERE codigo <> 'GENERAL')"
        )
    )
    await sesion.execute(text("UPDATE listas_precios SET es_default = false WHERE es_default"))
    await sesion.execute(
        text("UPDATE listas_precios SET es_default = true, activo = true WHERE codigo = 'GENERAL'")
    )
    await sesion.execute(text("DELETE FROM listas_precios WHERE codigo <> 'GENERAL'"))
    await sesion.execute(
        text("UPDATE motivos_merma SET afecta_vendedor = false WHERE codigo = 'CADUCADO'")
    )
    await sesion.execute(text("DELETE FROM motivos_no_drop WHERE codigo = 'SIN_ESPACIO'"))
    await sesion.commit()


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


async def _post(cliente, ficha: str, accion: str, **datos):
    pagina = await cliente.get(ficha)
    return await cliente.post(
        f"{ficha}/{accion}" if accion else ficha,
        data={"csrf": _csrf(cliente, pagina), **datos},
        follow_redirects=True,
    )


async def _usuario(sesion, codigo: str, rol: str = "vendedor") -> uuid.UUID:
    from app.core.seguridad import hashear_password

    nuevo = uuid.uuid4()
    sucursal = (await sesion.execute(text("SELECT id FROM sucursales LIMIT 1"))).scalar_one()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, rol_codigo, "
            "                      creado_en, actualizado_en) "
            "VALUES (:id, :s, :c, :c, :h, :r, now(), now())"
        ),
        {
            "id": nuevo,
            "s": sucursal,
            "c": codigo,
            "h": hashear_password(PASSWORD_VENDEDOR),
            "r": rol,
        },
    )
    await sesion.commit()
    return nuevo


async def _cliente_en(sesion, ruta_id, codigo="CLI-1") -> uuid.UUID:
    nuevo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
            "                      creado_en, actualizado_en) "
            "VALUES (:id, :c, :c, :r, now(), now())"
        ),
        {"id": nuevo, "c": codigo, "r": ruta_id},
    )
    await sesion.commit()
    return nuevo


async def _uno(sesion, sql, **p):
    return (await sesion.execute(text(sql), p)).mappings().first()


# ===========================================================================
# Usuarios
# ===========================================================================
async def test_editar_un_usuario_y_su_codigo_no_cambia(cliente, sesion, semilla):
    await _entrar(cliente)
    ficha = f"/panel/equipo/usuarios/{semilla['vendedor']}"
    r = await _post(
        cliente,
        ficha,
        "datos",
        nombre="Juan Pérez López",
        rol_codigo="vendedor",
        dias_max_offline="5",
    )
    assert "Datos guardados" in solo_texto(r)
    fila = await _uno(
        sesion,
        "SELECT codigo, nombre, dias_max_offline FROM usuarios WHERE id = :u",
        u=semilla["vendedor"],
    )
    assert fila["nombre"] == "Juan Pérez López"
    assert fila["dias_max_offline"] == 5
    assert fila["codigo"] == "VEND01"


async def test_no_te_quitas_el_rol_de_admin_a_ti_mismo(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await _post(
        cliente,
        f"/panel/equipo/usuarios/{semilla['admin']}",
        "datos",
        nombre="Bryan",
        rol_codigo="gerente",
        dias_max_offline="7",
    )
    assert "No puedes quitarte el rol de administrador" in solo_texto(r)
    rol = await _uno(sesion, "SELECT rol_codigo FROM usuarios WHERE id = :u", u=semilla["admin"])
    assert rol["rol_codigo"] == "admin"


async def test_un_usuario_sin_historia_se_borra(cliente, sesion, semilla):
    nuevo = await _usuario(sesion, "SUP09", "supervisor")
    await _entrar(cliente)
    r = await _post(cliente, f"/panel/equipo/usuarios/{nuevo}", "eliminar", confirmo="1")
    assert "se eliminó" in solo_texto(r)
    assert await _uno(sesion, "SELECT 1 FROM usuarios WHERE id = :u", u=nuevo) is None


async def test_un_usuario_con_historia_se_desactiva(cliente, sesion, semilla):
    """El vendedor de la semilla es titular de una ruta y responsable de un camión."""
    await _entrar(cliente)
    r = await _post(
        cliente, f"/panel/equipo/usuarios/{semilla['vendedor']}", "eliminar", confirmo="1"
    )
    assert "se desactivó" in solo_texto(r)
    fila = await _uno(sesion, "SELECT activo FROM usuarios WHERE id = :u", u=semilla["vendedor"])
    assert fila["activo"] is False


async def test_eliminar_pide_confirmar_y_no_a_uno_mismo(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await _post(cliente, f"/panel/equipo/usuarios/{semilla['vendedor']}", "eliminar")
    assert "Marca la casilla" in solo_texto(r)
    r = await _post(cliente, f"/panel/equipo/usuarios/{semilla['admin']}", "eliminar", confirmo="1")
    assert "No puedes eliminarte a ti mismo" in solo_texto(r)


# ===========================================================================
# Rutas
# ===========================================================================
async def test_editar_una_ruta(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await _post(
        cliente,
        f"/panel/equipo/rutas/{semilla['ruta']}",
        "datos",
        codigo="r 4n",
        nombre="Ruta 4 Norte",
        activo="1",
    )
    assert "Ruta R4N guardada" in solo_texto(r)
    fila = await _uno(sesion, "SELECT codigo, nombre FROM rutas WHERE id = :r", r=semilla["ruta"])
    assert (fila["codigo"], fila["nombre"]) == ("R4N", "Ruta 4 Norte")


async def test_una_ruta_con_clientes_no_se_desactiva_ni_se_borra(cliente, sesion, semilla):
    await _cliente_en(sesion, semilla["ruta"])
    await _entrar(cliente)
    ficha = f"/panel/equipo/rutas/{semilla['ruta']}"
    r = await _post(cliente, ficha, "datos", codigo="R04", nombre="Ruta 4")  # sin «activo»
    assert "Muévelos a otra ruta" in solo_texto(r)
    r = await _post(cliente, ficha, "eliminar", confirmo="1")
    assert "muévelos a otra ruta primero" in solo_texto(r)
    fila = await _uno(sesion, "SELECT activo FROM rutas WHERE id = :r", r=semilla["ruta"])
    assert fila["activo"] is True


async def test_mover_los_clientes_avisa_al_telefono_viejo(cliente, sesion, semilla):
    """El disparador de clientes publica la BAJA a la ruta vieja (migración 0034)."""
    cliente_id = await _cliente_en(sesion, semilla["ruta"])
    otra = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R05', 'Ruta 5')"), {"r": otra}
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await _post(
        cliente, f"/panel/equipo/rutas/{semilla['ruta']}", "mover-clientes", destino_id=str(otra)
    )
    assert "1 cliente(s) pasaron a R05" in solo_texto(r)
    fila = await _uno(sesion, "SELECT ruta_id FROM clientes WHERE id = :c", c=cliente_id)
    assert fila["ruta_id"] == otra
    baja = await _uno(
        sesion,
        "SELECT operacion FROM change_log WHERE entidad = 'cliente' AND entidad_id = :c "
        "   AND ruta_id = :r ORDER BY cursor DESC LIMIT 1",
        c=cliente_id,
        r=semilla["ruta"],
    )
    assert baja["operacion"] == "delete"


async def test_una_ruta_nueva_sin_nada_se_borra(cliente, sesion, semilla):
    nueva = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R99', 'Prueba')"), {"r": nueva}
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await _post(cliente, f"/panel/equipo/rutas/{nueva}", "eliminar", confirmo="1")
    assert "Ruta R99 eliminada" in solo_texto(r)
    assert await _uno(sesion, "SELECT 1 FROM rutas WHERE id = :r", r=nueva) is None


async def test_una_ruta_con_historia_se_da_de_baja_y_pierde_su_alcance(cliente, sesion, semilla):
    """Un cliente que ya se dio de baja dejó su rastro en el change_log de la ruta."""
    cliente_id = await _cliente_en(sesion, semilla["ruta"])
    await sesion.execute(
        text("UPDATE clientes SET estatus = 'baja' WHERE id = :c"), {"c": cliente_id}
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await _post(cliente, f"/panel/equipo/rutas/{semilla['ruta']}", "eliminar", confirmo="1")
    assert "se dio de baja" in solo_texto(r)
    fila = await _uno(
        sesion, "SELECT activo, vendedor_id FROM rutas WHERE id = :r", r=semilla["ruta"]
    )
    assert fila["activo"] is False and fila["vendedor_id"] is None
    alcance = await _uno(
        sesion, "SELECT count(*) AS n FROM usuarios_rutas WHERE ruta_id = :r", r=semilla["ruta"]
    )
    assert alcance["n"] == 0


# ===========================================================================
# Almacenes
# ===========================================================================
async def _existencia(sesion, almacen, cantidad) -> uuid.UUID:
    producto = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO productos (id, sku, nombre, unidad_base) VALUES (:p, :s, 'Atún', 'PZA')"),
        {"p": producto, "s": f"SKU-{str(producto)[:6]}"},
    )
    await sesion.execute(
        text("INSERT INTO existencias (almacen_id, producto_id, cantidad) VALUES (:a, :p, :c)"),
        {"a": almacen, "p": producto, "c": Decimal(cantidad)},
    )
    await sesion.commit()
    return producto


async def test_un_almacen_con_mercancia_no_se_borra(cliente, sesion, semilla):
    await _existencia(sesion, semilla["bodega"], "24")
    await _entrar(cliente)
    r = await _post(
        cliente, f"/panel/equipo/almacenes/{semilla['bodega']}", "eliminar", confirmo="1"
    )
    assert "Tiene mercancía" in solo_texto(r)
    assert (
        await _uno(sesion, "SELECT 1 FROM almacenes WHERE id = :a AND activo", a=semilla["bodega"])
        is not None
    )


async def test_un_almacen_vacio_y_sin_movimientos_se_borra(cliente, sesion, semilla):
    nuevo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo) VALUES (:a, 'BOD_X', 'X', 'bodega')"
        ),
        {"a": nuevo},
    )
    await sesion.commit()
    await _existencia(sesion, nuevo, "0")  # una caché en cero no es historia
    await _entrar(cliente)
    r = await _post(cliente, f"/panel/equipo/almacenes/{nuevo}", "eliminar", confirmo="1")
    assert "BOD_X eliminado" in solo_texto(r)
    assert await _uno(sesion, "SELECT 1 FROM almacenes WHERE id = :a", a=nuevo) is None


async def test_un_almacen_con_movimientos_se_da_de_baja(cliente, sesion, semilla):
    producto = await _existencia(sesion, semilla["bodega"], "0")
    await sesion.execute(
        text(
            "INSERT INTO movimientos_inventario (tipo, almacen_destino_id, producto_id, cantidad, "
            "                                    documento_tipo, documento_id) "
            "VALUES ('compra', :a, :p, 10, 'entrada', :d)"
        ),
        {"a": semilla["bodega"], "p": producto, "d": uuid.uuid4()},
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await _post(
        cliente, f"/panel/equipo/almacenes/{semilla['bodega']}", "eliminar", confirmo="1"
    )
    assert "se dio de baja" in solo_texto(r)
    fila = await _uno(sesion, "SELECT activo FROM almacenes WHERE id = :a", a=semilla["bodega"])
    assert fila["activo"] is False


async def test_un_camion_con_mercancia_no_cambia_de_responsable(cliente, sesion, semilla):
    """Su teléfono reiniciaría el camión en cero y no podría venderla."""
    await _existencia(sesion, semilla["camion"], "48")
    otro = await _usuario(sesion, "VEND02")
    await _entrar(cliente)
    r = await _post(
        cliente,
        f"/panel/equipo/almacenes/{semilla['camion']}",
        "datos",
        codigo="CAMION_01",
        nombre="Camión 01",
        tipo="camion",
        responsable_id=str(otro),
        activo="1",
    )
    assert "El camión trae mercancía" in solo_texto(r)
    fila = await _uno(
        sesion, "SELECT responsable_id FROM almacenes WHERE id = :a", a=semilla["camion"]
    )
    assert fila["responsable_id"] == semilla["vendedor"]


async def test_un_camion_vacio_cambia_de_responsable_en_las_dos_puntas(cliente, sesion, semilla):
    otro = await _usuario(sesion, "VEND02")
    await _entrar(cliente)
    r = await _post(
        cliente,
        f"/panel/equipo/almacenes/{semilla['camion']}",
        "datos",
        codigo="CAMION_01",
        nombre="Camión 01",
        tipo="camion",
        responsable_id=str(otro),
        activo="1",
    )
    assert "cambió de responsable" in solo_texto(r)
    nuevo = await _uno(sesion, "SELECT almacen_id FROM usuarios WHERE id = :u", u=otro)
    viejo = await _uno(
        sesion, "SELECT almacen_id FROM usuarios WHERE id = :u", u=semilla["vendedor"]
    )
    assert nuevo["almacen_id"] == semilla["camion"]
    assert viejo["almacen_id"] is None
    # El teléfono del nuevo se entera por `identidad`.
    delta = await _uno(
        sesion,
        "SELECT payload FROM change_log WHERE entidad = 'identidad' AND vendedor_id = :u "
        " ORDER BY cursor DESC LIMIT 1",
        u=otro,
    )
    assert delta is not None and delta["payload"]["almacen_id"] == str(semilla["camion"])


async def test_el_tipo_no_cambia_si_ya_se_movio(cliente, sesion, semilla):
    await _existencia(sesion, semilla["bodega"], "5")
    await _entrar(cliente)
    r = await _post(
        cliente,
        f"/panel/equipo/almacenes/{semilla['bodega']}",
        "datos",
        codigo="BODEGA_PRINCIPAL",
        nombre="Bodega",
        tipo="merma",
        activo="1",
    )
    assert "El tipo solo se cambia" in solo_texto(r)


# ===========================================================================
# Listas de precios
# ===========================================================================
async def test_la_lista_por_omision_no_se_borra_y_se_cambia_con_confirmacion(
    cliente, sesion, semilla
):
    otra = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO listas_precios (id, codigo, nombre) VALUES (:l, 'MAYOREO', 'Mayoreo')"),
        {"l": otra},
    )
    await sesion.commit()
    await _entrar(cliente)
    general = f"/panel/equipo/listas/{semilla['lista_precios']}"
    r = await _post(cliente, general, "eliminar", confirmo="1")
    assert "Es la lista por omisión" in solo_texto(r)

    r = await _post(cliente, f"/panel/equipo/listas/{otra}", "por-omision")
    assert "Marca la casilla" in solo_texto(r)
    r = await _post(cliente, f"/panel/equipo/listas/{otra}", "por-omision", confirmo="1")
    assert "es ahora la lista por omisión" in solo_texto(r)
    filas = (await sesion.execute(text("SELECT codigo FROM listas_precios WHERE es_default"))).all()
    assert [f[0] for f in filas] == ["MAYOREO"]


async def test_una_lista_con_clientes_se_vacia_y_luego_se_borra(cliente, sesion, semilla):
    otra = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO listas_precios (id, codigo, nombre) VALUES (:l, 'MAYOREO', 'Mayoreo')"),
        {"l": otra},
    )
    cliente_id = await _cliente_en(sesion, semilla["ruta"])
    await sesion.execute(
        text("UPDATE clientes SET lista_precios_id = :l WHERE id = :c"),
        {"l": otra, "c": cliente_id},
    )
    await sesion.commit()
    await _entrar(cliente)
    ficha = f"/panel/equipo/listas/{otra}"
    r = await _post(cliente, ficha, "eliminar", confirmo="1")
    assert "pásalos a otra lista" in solo_texto(r)
    await _post(cliente, ficha, "mover-clientes", destino_id=str(semilla["lista_precios"]))
    r = await _post(cliente, ficha, "eliminar", confirmo="1")
    assert "Lista MAYOREO eliminada" in solo_texto(r)
    fila = await _uno(sesion, "SELECT lista_precios_id FROM clientes WHERE id = :c", c=cliente_id)
    assert fila["lista_precios_id"] == semilla["lista_precios"]


# ===========================================================================
# Proveedores
# ===========================================================================
async def test_un_proveedor_se_edita_y_se_borra_si_no_tiene_compras(cliente, sesion, semilla):
    proveedor = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO proveedores (id, codigo, nombre) VALUES (:p, 'ABARR01', 'Abarrotes')"),
        {"p": proveedor},
    )
    await sesion.commit()
    await _entrar(cliente)
    ficha = f"/panel/compras/proveedor/{proveedor}"
    pagina = await cliente.get(ficha)
    assert 'name="proveedor_id"' in pagina.text
    await cliente.post(
        "/panel/compras/proveedor",
        data={
            "csrf": _csrf(cliente, pagina),
            "proveedor_id": str(proveedor),
            "codigo": "ABARR01",
            "nombre": "Abarrotes del Centro",
            "dias_credito": "15",
        },
    )
    fila = await _uno(
        sesion, "SELECT nombre, dias_credito FROM proveedores WHERE id = :p", p=proveedor
    )
    assert (fila["nombre"], fila["dias_credito"]) == ("Abarrotes del Centro", 15)

    r = await _post(cliente, ficha, "eliminar", confirmo="1")
    assert "se eliminó" in solo_texto(r)
    assert await _uno(sesion, "SELECT 1 FROM proveedores WHERE id = :p", p=proveedor) is None


# ===========================================================================
# Motivos
# ===========================================================================
async def test_cambiar_si_un_motivo_se_cobra_viaja_al_telefono(cliente, sesion, semilla):
    await _entrar(cliente)
    pagina = await cliente.get("/panel/motivos")
    assert pagina.status_code == 200
    r = await cliente.post(
        "/panel/motivos/merma",
        data={
            "csrf": _csrf(cliente, pagina),
            "codigo": "CADUCADO",
            "nombre": "Producto caducado",
            "afecta_vendedor": "1",
            "activo": "1",
        },
        follow_redirects=True,
    )
    assert "Cambió si se le cobra al vendedor" in solo_texto(r)
    fila = await _uno(sesion, "SELECT afecta_vendedor FROM motivos_merma WHERE codigo = 'CADUCADO'")
    assert fila["afecta_vendedor"] is True
    delta = await _uno(
        sesion,
        "SELECT payload FROM change_log WHERE entidad = 'motivo_merma' "
        " ORDER BY cursor DESC LIMIT 1",
    )
    assert delta["payload"]["codigo"] == "CADUCADO"
    assert delta["payload"]["afecta_vendedor"] is True


async def test_un_motivo_nuevo_y_eliminarlo_lo_desactiva(cliente, sesion, semilla):
    await _entrar(cliente)
    pagina = await cliente.get("/panel/motivos")
    await cliente.post(
        "/panel/motivos/no-drop",
        data={
            "csrf": _csrf(cliente, pagina),
            "nuevo": "1",
            "codigo": "sin espacio",
            "nombre": "No había dónde estacionarse",
            "categoria": "operacion",
            "orden": "40",
        },
    )
    fila = await _uno(
        sesion, "SELECT nombre, activo FROM motivos_no_drop WHERE codigo = 'SIN_ESPACIO'"
    )
    assert fila["activo"] is True

    r = await cliente.post(
        "/panel/motivos/no-drop/SIN_ESPACIO/eliminar",
        data={"csrf": _csrf(cliente, pagina)},
        follow_redirects=True,
    )
    assert "desactivado" in solo_texto(r)
    fila = await _uno(sesion, "SELECT activo FROM motivos_no_drop WHERE codigo = 'SIN_ESPACIO'")
    assert fila["activo"] is False  # existe todavía: nunca se borra


async def test_el_supervisor_ve_los_motivos_pero_no_los_edita(cliente, sesion, semilla):
    await _usuario(sesion, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    pagina = await cliente.get("/panel/motivos")
    assert pagina.status_code == 200
    assert "forma_nueva_merma" not in pagina.text
    r = await cliente.post(
        "/panel/motivos/merma",
        data={"csrf": _csrf(cliente), "codigo": "ROTO", "nombre": "Roto", "activo": "1"},
    )
    assert r.status_code == 403


# ===========================================================================
# Objetivos
# ===========================================================================
async def test_un_objetivo_se_quita_con_su_boton(cliente, sesion, semilla):
    await _entrar(cliente)
    pagina = await cliente.get("/panel/objetivos")
    await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": _csrf(cliente, pagina),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "150000",
        },
    )
    pagina = await cliente.get("/panel/objetivos")
    assert 'id="quitar_R04"' in pagina.text
    await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": _csrf(cliente, pagina),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "",
        },
    )
    quedan = await _uno(sesion, "SELECT count(*) AS n FROM objetivos_ruta")
    assert quedan["n"] == 0


# ===========================================================================
# Las tablas llevan a la ficha
# ===========================================================================
async def test_cada_renglon_de_equipo_lleva_a_su_ficha(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/equipo")
    for tipo, id_ in [
        ("usuarios", semilla["vendedor"]),
        ("rutas", semilla["ruta"]),
        ("almacenes", semilla["camion"]),
        ("listas", semilla["lista_precios"]),
    ]:
        assert f"/panel/equipo/{tipo}/{id_}#editar" in r.text, tipo
        assert f"/panel/equipo/{tipo}/{id_}#eliminar" in r.text, tipo
        ficha = await cliente.get(f"/panel/equipo/{tipo}/{id_}")
        assert ficha.status_code == 200, tipo
