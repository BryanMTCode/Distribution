"""Cambiar el SKU, eliminar un producto, y quitar un precio de verdad.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
1. **Un producto que ya se usó NO se borra.** Aparece en una venta, y una venta es
   un papel que un cliente tiene en la mano. La pantalla dice en qué documento
   aparece y manda a darlo de baja, que es lo que la gente quiere el 99 % de las
   veces.
2. **El SKU se puede corregir**, con su unicidad validada y su cambio asentado: el
   día que la bodega no encuentre «SOPA-70» hay que poder saber en qué se convirtió.
3. **Quitar un precio llega al teléfono.** Era un defecto: la pantalla decía «el
   vendedor ya no la verá» y el vendedor la seguía viendo para siempre, porque el
   delta de borrado viajaba sin decir CUÁL de los precios del producto se había
   quitado. Ver la migración 0033.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, solo_texto

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf_de(respuesta) -> str:
    marca = 'name="csrf" value="'
    inicio = respuesta.text.index(marca) + len(marca)
    return respuesta.text[inicio : respuesta.text.index('"', inicio)]


@pytest.fixture
async def sopa(sesion, semilla) -> dict:
    """Un producto recién capturado: con presentaciones y precio, sin documentos."""
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'SOPA-70', 'Sopa de fideo 70 g', 'PZA', 0)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true), (:p, 'CAJA', 24, false)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio) "
            "VALUES (:l, :p, 'PZA', 12.3333), (:l, :p, 'CAJA', 296.0000)"
        ),
        {"l": semilla["lista_precios"], "p": producto},
    )
    await sesion.commit()
    return {"id": producto}


async def _guardar_datos(cliente, producto_id, **campos):
    detalle = await cliente.get(f"/panel/productos/{producto_id}")
    datos = {
        "csrf": _csrf_de(detalle),
        "sku": "SOPA-70",
        "nombre": "Sopa de fideo 70 g",
        "tasa_iva": "0.0000",
        "activo": "1",
        **campos,
    }
    return await cliente.post(
        f"/panel/productos/{producto_id}", data=datos, follow_redirects=True
    )


async def _eliminar(cliente, producto_id, *, confirmo="1"):
    detalle = await cliente.get(f"/panel/productos/{producto_id}")
    datos = {"csrf": _csrf_de(detalle)}
    if confirmo:
        datos["confirmo"] = confirmo
    return await cliente.post(
        f"/panel/productos/{producto_id}/eliminar", data=datos, follow_redirects=True
    )


# ---------------------------------------------------------------------------
# El SKU
# ---------------------------------------------------------------------------


async def test_EL_SKU_SE_CORRIGE_Y_QUEDA_ASENTADO(cliente, semilla, sopa, sesion):
    """No se editaba «porque es con lo que la bodega lo identifica», que es una razón
    operativa y no de integridad: ninguna tabla apunta al SKU, todas al `id`. Y esa
    razón se vuelve en contra el día que el SKU está mal escrito.
    """
    await _entrar(cliente)
    r = await _guardar_datos(cliente, sopa["id"], sku="SOPA-70G")

    assert "pasó de «SOPA-70» a «SOPA-70G»" in solo_texto(r)
    assert (
        await sesion.execute(
            text("SELECT sku FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one() == "SOPA-70G"

    fila = (
        await sesion.execute(
            text(
                "SELECT accion, datos_antes, datos_despues, usuario_id FROM auditoria "
                " WHERE entidad = 'producto' AND entidad_id = :p"
            ),
            {"p": sopa["id"]},
        )
    ).mappings().one()
    assert fila["accion"] == "cambiar_sku"
    assert fila["datos_antes"]["sku"] == "SOPA-70"
    assert fila["datos_despues"]["sku"] == "SOPA-70G"
    assert fila["usuario_id"] == semilla["admin"]


async def test_el_sku_viaja_al_telefono(cliente, semilla, sopa, sesion):
    await _entrar(cliente)
    await _guardar_datos(cliente, sopa["id"], sku="SOPA-70G")

    delta = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log "
                " WHERE entidad = 'producto' AND entidad_id = :p "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"p": sopa["id"]},
        )
    ).mappings().one()
    assert delta["payload"]["sku"] == "SOPA-70G"


async def test_un_sku_que_ya_es_de_otro_se_explica(cliente, semilla, sopa, sesion):
    """El UNIQUE de la base lo impediría igual, pero su error no le dice a nadie que
    el código ya lo tiene el atún."""
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua', 'PZA')"
        ),
        {"p": uuid.uuid4()},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await _guardar_datos(cliente, sopa["id"], sku="ATUN-140")

    assert "ya es de «Atún en agua»" in solo_texto(r)
    assert (
        await sesion.execute(
            text("SELECT sku FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one() == "SOPA-70"


async def test_el_sku_se_guarda_en_mayusculas(cliente, semilla, sopa, sesion):
    """Para que «sopa-70g» y «SOPA-70G» no sean dos productos distintos que la
    bodega no puede distinguir en una etiqueta."""
    await _entrar(cliente)
    await _guardar_datos(cliente, sopa["id"], sku="sopa-70g")

    assert (
        await sesion.execute(
            text("SELECT sku FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one() == "SOPA-70G"


async def test_el_sku_no_puede_quedar_vacio(cliente, semilla, sopa, sesion):
    await _entrar(cliente)
    r = await _guardar_datos(cliente, sopa["id"], sku="   ")
    assert "no puede quedar vacío" in solo_texto(r)


async def test_la_unidad_base_no_se_edita(cliente, semilla, sopa):
    """Cambiarla convertiría en piezas lo que se contó en cajas sin tocar un solo
    número. El campo está en la pantalla, deshabilitado y con su explicación."""
    await _entrar(cliente)
    plano = solo_texto(await cliente.get(f"/panel/productos/{sopa['id']}"))
    assert "No se edita." in plano
    assert "convertiría en piezas lo que se contó en cajas" in plano


# ---------------------------------------------------------------------------
# Eliminar
# ---------------------------------------------------------------------------


async def test_UN_PRODUCTO_SIN_HISTORIA_SE_BORRA_CON_SU_CONFIGURACION(
    cliente, semilla, sopa, sesion
):
    """El caso que justifica el borrado: el producto que se capturó por error."""
    await _entrar(cliente)
    r = await _eliminar(cliente, sopa["id"])

    assert "se borró" in solo_texto(r)
    for tabla in ("productos", "precios", "producto_unidades"):
        cuantos = (
            await sesion.execute(
                text(
                    f"SELECT count(*) FROM {tabla} "  # noqa: S608
                    f"WHERE {'id' if tabla == 'productos' else 'producto_id'} = :p"
                ),
                {"p": sopa["id"]},
            )
        ).scalar_one()
        assert cuantos == 0, f"quedaron renglones en {tabla}"

    # El antes queda asentado: después del DELETE no hay de dónde leerlo.
    fila = (
        await sesion.execute(
            text(
                "SELECT accion, datos_antes FROM auditoria "
                " WHERE entidad = 'producto' AND entidad_id = :p"
            ),
            {"p": sopa["id"]},
        )
    ).mappings().one()
    assert fila["accion"] == "eliminar"
    assert fila["datos_antes"]["sku"] == "SOPA-70"


async def test_UN_PRODUCTO_CON_UNA_VENTA_SE_DA_DE_BAJA_Y_NO_SE_BORRA(
    cliente, semilla, sopa, sesion
):
    """Una venta es un papel que un cliente tiene en la mano: el producto no se
    borra. Pero desde octubre de 2026 «Eliminar» tampoco se niega —la dirección
    pidió que el botón funcione—: lo da de baja, que es lo que la persona buscaba,
    y dice por qué no lo borró."""
    dispositivo = uuid.uuid4()
    cliente_id = uuid.uuid4()
    venta = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado) "
            "VALUES (:d, :u, 'POCO', 'activo')"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, creado_en, "
            "                      actualizado_en) "
            "VALUES (:c, 'CLI-X', 'La Esquina', :r, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, almacen_id, tipo, subtotal,
                                total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 1, 'VEND01-000001', :c, :u, :a, 'contado', 12.33, 12.33,
                    now(), CURRENT_DATE)
            """
        ),
        {
            "v": venta,
            "d": dispositivo,
            "c": cliente_id,
            "u": semilla["vendedor"],
            "a": semilla["camion"],
        },
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, 1, 1, 12.3333, 12.33)
            """
        ),
        {"id": uuid.uuid4(), "v": venta, "p": sopa["id"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await _eliminar(cliente, sopa["id"])

    plano = solo_texto(r)
    assert "se dio de baja" in plano
    assert "aparece en 1 ventas" in plano
    activo = (
        await sesion.execute(
            text("SELECT activo FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one_or_none()
    assert activo is False, "sigue existiendo, inactivo"
    # Y su venta, intacta.
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM venta_partidas WHERE producto_id = :p"),
            {"p": sopa["id"]},
        )
    ).scalar_one() == 1


async def test_un_producto_CON_EXISTENCIA_no_se_borra(cliente, semilla, sopa, sesion):
    """Un producto con saldo no es un error de captura: es mercancía que alguien
    tiene en un anaquel o arriba de un camión."""
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 30)"
        ),
        {"a": semilla["camion"], "p": sopa["id"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await _eliminar(cliente, sopa["id"])

    assert "Hay 30" in solo_texto(r)
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one() == 1


async def test_una_existencia_en_CERO_no_impide_borrar(cliente, semilla, sopa, sesion):
    """El renglón en cero es un residuo de configuración, no mercancía."""
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 0)"
        ),
        {"a": semilla["camion"], "p": sopa["id"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    await _eliminar(cliente, sopa["id"])

    assert (
        await sesion.execute(
            text("SELECT count(*) FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one() == 0


async def test_borrar_exige_la_confirmacion(cliente, semilla, sopa, sesion):
    await _entrar(cliente)
    r = await _eliminar(cliente, sopa["id"], confirmo="")

    assert "Marca la casilla" in solo_texto(r)
    assert (
        await sesion.execute(
            text("SELECT count(*) FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one() == 1


async def test_borrar_publica_la_BAJA_al_telefono(cliente, semilla, sopa, sesion):
    """El teléfono no borra: desactiva. Está así desde el principio en el aplicador
    —un producto retirado puede seguir apareciendo en ventas que aún no
    sincronizan—, y un DELETE local contra la llave foránea de `venta_partidas`
    abortaría la tanda entera y el dispositivo no volvería a sincronizar nunca.
    """
    await _entrar(cliente)
    await _eliminar(cliente, sopa["id"])

    delta = (
        await sesion.execute(
            text(
                "SELECT operacion, payload FROM change_log "
                " WHERE entidad = 'producto' AND entidad_id = :p "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"p": sopa["id"]},
        )
    ).mappings().one()
    assert delta["operacion"] == "delete"


async def test_GERENCIA_SI_ELIMINA(cliente, semilla, sopa, sesion):
    """Decisión de la dirección, octubre 2026: la 0031 le concede
    `catalogo.administrar`."""
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    await sesion.commit()

    await _entrar(cliente, "GER01")
    await _eliminar(cliente, sopa["id"])

    assert (
        await sesion.execute(
            text("SELECT count(*) FROM productos WHERE id = :p"), {"p": sopa["id"]}
        )
    ).scalar_one() == 0


# ---------------------------------------------------------------------------
# Quitar un precio: el defecto que llevaba desde la 0010
# ---------------------------------------------------------------------------


async def test_QUITAR_UN_PRECIO_LE_DICE_AL_TELEFONO_CUAL(
    cliente, semilla, sopa, sesion
):
    """El delta de precio se acota por `producto_id`, no por la llave del renglón.

    Con el payload en NULL —como lo dejaba el disparador genérico— el teléfono no
    podía saber cuál de los precios del producto se había quitado, así que no hacía
    nada: el vendedor seguía ofreciendo la presentación retirada, al precio que
    tenía. La pantalla decía «el vendedor ya no la verá», y era falso.
    """
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/productos/{sopa['id']}")
    await cliente.post(
        f"/panel/productos/{sopa['id']}/precios/quitar",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "unidad_codigo": "CAJA",
        },
        follow_redirects=True,
    )

    delta = (
        await sesion.execute(
            text(
                "SELECT operacion, payload FROM change_log "
                " WHERE entidad = 'precio' AND entidad_id = :p "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"p": sopa["id"]},
        )
    ).mappings().one()

    assert delta["operacion"] == "delete"
    assert delta["payload"] is not None, "el teléfono no sabría cuál quitar"
    assert delta["payload"]["unidad_codigo"] == "CAJA"
    assert delta["payload"]["lista_id"] == str(semilla["lista_precios"])
    assert delta["payload"]["producto_id"] == str(sopa["id"])
    # Y NO viaja el precio: mandarlo diría «esto vale 296.00» de algo que ya no
    # existe, y un aplicador distraído podría insertarlo de vuelta.
    assert "precio" not in delta["payload"]


async def test_capturar_un_precio_sigue_publicando_la_fila_completa(
    cliente, semilla, sopa, sesion
):
    """El disparador nuevo solo cambia el DELETE. Si también hubiera cambiado el
    upsert, el teléfono se quedaría sin precios."""
    await _entrar(cliente)
    detalle = await cliente.get(f"/panel/productos/{sopa['id']}")
    await cliente.post(
        f"/panel/productos/{sopa['id']}/precios",
        data={
            "csrf": _csrf_de(detalle),
            "lista_id": str(semilla["lista_precios"]),
            "precio_CAJA": "300.00",
        },
        follow_redirects=True,
    )

    delta = (
        await sesion.execute(
            text(
                "SELECT operacion, payload FROM change_log "
                " WHERE entidad = 'precio' AND entidad_id = :p "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"p": sopa["id"]},
        )
    ).mappings().one()
    assert delta["operacion"] == "upsert"
    assert Decimal(str(delta["payload"]["precio"])) == Decimal("300.0000")
    assert delta["payload"]["unidad_codigo"] == "CAJA"
