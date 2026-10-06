"""¿Listo para operar?, los pendientes de hoy y el menú por permisos.

Lo que estas pruebas defienden:

1. La lista de arranque **dice qué falta y se apaga sola** cuando se completa: el
   aviso del tablero desaparece en cuanto el último paso bloqueante queda listo.
2. Los pendientes del tablero **solo listan lo que tiene algo**, en el orden del
   día, y nada que la persona no pueda abrir.
3. El menú **no ofrece callejones**: lo que una persona ve se abre, y lo que no
   ve es porque respondería 403.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from app.core.seguridad import hashear_password
from tests.conftest import PASSWORD_VENDEDOR, solo_texto
from tests.test_plan_visita import _cliente, _entrar

pytestmark = pytest.mark.asyncio


def _estado_de(html: str, titulo: str) -> str:
    """La clase del renglón del paso: 'listo', 'falta' o 'recomendado'."""
    m = re.search(
        r'<tr class="(listo|falta|recomendado)">(?:(?!</tr>).)*?' + re.escape(titulo),
        html,
        re.DOTALL,
    )
    assert m, f"no aparece el paso «{titulo}»"
    return m.group(1)


async def _producto_completo(sesion, semilla, sku: str = "ATUN-140") -> uuid.UUID:
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, creado_en, actualizado_en) "
            "VALUES (:id, :sku, :sku, 'PZA', now(), now())"
        ),
        {"id": producto, "sku": sku},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version) "
            "VALUES (:l, :p, 'PZA', 18.50, 1)"
        ),
        {"l": semilla["lista_precios"], "p": producto},
    )
    await sesion.execute(
        text("INSERT INTO existencias (almacen_id, producto_id, cantidad) VALUES (:a, :p, 240)"),
        {"a": semilla["bodega"], "p": producto},
    )
    await sesion.execute(
        text("INSERT INTO producto_costos (producto_id, costo_promedio) VALUES (:p, 12.75)"),
        {"p": producto},
    )
    await sesion.commit()
    return producto


async def _telefono(sesion, usuario_id) -> None:
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (gen_random_uuid(), :u, 'Moto G54', 'activo', now())"
        ),
        {"u": usuario_id},
    )
    await sesion.commit()


# ---------------------------------------------------------------------------
# ¿Listo para operar?
# ---------------------------------------------------------------------------


async def test_un_sistema_recien_instalado_dice_que_falta_y_donde(cliente, semilla):
    """La semilla tiene bodega, camión, ruta y lista; le faltan productos,
    existencia, teléfono y clientes."""
    await _entrar(cliente)
    r = await cliente.get("/panel/arranque")
    assert r.status_code == 200
    html = r.text

    assert _estado_de(html, "Una bodega activa") == "listo"
    assert _estado_de(html, "Una lista de precios por omisión") == "listo"
    assert _estado_de(html, "Cada vendedor con su camión y su ruta") == "listo"
    assert _estado_de(html, "Los productos, con precio") == "falta"
    assert _estado_de(html, "La existencia inicial en bodega") == "falta"
    assert _estado_de(html, "Cada vendedor con su teléfono vinculado") == "falta"
    assert _estado_de(html, "Los clientes, cada uno en su ruta") == "falta"
    assert "Sin teléfono: VEND01" in html
    # Cada paso que falta lleva a donde se arregla.
    assert 'href="/panel/equipos"' in html
    assert 'href="/panel/entradas"' in html

    tablero = await cliente.get("/panel")
    assert 'id="aviso_arranque"' in tablero.text
    assert "para empezar a operar" in solo_texto(tablero)


async def test_completo_queda_listo_y_el_aviso_del_tablero_se_apaga(cliente, sesion, semilla):
    await _producto_completo(sesion, semilla)
    await _telefono(sesion, semilla["vendedor"])
    lupita = await _cliente(sesion, semilla, "Abarrotes Lupita")

    await _entrar(cliente)
    html = (await cliente.get("/panel/arranque")).text
    assert "Listo para operar." in html
    # El plan de visita es recomendado: no detiene la operación, pero se dice.
    assert _estado_de(html, "El plan de visita") == "recomendado"
    assert "Quedan recomendaciones" in html
    assert 'id="aviso_arranque"' not in (await cliente.get("/panel")).text

    await sesion.execute(
        text(
            "INSERT INTO clientes_frecuencia (cliente_id, dia_semana) VALUES (:c, 1)"
        ),
        {"c": lupita},
    )
    await sesion.commit()
    html = (await cliente.get("/panel/arranque")).text
    assert _estado_de(html, "El plan de visita") == "listo"
    assert "Todo en su lugar." in html


async def test_una_ruta_con_clientes_y_sin_titular_detiene_el_arranque(cliente, sesion, semilla):
    """Sus clientes no le llegan a ningún teléfono: es justo lo que se descubre en
    la calle con el cliente enfrente."""
    await _cliente(sesion, semilla, "Abarrotes Lupita")
    await sesion.execute(
        text("UPDATE rutas SET vendedor_id = NULL WHERE id = :r"), {"r": semilla["ruta"]}
    )
    await sesion.commit()

    await _entrar(cliente)
    html = (await cliente.get("/panel/arranque")).text
    assert _estado_de(html, "Cada ruta con clientes tiene titular") == "falta"
    assert "Sin titular: R04" in html


async def test_un_producto_sin_costo_es_recomendacion_no_bloqueo(cliente, sesion, semilla):
    producto = await _producto_completo(sesion, semilla)
    await sesion.execute(
        text("DELETE FROM producto_costos WHERE producto_id = :p"), {"p": producto}
    )
    await sesion.commit()

    await _entrar(cliente)
    html = (await cliente.get("/panel/arranque")).text
    assert _estado_de(html, "El costo de los productos") == "recomendado"
    assert "en $0" in html


# ---------------------------------------------------------------------------
# Los pendientes de hoy
# ---------------------------------------------------------------------------


async def test_sin_pendientes_lo_dice_en_una_linea(cliente, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel")
    assert 'id="sin_pendientes"' in r.text
    assert 'id="lista_pendientes"' not in r.text


async def test_los_pendientes_salen_en_el_orden_del_dia_y_solo_los_que_tienen_algo(
    cliente, sesion, semilla
):
    # Al cierre: un cliente dado de alta en la calle (durante el día).
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, nombre_comercial, estatus, creado_en, actualizado_en) "
            "VALUES (gen_random_uuid(), 'Tienda nueva', 'prospecto', now(), now())"
        )
    )
    # En la mañana: una carga en borrador para hoy.
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, fecha_operativa, estado) "
            "VALUES (gen_random_uuid(), 'CG-000777', :b, :c, :v, CURRENT_DATE, 'borrador')"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "v": semilla["vendedor"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await cliente.get("/panel")
    tabla = r.text[r.text.index('id="lista_pendientes"') :]
    tabla = tabla[: tabla.index("</table>")]

    assert "carga(s) en borrador" in tabla
    assert "dado(s) de alta en la calle" in tabla
    assert tabla.index("carga(s) en borrador") < tabla.index("dado(s) de alta en la calle")
    assert tabla.index("Antes de que salgan los camiones") < tabla.index("Durante el día")
    # Lo que no tiene nada no se lista.
    assert "en cuarentena" not in tabla
    assert "Al cierre" not in tabla
    assert 'href="/panel/cargas"' in tabla


async def test_un_pendiente_que_la_persona_no_puede_abrir_no_se_le_lista(
    cliente, sesion, semilla
):
    gerente = await _usuario_de_oficina(sesion, "GER01", "gerente")
    await sesion.execute(
        text(
            "INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
            "VALUES (:u, 'inventario.ver', false)"
        ),
        {"u": gerente},
    )
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "                    vendedor_id, fecha_operativa, estado) "
            "VALUES (gen_random_uuid(), 'CG-000777', :b, :c, :v, CURRENT_DATE, 'borrador')"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "v": semilla["vendedor"]},
    )
    await sesion.commit()

    await _entrar(cliente, "GER01")
    html = (await cliente.get("/panel")).text
    assert "carga(s) en borrador" not in html
    assert 'href="/panel/cargas"' not in html


# ---------------------------------------------------------------------------
# El menú por permisos
# ---------------------------------------------------------------------------


async def _usuario_de_oficina(sesion, codigo: str, rol: str) -> uuid.UUID:
    usuario = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "SELECT :u, sucursal_id, :c, :c, :h, :r, now(), now() "
            "  FROM usuarios WHERE codigo = 'ADMIN01'"
        ),
        {"u": usuario, "c": codigo, "h": hashear_password(PASSWORD_VENDEDOR), "r": rol},
    )
    await sesion.commit()
    return usuario


def _enlaces_del_menu(html: str) -> set[str]:
    menu = html[html.index('<nav class="lateral"') : html.index("</nav>")]
    return set(re.findall(r'<a href="([^"]+)"', menu))


@pytest.mark.parametrize("rol", ["gerente", "supervisor"])
async def test_lo_que_el_menu_ofrece_se_abre_y_lo_que_esconde_se_negaria(
    cliente, sesion, semilla, rol
):
    from app.api.admin.comun import NAVEGACION

    usuario = await _usuario_de_oficina(sesion, "OFI01", rol)
    # Un usuario con excepciones, para que el menú tenga algo que esconder: con
    # los roles de hoy, gerente y supervisor ven todo.
    for permiso in ("inventario.ver", "tablero.ver"):
        await sesion.execute(
            text(
                "INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
                "VALUES (:u, :p, false)"
            ),
            {"u": usuario, "p": permiso},
        )
    await sesion.commit()

    await _entrar(cliente, "OFI01")
    visibles = _enlaces_del_menu((await cliente.get("/panel")).text)
    todas = {ruta for _, enlaces in NAVEGACION for ruta, _ in enlaces}

    assert "/panel/cargas" not in visibles
    assert "/panel/desempeno" not in visibles
    for ruta in sorted(visibles):
        r = await cliente.get(ruta)
        assert r.status_code == 200, f"el menú ofrece {ruta} y responde {r.status_code}"
    for ruta in sorted(todas - visibles):
        r = await cliente.get(ruta)
        assert r.status_code == 403, f"el menú esconde {ruta} pero se abre ({r.status_code})"


async def test_un_modulo_sin_pantallas_visibles_no_aparece(cliente, sesion, semilla):
    usuario = await _usuario_de_oficina(sesion, "OFI01", "supervisor")
    for permiso in ("inventario.ver",):
        await sesion.execute(
            text(
                "INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
                "VALUES (:u, :p, false)"
            ),
            {"u": usuario, "p": permiso},
        )
    await sesion.commit()

    await _entrar(cliente, "OFI01")
    html = (await cliente.get("/panel")).text
    assert "<summary>Almacén</summary>" not in html
    assert "<summary>Catálogos</summary>" in html
