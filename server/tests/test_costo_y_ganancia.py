"""El costo del inventario y la ganancia, solo en el dashboard (ADR 0002 §180).

«En el dashboard quiero que me aparezca el costo del inventario, así como la
ganancia que se obtendrá en cada artículo», «también la ganancia de lo que ya se
vendió», «los que no tienen precio, que no aparezca la ganancia», y «solo en el
dashboard; solo yo tengo acceso».

1. **Cada venta guarda el costo de cuando se vendió**: si el costo cambia
   después, su ganancia no se mueve. La que no tenía costo usa el de hoy.
2. **Desempeño → Ganancia**: por vendedor, día y artículo, con el mayoreo de la
   oficina en su renglón; lo vendido sin costo, aparte.
3. **Inventario**: costo y ganancia por artículo, por almacén y del total; sin
   precio o sin costo, sin ganancia.
4. **Productos → Costos**: el costo de todos de un jalón, por pieza o por caja,
   todo o nada, con auditoría.
5. **Solo el administrador**: ni el gerente lo ve.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel
from tests.test_cierre_del_vendedor import dia  # noqa: F401
from tests.test_panel_liquidacion import _entrar
from tests.test_rls import cliente_rls, motor_restringido, rol_api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _costo(sesion, producto, costo: str) -> None:
    await sesion.execute(
        text("INSERT INTO producto_costos (producto_id, costo_promedio) VALUES (:p, :c) "
             "ON CONFLICT (producto_id) DO UPDATE SET costo_promedio = EXCLUDED.costo_promedio"),
        {"p": producto, "c": Decimal(costo)},
    )
    await sesion.commit()


async def _precio(sesion, semilla, producto, precio: str = "12.50") -> None:
    await sesion.execute(
        text("INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version) "
             "VALUES (:l, :p, 'PZA', :precio, 1)"),
        {"l": semilla["lista_precios"], "p": producto, "precio": Decimal(precio)},
    )
    await sesion.commit()


async def _vende(sesion, semilla, dia, piezas: int) -> uuid.UUID:  # noqa: F811
    """Una venta más de hoy, a $12.50 la pieza. Devuelve su renglón."""
    venta, partida = uuid.uuid4(), uuid.uuid4()
    importe = Decimal(piezas) * Decimal("12.50")
    await sesion.execute(
        text(
            "INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local, cliente_id, "
            "  vendedor_id, almacen_id, tipo, subtotal, total, fecha_dispositivo, fecha_operativa) "
            "VALUES (:v, :d, :n, :folio, :c, :u, :a, 'contado', :i, :i, now(), :dia)"
        ),
        {"v": venta, "d": dia["dispositivo"], "n": piezas, "folio": f"VEND01-{piezas:06d}",
         "c": dia["cliente"], "u": semilla["vendedor"], "a": semilla["camion"], "i": importe,
         "dia": dia["dia"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo, "
            "  factor_unidad, cantidad, cantidad_base, precio_unitario, importe) "
            "VALUES (:id, :v, 1, :p, 'PZA', 1, :n, :n, 12.5, :i)"
        ),
        {"id": partida, "v": venta, "p": dia["producto"], "n": piezas, "i": importe},
    )
    await sesion.commit()
    return partida


async def _costo_guardado(sesion, partida):
    return (
        await sesion.execute(
            text("SELECT costo_unitario FROM costo_de_lo_vendido WHERE partida_id = :p"),
            {"p": partida},
        )
    ).scalar_one_or_none()


async def test_cada_venta_guarda_el_costo_de_cuando_se_vendio(
    cliente, sesion, semilla, dia  # noqa: F811
):
    # La venta del día se hizo cuando el atún no tenía costo.
    sin_costo_entonces = (
        await sesion.execute(
            text("SELECT id FROM venta_partidas WHERE venta_id = :v"), {"v": dia["venta"]}
        )
    ).scalar_one()
    assert await _costo_guardado(sesion, sin_costo_entonces) is None

    await _costo(sesion, dia["producto"], "8.50")
    partida = await _vende(sesion, semilla, dia, 100)
    assert await _costo_guardado(sesion, partida) == Decimal("8.5000")

    # Entra una compra más cara: lo ya vendido no se mueve.
    await _costo(sesion, dia["producto"], "10.00")
    assert await _costo_guardado(sesion, partida) == Decimal("8.5000")

    # Y una de mayoreo de la oficina, 24 piezas a $12, con el costo de hoy.
    mayoreo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO ventas_mayoreo (id, folio, cliente_id, almacen_id, fecha, total) "
             "VALUES (:m, 'MY-000001', :c, :b, :dia, 288)"),
        {"m": mayoreo, "c": dia["cliente"], "b": semilla["bodega"], "dia": dia["dia"]},
    )
    await sesion.execute(
        text("INSERT INTO venta_mayoreo_partidas (venta_id, producto_id, unidad_codigo, factor, "
             "  bultos, cantidad, precio, importe) "
             "VALUES (:m, :p, 'PZA', 1, 24, 24, 12, 288)"),
        {"m": mayoreo, "p": dia["producto"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await cliente.get("/panel/ganancia?periodo=hoy")
    assert r.status_code == 200, r.text
    # La de antes del costo, con el de hoy: 2,250 − 180 × 10 = 450.
    # La de 100 piezas, con el suyo: 1,250 − 100 × 8.50 = 400.
    # La de mayoreo: 288 − 24 × 10 = 48.
    assert 'id="ganancia_total">$898.00<' in r.text
    assert "Mayoreo (oficina)" in r.text
    assert "aviso_sin_costo" not in r.text
    fila = r.text[r.text.index('id="ganancia_ATUN-140"'):]
    fila = fila[: fila.index("</tr>")]
    assert "$3,788.00" in fila and "$898.00" in fila


async def test_lo_que_no_tiene_costo_no_entra_en_la_ganancia(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _entrar(cliente)
    r = await cliente.get("/panel/ganancia?periodo=hoy")
    assert 'id="ganancia_total">$0.00<' in r.text
    aviso = r.text[r.text.index('id="aviso_sin_costo"'):]
    assert "$2,250.00" in aviso[: aviso.index("</div>")]
    fila = r.text[r.text.index('id="ganancia_ATUN-140"'):]
    assert "sin costo" in fila[: fila.index("</tr>")]


async def test_el_inventario_dice_su_costo_y_su_ganancia(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _entrar(cliente)
    # Sin precio todavía: tiene costo, pero no ganancia que decir.
    await _costo(sesion, dia["producto"], "8.50")
    r = await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}")
    costo = r.text[r.text.index('id="costo_ATUN-140"'):]
    assert "$8.50" in costo[: costo.index("</td>")]
    assert "$6,460.00" in costo[: costo.index("</td>")]  # 760 × 8.50
    ganancia = r.text[r.text.index('id="ganancia_ATUN-140"'):]
    assert "—" in ganancia[: ganancia.index("</td>")]

    await _precio(sesion, semilla, dia["producto"])
    r = await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}")
    ganancia = r.text[r.text.index('id="ganancia_ATUN-140"'):]
    ganancia = ganancia[: ganancia.index("</td>")]
    # 760 × (12.50 − 8.50) = 3,040; $4.00 la pieza, 32 % de lo que se vende.
    assert "$3,040.00" in ganancia and "$4.00 c/u" in ganancia and "32.0&nbsp;%" in ganancia
    assert 'id="total_ganancia"><strong>$3,040.00</strong>' in r.text
    # Arriba, todo: la bodega y el camión (60 más): 820 × 4 = 3,280.
    assert 'id="ganancia_inventario">$3,280.00<' in r.text


async def _guardar(cliente, datos: dict):
    pagina = await cliente.get("/panel/costos")
    return await cliente.post(
        "/panel/costos", data={"csrf": csrf_del_panel(cliente, pagina), **datos},
        follow_redirects=True,
    )


async def _costo_de(sesion, producto):
    await sesion.rollback()
    return (
        await sesion.execute(
            text("SELECT costo_promedio FROM producto_costos WHERE producto_id = :p"),
            {"p": producto},
        )
    ).scalar_one_or_none()


async def test_los_costos_se_capturan_de_un_jalon(cliente, sesion, semilla, dia):  # noqa: F811
    await _entrar(cliente)
    pagina = await cliente.get("/panel/costos?faltan=1")
    assert pagina.status_code == 200
    assert 'id="costo_ATUN-140"' in pagina.text and "1 artículo(s) sin costo" in pagina.text

    p = dia["producto"]
    r = await _guardar(cliente, {f"pieza_{p}": "9.20"})
    assert "Se guardó el costo de 1 artículo(s)." in r.text
    assert await _costo_de(sesion, p) == Decimal("9.2000")
    auditoria = (
        await sesion.execute(
            text("SELECT datos_antes, datos_despues FROM auditoria "
                 " WHERE entidad = 'producto' AND accion = 'capturar_costo'")
        )
    ).one()
    assert auditoria.datos_antes == {"costo": None}

    # Por caja: la de 24 a $240 son $10 la pieza.
    await _guardar(cliente, {f"caja_{p}": "240"})
    assert await _costo_de(sesion, p) == Decimal("10.0000")

    # Los dos, o un cero: no se guarda nada.
    r = await _guardar(cliente, {f"pieza_{p}": "11", f"caja_{p}": "264"})
    assert "no los dos" in r.text and "No se guardó nada" in r.text
    r = await _guardar(cliente, {f"pieza_{p}": "0"})
    assert "No se guardó nada" in r.text
    assert await _costo_de(sesion, p) == Decimal("10.0000")


async def test_solo_el_administrador_ve_el_costo(cliente, sesion, semilla, dia):  # noqa: F811
    await _costo(sesion, dia["producto"], "8.50")
    await _entrar(cliente, "GER01")
    for pantalla in ("/panel/ganancia", "/panel/costos"):
        r = await cliente.get(pantalla, follow_redirects=False)
        assert r.status_code == 403, pantalla
    inventario = await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}")
    assert inventario.status_code == 200
    assert 'id="costo_inventario"' not in inventario.text
    assert 'id="costo_ATUN-140"' not in inventario.text
    desempeno = await cliente.get("/panel/desempeno")
    assert 'href="/panel/ganancia"' not in desempeno.text

    await cliente.get("/panel/salir", follow_redirects=False)
    await _entrar(cliente)
    desempeno = await cliente.get("/panel/desempeno")
    assert 'href="/panel/ganancia"' in desempeno.text
    productos = await cliente.get("/panel/productos")
    assert 'href="/panel/costos"' in productos.text


async def test_las_pantallas_abren_con_el_rol_restringido(
    cliente_rls, sesion, semilla, dia  # noqa: F811
):
    """Como en el servidor: la API habla con PostgreSQL como `dsd_api`, que no
    salta la seguridad por renglón. Las tablas nuevas tienen que dejarse leer."""
    await _precio(sesion, semilla, dia["producto"])
    await _costo(sesion, dia["producto"], "8.50")
    await _vende(sesion, semilla, dia, 100)
    await _entrar(cliente_rls)
    r = await cliente_rls.get("/panel/ganancia?periodo=hoy")
    assert r.status_code == 200 and 'id="ganancia_total">$1,120.00<' in r.text
    r = await cliente_rls.get(f"/panel/inventario?almacen={semilla['bodega']}")
    assert r.status_code == 200 and 'id="total_ganancia"><strong>$3,040.00</strong>' in r.text
    r = await cliente_rls.get("/panel/costos")
    assert r.status_code == 200 and 'id="costo_ATUN-140"' in r.text
