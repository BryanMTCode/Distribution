"""Empezar a operar: se van los usuarios, la operación queda en cero (ADR 0002 §131).

1. **Ninguna tabla sin decidir**: cada tabla del esquema está en una de las
   listas del comando, y en una sola.
2. **Se van** los usuarios menos ADMIN01, con sus teléfonos y sus camiones, y
   toda la operación (ventas, cargas, cortes, existencias, libro mayor).
   **Se quedan** ADMIN01 con su teléfono, los artículos con su precio, las
   tiendas con su ubicación y su plan de visita, las rutas (sin titular) y las
   bodegas (vacías). Los folios empiezan en 1; los códigos de tienda siguen.
3. **Un teléfono nuevo** baja los artículos y las tiendas al día, y nada de la
   operación ni de los que se fueron.
4. **Después, «Eliminar» del panel funciona** con un usuario nuevo.
5. **Solo un administrador activo** puede quedarse, y **el comando pide la
   palabra**.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_panel_liquidacion import sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


def _eo():
    """Importado dentro de cada prueba: ver `_bb` en test_base_en_blanco.py."""
    from app.infra import empezar_a_operar

    return empezar_a_operar


async def _cuantos(sesion, tabla: str, donde: str = "true") -> int:
    return (
        await sesion.execute(text(f"SELECT count(*) FROM {tabla} WHERE {donde}"))
    ).scalar_one()


async def test_cada_tabla_esta_decidida_una_sola_vez(sesion):
    eo = _eo()
    tablas = set(
        (await sesion.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )).scalars()
    )
    decididas = [*eo.SE_QUEDAN, *eo.SE_QUEDAN_Y_SE_LIMPIAN, *eo.SE_VACIAN]
    assert len(decididas) == len(set(decididas)), "una tabla está en dos listas"
    assert tablas - set(decididas) == set(), "tablas nuevas sin decidir qué pasa con ellas"
    assert set(decididas) - tablas == set(), "listas con tablas que ya no existen"


async def _preparar(sesion, semilla) -> dict:
    """Un día de trabajo del vendedor, una tienda con ubicación y plan de visita
    dada de alta por él desde su teléfono, y un teléfono del administrador."""
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    telefono = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado) "
             "VALUES (:d, :u, 'Teléfono de Bryan', 'activo')"),
        {"d": telefono, "u": semilla["admin"]},
    )
    await sesion.execute(
        text("UPDATE clientes SET lat = 23.2494, lng = -106.4111, creado_por = :v, "
             "       dispositivo_id = :d, visita_reprogramada = current_date"),
        {"v": semilla["vendedor"], "d": dia["dispositivo"]},
    )
    await sesion.execute(
        text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana) "
             "SELECT id, 4 FROM clientes")
    )
    await sesion.commit()
    return {"dia": dia, "telefono_admin": telefono}


async def test_se_van_los_usuarios_y_la_operacion_y_se_quedan_articulos_y_tiendas(
    sesion, semilla
):
    datos = await _preparar(sesion, semilla)
    productos = await _cuantos(sesion, "productos")
    clientes = await _cuantos(sesion, "clientes")
    precios = await _cuantos(sesion, "precios")
    assert productos and clientes

    plan = await _eo().planear(sesion)
    assert plan.se_queda.codigo == "ADMIN01" and plan.se_queda.telefonos == 1
    assert [c for c, _ in plan.usuarios_que_se_van] == ["VEND01"]
    assert plan.camiones == ["CAMION_01"]
    assert plan.se_van["ventas"] == 1
    assert plan.se_quedan["tiendas"] == clientes
    assert plan.tiendas_con_ubicacion == clientes
    await _eo().aplicar(sesion, plan)

    # Solo ADMIN01, con su teléfono marcado para empezar de cero.
    assert (await sesion.execute(text("SELECT codigo FROM usuarios"))).scalars().all() == [
        "ADMIN01"
    ]
    telefonos = (await sesion.execute(text("SELECT id, empezar_de_cero FROM dispositivos"))).all()
    assert [tuple(t) for t in telefonos] == [(datos["telefono_admin"], True)]

    # La operación, en cero.
    for tabla in ("ventas", "cargas", "liquidaciones", "cortes_vendedor", "mermas",
                  "entradas", "existencias", "movimientos_inventario", "cuenta_vendedor",
                  "sesiones", "folios_rangos", "sync_lotes", "usuarios_rutas"):
        assert await _cuantos(sesion, tabla) == 0, tabla
    # El catálogo y las tiendas, intactos; las tiendas con su ubicación y su
    # plan, sin quién las dio de alta ni la visita reprogramada.
    assert await _cuantos(sesion, "productos") == productos
    assert await _cuantos(sesion, "precios") == precios
    assert await _cuantos(sesion, "clientes") == clientes
    assert await _cuantos(sesion, "clientes", "lat IS NOT NULL AND creado_por IS NULL "
                          "AND dispositivo_id IS NULL AND visita_reprogramada IS NULL") == clientes
    assert await _cuantos(sesion, "clientes_frecuencia") == clientes
    # Las rutas sin titular; sin camiones; la bodega se queda.
    assert await _cuantos(sesion, "rutas") == 1
    assert await _cuantos(sesion, "rutas", "vendedor_id IS NOT NULL") == 0
    almacenes = (await sesion.execute(text("SELECT codigo, tipo FROM almacenes"))).all()
    assert [tuple(a) for a in almacenes] == [("BODEGA_PRINCIPAL", "bodega")]
    # Los folios empiezan en 1; los códigos de tienda siguen después del último.
    assert (await sesion.execute(text("SELECT nextval('seq_folio_venta')"))).scalar_one() == 1
    # Y queda escrito.
    accion = (await sesion.execute(text("SELECT accion FROM auditoria"))).scalars().all()
    assert accion == ["para_operar"]


async def test_un_telefono_nuevo_baja_articulos_y_tiendas_al_dia(sesion, semilla):
    await _preparar(sesion, semilla)
    catalogo = "entidad IN ('producto', 'producto_unidad', 'precio', 'categoria')"
    deltas_del_catalogo = await _cuantos(sesion, "change_log", catalogo)
    assert deltas_del_catalogo
    await _eo().aplicar(sesion, await _eo().planear(sesion))

    # Los del catálogo se quedan todos.
    assert await _cuantos(sesion, "change_log", catalogo) == deltas_del_catalogo
    entidades = set(
        (await sesion.execute(text("SELECT DISTINCT entidad FROM change_log"))).scalars()
    )
    assert {"producto", "cliente"} <= entidades
    assert entidades.isdisjoint({"carga", "venta", "cartera"})
    # De cada tienda, un solo delta, y al día: sin quién la dio de alta.
    deltas = (
        await sesion.execute(
            text("SELECT entidad_id, payload FROM change_log WHERE entidad = 'cliente'")
        )
    ).all()
    assert len(deltas) == await _cuantos(sesion, "clientes")
    for _, payload in deltas:
        assert payload["creado_por"] is None and payload["visita_reprogramada"] is None
        assert payload["lat"] is not None
    # Nada que apunte a quien ya no existe.
    sobran = await _cuantos(
        sesion, "change_log",
        f"vendedor_id IS NOT NULL AND vendedor_id <> '{semilla['admin']}'",
    )
    assert sobran == 0


async def test_despues_eliminar_del_panel_funciona(cliente, sesion, semilla):
    from tests.conftest import csrf_del_panel as _csrf
    from tests.test_panel_equipo import _crear_usuario, _entrar

    await _preparar(sesion, semilla)
    await _eo().aplicar(sesion, await _eo().planear(sesion))
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="VEND01")
    nuevo = (
        await sesion.execute(text("SELECT id FROM usuarios WHERE codigo = 'VEND01'"))
    ).scalar_one()
    ficha = await cliente.get(f"/panel/equipo/usuarios/{nuevo}")
    r = await cliente.post(
        f"/panel/equipo/usuarios/{nuevo}/eliminar",
        data={"csrf": _csrf(cliente, ficha), "confirmo": "1"},
        follow_redirects=True,
    )
    assert "VEND01 se eliminó" in r.text
    await sesion.rollback()
    assert (await sesion.execute(text("SELECT codigo FROM usuarios"))).scalars().all() == [
        "ADMIN01"
    ]


async def test_solo_puede_quedarse_un_administrador_activo(sesion, semilla):
    with pytest.raises(_eo().NoSePuede, match="ningún usuario"):
        await _eo().planear(sesion, "NADIE")
    with pytest.raises(_eo().NoSePuede, match="no es un administrador"):
        await _eo().planear(sesion, "VEND01")
    await sesion.execute(text("UPDATE usuarios SET activo = false WHERE codigo = 'ADMIN01'"))
    await sesion.commit()
    with pytest.raises(_eo().NoSePuede, match="no es un administrador"):
        await _eo().planear(sesion, "admin01")


async def test_el_comando_pide_la_palabra(motor, sesion, semilla, monkeypatch, capsys):
    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import empezar_a_operar

    await _preparar(sesion, semilla)
    assert await empezar_a_operar(["--resumen"]) == 0
    salida = capsys.readouterr().out
    assert "Se queda: ADMIN01" in salida
    assert "VEND01 «Juan Pérez»" in salida
    assert "Se van sus 1 camión(es): CAMION_01." in salida
    assert "1 ventas" in salida and "tiendas con su ubicación" in salida
    assert "Con --resumen no se cambió nada." in salida
    assert await _cuantos(sesion, "usuarios") == 2

    monkeypatch.setattr("builtins.input", lambda _="": "si")
    assert await empezar_a_operar([]) == 1
    assert "No se cambió nada." in capsys.readouterr().out
    assert await _cuantos(sesion, "usuarios") == 2

    assert await empezar_a_operar(["--se-queda", "VEND01"]) == 1
    assert "no es un administrador" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda _="": "operar")
    assert await empezar_a_operar([]) == 0
    assert "Listo. Solo queda ADMIN01" in capsys.readouterr().out
    await sesion.rollback()
    assert await _cuantos(sesion, "usuarios") == 1
    assert await _cuantos(sesion, "ventas") == 0
    assert await _cuantos(sesion, "clientes") == 1
