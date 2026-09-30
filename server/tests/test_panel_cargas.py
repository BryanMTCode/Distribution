"""La carga del camión: bodega → camión, y el delta que llena el teléfono.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Confirmar una carga es el momento en que la mercancía cambia de dueño. Tres
cosas pasan en una transacción y las tres importan:

1. El movimiento entra al libro mayor, que es append-only y no se puede deshacer
   editando.
2. La bodega baja y el camión sube, en la misma transacción que el movimiento.
3. El delta sale con su detalle, y solo entonces — un borrador no se publica.

Y una cuarta que no se ve hasta que pasa: **confirmar dos veces no puede mover
el inventario dos veces.** Un doble clic en una pantalla lenta duplicaría la
carga del día, y el faltante aparecería en la liquidación como si el vendedor se
hubiera llevado el doble.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

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


def _csrf(cliente, respuesta=None) -> str:
    """El token, de la página si lo trae, y si no derivado de la cookie.

    Una carga confirmada —o la vista de quien no tiene permiso de escribir— no
    dibuja ni un formulario, así que no hay de dónde leerlo. Mandar uno inventado
    daría 403 por el motivo equivocado: el de CSRF, no el que se está probando.
    El token se deriva de la cookie con HMAC, así que se puede recalcular igual
    que lo hace el servidor.
    """
    if respuesta is not None and 'name="csrf" value="' in respuesta.text:
        return _csrf_de(respuesta)

    import hashlib
    import hmac

    from app.core.config import obtener_config

    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cliente.cookies.get('dsd_panel', '')}".encode(),
        hashlib.sha256,
    ).hexdigest()


@pytest.fixture
async def catalogo(sesion, semilla) -> dict:
    """Un producto con caja de 24, y 20 cajas en la bodega."""
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA', 0.0000)"
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
    # 480 piezas = 20 cajas.
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 480)"
        ),
        {"a": semilla["bodega"], "p": producto},
    )
    await sesion.commit()
    return {"producto": producto}


async def _abrir(cliente, semilla) -> str:
    lista = await cliente.get("/panel/cargas")
    r = await cliente.post(
        "/panel/cargas/nueva",
        data={
            "csrf": _csrf_de(lista),
            "vendedor_id": str(semilla["vendedor"]),
            "almacen_origen_id": str(semilla["bodega"]),
            "fecha_operativa": date.today().isoformat(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert "/panel/cargas/" in r.headers["location"], r.headers["location"]
    return r.headers["location"].split("/panel/cargas/")[1].split("?")[0]


async def _agregar(cliente, carga_id: str, *, clave="ATUN-140", unidad="CAJA", cantidad="10"):
    detalle = await cliente.get(f"/panel/cargas/{carga_id}")
    return await cliente.post(
        f"/panel/cargas/{carga_id}/renglon",
        data={
            "csrf": _csrf(cliente, detalle),
            "producto": clave,
            "unidad_codigo": unidad,
            "cantidad": cantidad,
        },
        follow_redirects=True,
    )


async def _confirmar(cliente, carga_id: str):
    detalle = await cliente.get(f"/panel/cargas/{carga_id}")
    return await cliente.post(
        f"/panel/cargas/{carga_id}/confirmar",
        data={"csrf": _csrf(cliente, detalle)},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Capturar
# ---------------------------------------------------------------------------


async def test_la_conversion_a_unidad_base_ocurre_al_capturar(
    cliente, semilla, catalogo, sesion
):
    """Se capturan 10 cajas y se guardan 240 piezas.

    Quien carga el camión cuenta cajas, porque es lo que levanta con las manos.
    El inventario se lleva en unidad base. La multiplicación ocurre una sola vez,
    aquí: es lo que evita que media empresa cuente cajas y la otra media piezas.
    """
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    r = await _agregar(cliente, carga_id, cantidad="10")

    assert "10 CAJA = 240 PZA" in r.text

    cantidad = (
        await sesion.execute(
            text("SELECT cantidad FROM carga_detalle WHERE carga_id = :c"),
            {"c": uuid.UUID(carga_id)},
        )
    ).scalar_one()
    assert cantidad == Decimal("240.000")


async def test_el_mismo_producto_dos_veces_se_suma(cliente, semilla, catalogo, sesion):
    """Es lo que espera quien captura de dos tarimas del mismo producto.

    Reemplazar sería perder la primera captura sin avisar.
    """
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await _agregar(cliente, carga_id, cantidad="5")

    filas = (
        await sesion.execute(
            text("SELECT cantidad FROM carga_detalle WHERE carga_id = :c"),
            {"c": uuid.UUID(carga_id)},
        )
    ).scalars().all()
    assert filas == [Decimal("360.000")]


async def test_se_puede_capturar_por_codigo_de_barras(cliente, semilla, sesion):
    """Quien está en la bodega tiene un lector en la mano, no un UUID."""
    await _entrar(cliente)
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, codigo_barras, nombre, unidad_base) "
            "VALUES (:p, 'SOPA-200', '7501055300011', 'Sopa de fideo', 'PZA')"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true)"
        ),
        {"p": producto},
    )
    await sesion.commit()

    carga_id = await _abrir(cliente, semilla)
    r = await _agregar(cliente, carga_id, clave="7501055300011", unidad="PZA", cantidad="12")
    assert "Sopa de fideo" in r.text


async def test_media_caja_no_se_sube_a_un_camion(cliente, semilla, catalogo):
    """Un 2.5 aquí es un dedazo, y `cantidad_base` lo convertiría en 60 piezas
    con cara de dato bueno."""
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    r = await _agregar(cliente, carga_id, cantidad="2.5")
    assert "bultos completos" in r.text


async def test_una_presentacion_que_el_producto_no_tiene_se_explica(
    cliente, semilla, sesion
):
    await _entrar(cliente)
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'SOLO-PZA', 'Producto sin caja', 'PZA')"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true)"
        ),
        {"p": producto},
    )
    await sesion.commit()

    carga_id = await _abrir(cliente, semilla)
    r = await _agregar(cliente, carga_id, clave="SOLO-PZA", unidad="CAJA", cantidad="3")
    assert "no tiene la presentación CAJA" in r.text


# ---------------------------------------------------------------------------
# El borrador no sale de la oficina
# ---------------------------------------------------------------------------


async def test_el_borrador_NO_publica_delta(cliente, semilla, catalogo, sesion):
    """Mientras se arma, los renglones se pueden corregir.

    Publicarla haría que el vendedor viera —y pudiera vender— mercancía que la
    bodega todavía no le entregó.
    """
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")

    deltas = (
        await sesion.execute(
            text("SELECT count(*) FROM change_log WHERE entidad = 'carga'")
        )
    ).scalar_one()
    assert deltas == 0


async def test_el_borrador_no_mueve_inventario(cliente, semilla, catalogo, sesion):
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")

    en_bodega = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a"),
            {"a": semilla["bodega"]},
        )
    ).scalar_one()
    assert en_bodega == Decimal("480.000")
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar_one() == 0


# ---------------------------------------------------------------------------
# Confirmar
# ---------------------------------------------------------------------------


async def test_confirmar_mueve_el_inventario_y_escribe_el_libro_mayor(
    cliente, semilla, catalogo, sesion
):
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    r = await _confirmar(cliente, carga_id)

    assert "confirmada" in r.text

    existencias = dict(
        (
            await sesion.execute(
                text("SELECT almacen_id, cantidad FROM existencias WHERE producto_id = :p"),
                {"p": catalogo["producto"]},
            )
        ).all()
    )
    # 480 − 240 en la bodega, 240 en el camión.
    assert existencias[semilla["bodega"]] == Decimal("240.000")
    assert existencias[semilla["camion"]] == Decimal("240.000")

    movimiento = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, almacen_destino_id, cantidad, "
                "       documento_tipo, documento_id, usuario_id "
                "  FROM movimientos_inventario"
            )
        )
    ).mappings().one()
    assert movimiento["tipo"] == "carga"
    assert movimiento["almacen_origen_id"] == semilla["bodega"]
    assert movimiento["almacen_destino_id"] == semilla["camion"]
    assert movimiento["cantidad"] == Decimal("240.000")
    assert movimiento["documento_id"] == uuid.UUID(carga_id)
    assert movimiento["usuario_id"] == semilla["admin"]


async def test_el_movimiento_del_libro_mayor_no_se_puede_editar(
    cliente, semilla, catalogo, sesion
):
    """Append-only por disparador, no por convención.

    Un error se corrige con un documento compensatorio, que deja rastro de las
    dos cosas. Editar el historial haría que el inventario de ayer cambiara de
    tamaño según quién lo mire.
    """
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await _confirmar(cliente, carga_id)

    with pytest.raises(Exception, match="append-only"):
        await sesion.execute(text("UPDATE movimientos_inventario SET cantidad = 1"))
    await sesion.rollback()


async def test_el_delta_sale_CON_su_detalle(cliente, semilla, catalogo, sesion):
    """Sin el detalle, el teléfono sabe que le cargaron el camión y no qué.

    Es la razón de la migración 0015: el disparador de la 0010 publicaba solo el
    encabezado.
    """
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await _confirmar(cliente, carga_id)

    fila = (
        await sesion.execute(
            text(
                "SELECT vendedor_id, ruta_id, payload FROM change_log "
                " WHERE entidad = 'carga' ORDER BY cursor DESC LIMIT 1"
            )
        )
    ).mappings().one()

    # Acotado al vendedor: una carga solo le importa al equipo que la trae.
    assert fila["vendedor_id"] == semilla["vendedor"]
    assert fila["ruta_id"] == semilla["ruta"]

    payload = fila["payload"]
    assert payload["estado"] == "confirmada"
    assert payload["detalle"] == [
        {
            "producto_id": str(catalogo["producto"]),
            # String de tres decimales: el contrato (contracts/README.md §1.4).
            "cantidad": "240.000",
            "lote": None,
            "caducidad": None,
        }
    ]


async def test_CONFIRMAR_DOS_VECES_NO_DUPLICA_LA_CARGA(
    cliente, semilla, catalogo, sesion
):
    """Un doble clic en una pantalla lenta duplicaría la carga del día.

    El faltante aparecería en la liquidación como si el vendedor se hubiera
    llevado el doble, y nadie podría explicarlo.
    """
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await _confirmar(cliente, carga_id)

    segunda = await _confirmar(cliente, carga_id)
    assert "ya está confirmada" in segunda.text

    movimientos = (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar_one()
    assert movimientos == 1

    en_camion = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a"),
            {"a": semilla["camion"]},
        )
    ).scalar_one()
    assert en_camion == Decimal("240.000")


async def test_una_carga_sin_renglones_no_se_confirma(cliente, semilla, catalogo):
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    r = await _confirmar(cliente, carga_id)
    assert "sin un solo renglón" in r.text


async def test_una_carga_confirmada_ya_no_se_edita(cliente, semilla, catalogo):
    """Lo que salió de la bodega se corrige con un traspaso o un ajuste."""
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await _confirmar(cliente, carga_id)

    r = await _agregar(cliente, carga_id, cantidad="5")
    assert "ya salió de la bodega" in r.text


async def test_una_carga_confirmada_no_se_cancela(cliente, semilla, catalogo):
    """La mercancía ya está en el camión y puede haber ventas contra ella que aún
    no sincronizan. Lo que regresa es un retorno, no una cancelación que finge
    que el día no pasó."""
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await _confirmar(cliente, carga_id)

    detalle = await cliente.get(f"/panel/cargas/{carga_id}")
    r = await cliente.post(
        f"/panel/cargas/{carga_id}/cancelar",
        data={"csrf": _csrf(cliente, detalle)},
        follow_redirects=True,
    )
    assert "se registra como retorno" in r.text


# ---------------------------------------------------------------------------
# La bodega en negativo: §0.1 dentro de la oficina
# ---------------------------------------------------------------------------


async def test_cargar_mas_de_lo_que_hay_en_bodega_SE_PERMITE_y_se_avisa(
    cliente, semilla, catalogo, sesion
):
    """Si la bodega marca menos de lo que el almacenista está subiendo, **el
    sistema está mal, no el mundo.**

    Rechazar la carga significaría que el camión sale con mercancía que el sistema
    no registró, que es infinitamente peor que un número negativo en una caché.
    Es por esto que `existencias` no tiene `CHECK (cantidad >= 0)`.
    """
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    # Hay 480 piezas (20 cajas) y se suben 25.
    await _agregar(cliente, carga_id, cantidad="25")

    detalle = await cliente.get(f"/panel/cargas/{carga_id}")
    assert "no alcanza" in detalle.text

    r = await _confirmar(cliente, carga_id)
    assert "en negativo" in r.text

    en_bodega = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a"),
            {"a": semilla["bodega"]},
        )
    ).scalar_one()
    # 480 − 600.
    assert en_bodega == Decimal("-120.000")


# ---------------------------------------------------------------------------
# Abrir la carga
# ---------------------------------------------------------------------------


async def test_dos_cargas_del_mismo_dia_llevan_a_la_que_ya_existe(
    cliente, semilla, catalogo
):
    """El índice `uq_carga_vendedor_dia` lo impide, pero su error no le dice nada
    a quien está en la bodega a las seis de la mañana."""
    await _entrar(cliente)
    primera = await _abrir(cliente, semilla)

    lista = await cliente.get("/panel/cargas")
    r = await cliente.post(
        "/panel/cargas/nueva",
        data={
            "csrf": _csrf_de(lista),
            "vendedor_id": str(semilla["vendedor"]),
            "almacen_origen_id": str(semilla["bodega"]),
            "fecha_operativa": date.today().isoformat(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert primera in r.headers["location"]
    assert "ya+trae+la+carga" in r.headers["location"].replace("%20", "+")


async def test_un_vendedor_sin_camion_no_se_puede_cargar(cliente, semilla, sesion):
    """Sin almacén propio no hay a dónde cargar, y el dueño exclusivo del almacén
    es la garantía sobre la que descansa todo el modelo offline."""
    await _entrar(cliente)
    otro = uuid.uuid4()
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'VEND99', 'Vendedor sin camión', :h, 'vendedor', now(), now())"
        ),
        {
            "id": otro,
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    await sesion.commit()

    lista = await cliente.get("/panel/cargas")
    r = await cliente.post(
        "/panel/cargas/nueva",
        data={
            "csrf": _csrf_de(lista),
            "vendedor_id": str(otro),
            "almacen_origen_id": str(semilla["bodega"]),
            "fecha_operativa": date.today().isoformat(),
        },
        follow_redirects=True,
    )
    assert "no tiene camión asignado" in r.text


async def test_el_folio_no_se_repite(cliente, semilla, catalogo, sesion):
    """Dos personas armando cargas para dos camiones distintos a las seis de la
    mañana es el caso normal, no la excepción."""
    await _entrar(cliente)
    await _abrir(cliente, semilla)

    otro = uuid.uuid4()
    almacen = uuid.uuid4()
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'VEND02', 'Otro vendedor', :h, 'vendedor', now(), now())"
        ),
        {"id": otro, "s": semilla["sucursal"], "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.execute(
        text(
            "INSERT INTO almacenes(id, codigo, nombre, tipo, responsable_id) "
            "VALUES (:a, 'CAMION_02', 'Camión 02', 'camion', :u)"
        ),
        {"a": almacen, "u": otro},
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :a WHERE id = :u"), {"a": almacen, "u": otro}
    )
    await sesion.commit()

    lista = await cliente.get("/panel/cargas")
    await cliente.post(
        "/panel/cargas/nueva",
        data={
            "csrf": _csrf_de(lista),
            "vendedor_id": str(otro),
            "almacen_origen_id": str(semilla["bodega"]),
            "fecha_operativa": date.today().isoformat(),
        },
        follow_redirects=False,
    )

    folios = (
        await sesion.execute(text("SELECT folio FROM cargas ORDER BY folio"))
    ).scalars().all()
    assert len(folios) == 2
    assert len(set(folios)) == 2
    assert all(f.startswith("CG-") for f in folios)


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


@pytest.fixture
async def gerente(sesion, semilla) -> None:
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


async def test_el_gerente_ve_las_cargas_y_no_las_confirma(
    cliente, semilla, catalogo, gerente
):
    """Gerencia monitorea, no opera: no tiene `inventario.cargar`."""
    await _entrar(cliente, "ADMIN01")
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await cliente.get("/panel/salir")

    await _entrar(cliente, "GER01")
    lectura = await cliente.get(f"/panel/cargas/{carga_id}")
    assert lectura.status_code == 200
    assert "Atún en agua 140 g" in lectura.text
    assert "Confirmar la carga" not in lectura.text

    r = await cliente.post(
        f"/panel/cargas/{carga_id}/confirmar", data={"csrf": _csrf(cliente, lectura)}
    )
    assert r.status_code == 403
    assert "inventario.cargar" in r.text


async def test_confirmar_exige_el_token_csrf(cliente, semilla, catalogo):
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")

    r = await cliente.post(
        f"/panel/cargas/{carga_id}/confirmar", data={"csrf": "inventado"}
    )
    assert r.status_code == 403
