"""La base en blanco para empezar a operar: solo queda ADMIN01 (ADR 0002 §131).

1. **Ninguna tabla sin decidir**: cada tabla del esquema está en una de las
   listas del comando, y en una sola.
2. **Se va todo** —los demás usuarios con sus teléfonos, artículos, tiendas,
   rutas, bodegas y camiones, ventas, cargas, el libro mayor— y **se queda**
   ADMIN01 con sus teléfonos y lo que siembran las migraciones. Queda una Bodega
   principal vacía y los folios empiezan en 1.
3. **Solo un administrador activo** puede quedarse: si no, nadie podría entrar.
4. **El comando pide la palabra**: sin ella no se borra nada.
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


async def _cuantos(sesion, tabla: str) -> int:
    return (await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))).scalar_one()


async def test_cada_tabla_esta_decidida_una_sola_vez(sesion):
    eo = _eo()
    tablas = set(
        (await sesion.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )).scalars()
    )
    listas = [eo.SE_QUEDAN, eo.SE_QUEDAN_EN_PARTE, eo.SE_BORRAN_A_MANO, eo.SE_VACIAN]
    decididas = [t for lista in listas for t in lista]
    assert len(decididas) == len(set(decididas)), "una tabla está en dos listas"
    assert tablas - set(decididas) == set(), "tablas nuevas sin decidir si se quedan"
    assert set(decididas) - tablas == set(), "listas con tablas que ya no existen"


async def _telefono_del_admin(sesion, semilla) -> uuid.UUID:
    telefono = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado) "
             "VALUES (:d, :u, 'Teléfono de Bryan', 'activo')"),
        {"d": telefono, "u": semilla["admin"]},
    )
    await sesion.commit()
    return telefono


async def test_se_va_todo_y_queda_admin01_con_su_telefono(sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    telefono = await _telefono_del_admin(sesion, semilla)
    roles = await _cuantos(sesion, "roles")
    listas = await _cuantos(sesion, "listas_precios")

    plan = await _eo().planear(sesion)
    assert plan.se_queda.codigo == "ADMIN01" and plan.se_queda.telefonos == 1
    assert [c for c, _ in plan.usuarios_que_se_van] == ["VEND01"]
    assert plan.telefonos_que_se_van == 1
    assert plan.cuantos["ventas"] == 1 and plan.cuantos["productos"] >= 1
    await _eo().aplicar(sesion, plan)

    # Solo ADMIN01, con sus permisos y su teléfono, marcado para empezar de cero.
    codigos = (await sesion.execute(text("SELECT codigo FROM usuarios"))).scalars().all()
    assert codigos == ["ADMIN01"]
    telefonos = (
        await sesion.execute(text("SELECT id, empezar_de_cero FROM dispositivos"))
    ).all()
    assert [tuple(t) for t in telefonos] == [(telefono, True)]
    assert dia["dispositivo"] != telefono
    otros_permisos = (
        await sesion.execute(
            text("SELECT count(*) FROM usuarios_permisos WHERE usuario_id <> :u"),
            {"u": semilla["admin"]},
        )
    ).scalar_one()
    assert otros_permisos == 0

    # Lo de la operación y el catálogo, en cero.
    for tabla in ("ventas", "cargas", "clientes", "productos", "precios", "categorias",
                  "rutas", "usuarios_rutas", "existencias", "movimientos_inventario",
                  "liquidaciones", "cortes_vendedor", "mermas", "entradas", "sesiones",
                  "folios_rangos", "sync_lotes", "usuarios_codigos_anteriores"):
        assert await _cuantos(sesion, tabla) == 0, tabla
    # Una Bodega principal vacía, para el inventario real.
    almacenes = (await sesion.execute(text("SELECT codigo, tipo FROM almacenes"))).all()
    assert [tuple(a) for a in almacenes] == [("BODEGA_PRINCIPAL", "bodega")]
    # Lo que siembran las migraciones, intacto.
    assert await _cuantos(sesion, "roles") == roles
    assert await _cuantos(sesion, "listas_precios") == listas
    # Los folios empiezan en 1 y las tiendas nuevas en 1934 (§122).
    assert (await sesion.execute(text("SELECT nextval('seq_folio_venta')"))).scalar_one() == 1
    assert (await sesion.execute(text("SELECT nextval('seq_folio_carga')"))).scalar_one() == 1
    assert (
        await sesion.execute(text("SELECT nextval('seq_codigo_cliente')"))
    ).scalar_one() == 1934
    # En el change_log solo queda lo que un teléfono nuevo necesita.
    sobran = (
        await sesion.execute(
            text("SELECT count(*) FROM change_log "
                 " WHERE ruta_id IS NOT NULL OR (vendedor_id IS NOT NULL AND vendedor_id <> :u)"),
            {"u": semilla["admin"]},
        )
    ).scalar_one()
    assert sobran == 0
    # Y queda escrito.
    accion = (await sesion.execute(text("SELECT accion FROM auditoria"))).scalars().all()
    assert accion == ["para_operar"]


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

    await sembrar_dia_de_trabajo(sesion, semilla)
    await sesion.execute(text("UPDATE clientes SET lat = 23.2494, lng = -106.4111"))
    await sesion.commit()
    assert await empezar_a_operar(["--resumen"]) == 0
    salida = capsys.readouterr().out
    assert "Se queda: ADMIN01" in salida
    assert "OJO: 1 tienda(s) tienen la ubicación" in salida
    assert "VEND01 «Juan Pérez»" in salida
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
    assert "Listo. La base está en blanco y solo queda ADMIN01." in capsys.readouterr().out
    await sesion.rollback()
    assert await _cuantos(sesion, "usuarios") == 1
    assert await _cuantos(sesion, "ventas") == 0
