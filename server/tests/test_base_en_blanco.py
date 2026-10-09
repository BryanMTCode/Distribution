"""La base en blanco: se quedan los usuarios, entran los artículos reales.

1. **El archivo de artículos** es el de la dirección: 25 artículos en cinco
   familias, y su valor en bodega cuadra con el total de la hoja ($417,237).
   Lo que viene mal escrito se dice antes de borrar nada.
2. **Se va** lo de operación —ventas, cargas, clientes, productos, existencias,
   libro mayor— y **se quedan** los usuarios, sus almacenes y sus teléfonos.
3. **Los artículos entran** con su precio en la lista por omisión y su
   existencia en la bodega principal, por una entrada de inventario inicial.
4. **Un teléfono que empieza de cero** recibe lo nuevo y nada de lo de antes.
5. **El comando pide la frase**: sin ella no se borra nada.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_panel_liquidacion import sembrar_dia_de_trabajo

pytestmark = pytest.mark.asyncio


def _bb():
    """El módulo, importado DENTRO de cada prueba y nunca aquí arriba.

    Importa `app.core.db`, que crea el motor de la base al importarse: hacerlo al
    recolectar las pruebas —antes de que `conftest` apunte a la base de pruebas—
    deja el motor con la URL de desarrollo, y fallan otras pruebas que lo usan
    (las del job de poda, el comando de arranque, el tablero). Ver
    `test_zona_horaria.py`.
    """
    from app.infra import base_en_blanco

    return base_en_blanco


async def _cuantos(sesion, tabla: str) -> int:
    return (await sesion.execute(text(f"SELECT count(*) FROM {tabla}"))).scalar_one()


async def _cuantos_deltas(sesion, entidad: str) -> int:
    return (
        await sesion.execute(
            text("SELECT count(*) FROM change_log WHERE entidad = :e"), {"e": entidad}
        )
    ).scalar_one()


async def test_el_archivo_es_el_de_la_direccion():
    articulos = _bb().leer_articulos()
    assert len(articulos) == 25
    assert len({a.familia for a in articulos}) == 5
    assert [a.sku for a in articulos][:3] == ["S-100", "S-101", "S-102"]
    # El total de la hoja: lo que vale lo que hay en la bodega a precio de venta.
    assert sum(a.existencia * a.precio for a in articulos) == Decimal("417237")


async def test_lo_mal_escrito_se_dice_antes_de_borrar(tmp_path):
    archivo = tmp_path / "articulos.csv"
    archivo.write_text(
        "sku,nombre,familia,existencia,precio\nS-1,Uno,F,1,10\ns-1,Otro,F,1,10\n",
        encoding="utf-8",
    )
    with pytest.raises(_bb().ArticulosInvalidos, match="dos veces"):
        _bb().leer_articulos(archivo)
    archivo.write_text(
        "sku,nombre,familia,existencia,precio\nS-1,Uno,F,1.5,10\n", encoding="utf-8"
    )
    with pytest.raises(_bb().ArticulosInvalidos, match="piezas enteras"):
        _bb().leer_articulos(archivo)


async def test_se_va_la_operacion_y_se_quedan_los_usuarios(sesion, semilla):
    dia = await sembrar_dia_de_trabajo(sesion, semilla)
    usuarios = await _cuantos(sesion, "usuarios")
    almacenes = await _cuantos(sesion, "almacenes")

    resultado = await _bb().poner_en_blanco(
        sesion, _bb().leer_articulos(), quien=await _bb().quien_registra(sesion)
    )
    assert resultado.borrado["ventas"] == 1
    assert resultado.articulos == 25
    assert resultado.entrada == "EN-000001"
    assert resultado.piezas == Decimal("10561")

    for tabla in ("ventas", "cargas", "clientes", "cortes_vendedor", "liquidaciones",
                  "mermas", "sesiones", "folios_rangos"):
        assert await _cuantos(sesion, tabla) == 0, tabla
    assert await _cuantos(sesion, "usuarios") == usuarios
    assert await _cuantos(sesion, "almacenes") == almacenes
    # El teléfono sigue vinculado, sin el rastro de su sincronización.
    cursor = (
        await sesion.execute(
            text("SELECT ultimo_cursor_pull FROM dispositivos WHERE id = :d"),
            {"d": dia["dispositivo"]},
        )
    ).scalar_one()
    assert cursor == 0

    # Los artículos, con su precio y su existencia en la bodega principal.
    assert await _cuantos(sesion, "productos") == 25
    fila = (
        await sesion.execute(
            text(
                "SELECT p.nombre, c.nombre AS familia, pr.precio, e.cantidad "
                "  FROM productos p "
                "  JOIN categorias c ON c.id = p.categoria_id "
                "  JOIN precios pr ON pr.producto_id = p.id AND pr.unidad_codigo = 'PZA' "
                "  JOIN existencias e ON e.producto_id = p.id AND e.almacen_id = :b "
                " WHERE p.sku = 'S-108'"
            ),
            {"b": semilla["bodega"]},
        )
    ).mappings().one()
    assert dict(fila) == {
        "nombre": "Costal Minino 15 kilos", "familia": "Ganador Minino",
        "precio": Decimal("579.00"), "cantidad": Decimal("197.000"),
    }
    # El camión quedó vacío: lo de antes ya no existe.
    en_camion = (
        await sesion.execute(
            text("SELECT count(*) FROM existencias WHERE almacen_id = :c"),
            {"c": semilla["camion"]},
        )
    ).scalar_one()
    assert en_camion == 0
    # Y queda escrito que se hizo.
    accion = (
        await sesion.execute(text("SELECT accion FROM auditoria"))
    ).scalars().all()
    assert accion == ["en_blanco"]


async def test_un_telefono_de_cero_recibe_lo_nuevo_y_nada_de_lo_de_antes(sesion, semilla):
    await sembrar_dia_de_trabajo(sesion, semilla)
    viejo = (
        await sesion.execute(text("SELECT id FROM productos WHERE sku = 'ATUN-140'"))
    ).scalar_one()
    # Lo que se queda conserva sus deltas: sin la lista de precios, un teléfono
    # que empieza de cero no tendría con qué cobrar.
    await sesion.execute(text("UPDATE listas_precios SET nombre = nombre"))
    await sesion.commit()
    listas = await _cuantos_deltas(sesion, "lista_precios")
    assert listas >= 1
    await _bb().poner_en_blanco(
        sesion, _bb().leer_articulos(), quien=await _bb().quien_registra(sesion)
    )
    assert await _cuantos_deltas(sesion, "lista_precios") == listas

    entidades = dict(
        (
            await sesion.execute(
                text("SELECT entidad, count(*) FROM change_log GROUP BY entidad")
            )
        ).all()
    )
    assert entidades["producto"] == 25
    assert entidades["precio"] == 25
    assert "carga" not in entidades and "venta" not in entidades
    del_viejo = (
        await sesion.execute(
            text("SELECT count(*) FROM change_log WHERE entidad_id = :p"), {"p": viejo}
        )
    ).scalar_one()
    assert del_viejo == 0


async def test_sin_la_frase_no_se_borra_nada(motor, sesion, semilla, monkeypatch):
    # El comando abre su propia sesión con el motor de la app; aquí, el de pruebas.
    monkeypatch.setattr(
        "app.core.db.CrearSesion",
        async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession),
    )
    from app.cli import base_en_blanco

    await sembrar_dia_de_trabajo(sesion, semilla)
    monkeypatch.setattr("builtins.input", lambda _="": "no")
    assert await base_en_blanco() == 1
    assert await _cuantos(sesion, "ventas") == 1
    await sesion.commit()  # que el TRUNCATE no espere a esta sesión

    monkeypatch.setattr("builtins.input", lambda _="": "en blanco")
    assert await base_en_blanco() == 0
    assert await _cuantos(sesion, "ventas") == 0
    assert await _cuantos(sesion, "productos") == 25
