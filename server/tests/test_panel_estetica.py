"""El dashboard más fácil de usar (ADR 0002 §128).

«Quiero que revises el dashboard y veas cómo hacerlo lo más fácil de usar… por
ejemplo en los almacenes se hace una caja de cada almacén, pero si son muchos se
harán muchos recuadros.»

1. **Inventario**: tres cifras que no crecen, un selector y la tabla de los
   almacenes —un renglón cada uno—, no un recuadro por almacén.
2. **Plan de visita**: los días son un filtro; con uno elegido la lista trae
   solo los de ese día, y guardar no toca a los demás.
3. **Usuarios**: «Editar» a la vista; la contraseña, desactivar y eliminar en
   «Más».
"""

from __future__ import annotations

import pytest

from tests.conftest import csrf_del_panel as _csrf
from tests.conftest import solo_texto
from tests.test_panel_inventario import con_inventario  # noqa: F401
from tests.test_plan_visita import _cliente, _entrar, _plan

pytestmark = pytest.mark.asyncio


async def test_el_inventario_es_una_tabla_de_almacenes(cliente, semilla, con_inventario):  # noqa: F811
    await _entrar(cliente)
    r = await cliente.get("/panel/inventario")
    assert r.status_code == 200
    assert 'id="resumen_inventario"' in r.text and 'id="elegir_almacen"' in r.text
    tabla = r.text.split('id="tabla_almacenes"')[1].split("</table>")[0]
    # Un renglón por almacén, el que se ve marcado.
    assert tabla.count("<tr") == 1 + 2   # el encabezado, la bodega y el camión
    assert 'class="elegido"' in tabla
    # Ya no hay un recuadro por almacén.
    assert r.text.count('class="tarjeta"') == 3


async def test_el_plan_se_filtra_por_dia_y_guardar_no_toca_a_los_demas(
    cliente, sesion, semilla
):
    lunes = await _cliente(sesion, semilla, "Abarrotes Lunes", 1)
    martes = await _cliente(sesion, semilla, "Abarrotes Martes", 1)
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    await cliente.post(
        f"/panel/plan-visita/{semilla['ruta']}",
        data={"csrf": _csrf(cliente, pagina), f"orden_{lunes}": "1", f"orden_{martes}": "1",
              f"dia_{lunes}_1": "1", f"dia_{martes}_2": "1"},
    )

    # Con el lunes elegido, solo el del lunes.
    pagina = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}&dia=1")
    texto = solo_texto(pagina)
    assert "Abarrotes Lunes" in texto and "Abarrotes Martes" not in texto
    assert "Lun · 1" in texto and "Todos · 2" in texto

    # Guardar desde ahí cambia el del lunes y deja al del martes como estaba.
    r = await cliente.post(
        f"/panel/plan-visita/{semilla['ruta']}",
        data={"csrf": _csrf(cliente, pagina), "dia": "1", "en_pantalla": str(lunes),
              f"orden_{lunes}": "3", f"dia_{lunes}_1": "1", f"dia_{lunes}_5": "1"},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("&dia=1") or "&dia=1&" in r.headers["location"]
    assert await _plan(sesion, lunes) == {(1, None), (5, None)}
    assert await _plan(sesion, martes) == {(2, None)}


async def test_las_acciones_raras_del_usuario_van_en_mas(cliente, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/equipo")
    renglon = r.text.split(f'id="mas_{semilla["vendedor"]}"')[1].split("</details>")[0]
    assert f'/panel/equipo/usuarios/{semilla["vendedor"]}/password' in renglon
    assert f'/panel/equipo/usuarios/{semilla["vendedor"]}/activo' in renglon
    assert "Eliminar" in renglon
