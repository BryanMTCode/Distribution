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


async def test_el_gerente_tambien_carga(cliente, sesion, semilla, catalogo, gerente):
    """Antes: «gerencia monitorea, no opera». Desde la migración 0044 el gerente
    tiene `inventario.cargar`: la dirección pidió que los puestos de arriba
    carguen camiones —también desde la app— y el vendedor no."""
    await _entrar(cliente, "ADMIN01")
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")
    await cliente.get("/panel/salir")

    await _entrar(cliente, "GER01")
    lectura = await cliente.get(f"/panel/cargas/{carga_id}")
    assert lectura.status_code == 200
    assert "Confirmar la carga" in lectura.text

    r = await cliente.post(
        f"/panel/cargas/{carga_id}/confirmar", data={"csrf": _csrf(cliente, lectura)}
    )
    assert r.status_code == 303
    estado = (
        await sesion.execute(text("SELECT estado FROM cargas WHERE id = :c"), {"c": carga_id})
    ).scalar_one()
    assert estado == "confirmada"


async def test_confirmar_exige_el_token_csrf(cliente, semilla, catalogo):
    await _entrar(cliente)
    carga_id = await _abrir(cliente, semilla)
    await _agregar(cliente, carga_id, cantidad="10")

    r = await cliente.post(
        f"/panel/cargas/{carga_id}/confirmar", data={"csrf": "inventado"}
    )
    assert r.status_code == 403

# ---------------------------------------------------------------------------
# La regla del §2.3: no se carga con operaciones pendientes
# ---------------------------------------------------------------------------
# `docs/ARQUITECTURA.md` §2.3 la declaraba y nada la imponía. Lo que previene es
# el escenario más ordinario de una ruta con mala señal:
#
#   el teléfono se queda con ventas del lunes sin subir → el martes se carga el
#   camión → la carga publica el delta → las ventas del lunes llegan con su
#   fecha vieja → los modelos recalculan el lunes → el arqueo que alguien firmó
#   el lunes deja de cuadrar con las ventas del lunes.


async def _equipo(sesion, semilla, *, cola: int | None, reportada: bool = True):
    """Un teléfono del vendedor que reporta (o no) su cola pendiente."""
    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, "
            "                          registrado_en, cola_pendiente, "
            "                          cola_reportada_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now(), :cola, "
            "        CASE WHEN :reportada THEN now() ELSE NULL END)"
        ),
        {"d": identificador, "u": semilla["vendedor"], "cola": cola,
         "reportada": reportada},
    )
    await sesion.commit()
    return identificador


async def test_un_equipo_con_cola_pendiente_bloquea_la_confirmacion(
    cliente, sesion, semilla, catalogo
):
    """El hecho lo reporta el teléfono, así que no es una sospecha."""
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=3)
    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)

    r = await _confirmar(cliente, carga)
    texto = solo_texto(r)
    assert "No se puede confirmar" in texto
    assert "3 operación(es) sin subir" in texto
    assert "§2.3" in texto

    # Y nada se movió: ni libro mayor, ni existencias, ni estado.
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar() == 0
    assert (
        await sesion.execute(text("SELECT estado FROM cargas"))
    ).scalar() == "borrador"
    assert (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a"),
            {"a": semilla["bodega"]},
        )
    ).scalar() == Decimal("480.000")


async def test_un_equipo_al_dia_no_bloquea_nada(cliente, sesion, semilla, catalogo):
    """Cero reportado es un dato, y deja pasar. Sin esto la regla sería un muro."""
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=0)
    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    await _confirmar(cliente, carga)

    assert (
        await sesion.execute(text("SELECT estado FROM cargas"))
    ).scalar() == "confirmada"
    # Y queda escrito que estaba al día: cero es un dato, no una ausencia.
    fila = (
        await sesion.execute(
            text("SELECT pendientes_al_confirmar, forzada FROM cargas")
        )
    ).mappings().one()
    assert fila["pendientes_al_confirmar"] == 0
    assert fila["forzada"] is False


async def test_la_cuarentena_pendiente_tambien_bloquea(
    cliente, sesion, semilla, catalogo
):
    """Son documentos que el servidor no pudo aplicar: del día anterior por
    definición, y cualquiera puede ser la venta que falta en el cierre de ayer."""
    await _entrar(cliente)
    equipo = await _equipo(sesion, semilla, cola=0)
    await sesion.execute(
        text(
            "INSERT INTO sync_cuarentena (operacion_id, dispositivo_id, usuario_id, "
            "        tipo, payload, hash_payload, error_codigo, error_mensaje, estado) "
            "VALUES (:o, :d, :u, 'venta', '{}'::jsonb, 'x', 'PAYLOAD_INVALIDO', "
            "        'renglón mal formado', 'pendiente')"
        ),
        {"o": uuid.uuid4(), "d": equipo, "u": semilla["vendedor"]},
    )
    await sesion.commit()

    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    texto = solo_texto(await _confirmar(cliente, carga))
    assert "cuarentena sin atender" in texto
    assert (
        await sesion.execute(text("SELECT estado FROM cargas"))
    ).scalar() == "borrador"


async def test_una_carga_anterior_sin_liquidar_bloquea(
    cliente, sesion, semilla, catalogo
):
    """No está en el §2.3 y se sigue de él: si el camión debe el conteo de un día
    previo, cargarlo hoy encima vuelve ese retorno incalculable."""
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=0)

    # Una carga confirmada de anteayer, sin liquidación.
    vieja = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "        vendedor_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (:id, 'CG-000999', :bod, :cam, :v, "
            "        CURRENT_DATE - 2, 'confirmada', now())"
        ),
        {"id": vieja, "bod": semilla["bodega"], "cam": semilla["camion"],
         "v": semilla["vendedor"]},
    )
    await sesion.commit()

    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    texto = solo_texto(await _confirmar(cliente, carga))
    assert "CG-000999" in texto
    assert "sin liquidación" in texto
    assert (
        await sesion.execute(
            text("SELECT estado FROM cargas WHERE id <> :vieja"), {"vieja": vieja}
        )
    ).scalar() == "borrador"


async def test_una_liquidacion_abierta_de_ayer_tambien_bloquea(
    cliente, sesion, semilla, catalogo
):
    """El caso que se escapa si el filtro solo mira 'confirmada'.

    Abrir el arqueo mueve la carga a 'en_ruta' (`liquidaciones.py`), así que la
    carga cuyo conteo SÍ empezó y nadie cerró es precisamente la que deja de
    estar en 'confirmada'. Y es la peor de las dos: ya hay un
    `efectivo_esperado` calculado que las ventas que falten por subir van a
    desmentir.
    """
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=0)

    vieja = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "        vendedor_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (:id, 'CG-000998', :bod, :cam, :v, "
            "        CURRENT_DATE - 1, 'en_ruta', now())"
        ),
        {"id": vieja, "bod": semilla["bodega"], "cam": semilla["camion"],
         "v": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (folio, carga_id, vendedor_id, "
            "        fecha_operativa, estado) "
            "VALUES ('LQ-000998', :c, :v, CURRENT_DATE - 1, 'abierta')"
        ),
        {"c": vieja, "v": semilla["vendedor"]},
    )
    await sesion.commit()

    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    texto = solo_texto(await _confirmar(cliente, carga))
    assert "CG-000998" in texto
    assert "abierta" in texto
    assert (
        await sesion.execute(
            text("SELECT estado FROM cargas WHERE id <> :vieja"), {"vieja": vieja}
        )
    ).scalar() == "borrador"


async def test_una_carga_ya_liquidada_no_bloquea(cliente, sesion, semilla, catalogo):
    """El otro lado de la misma moneda: cerrar el arqueo deja pasar.

    Sin esta prueba, un filtro de más —mirar también 'liquidada'— bloquearía
    todos los días a partir del segundo, y la regla acabaría apagada a mano.
    """
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=0)

    vieja = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "        vendedor_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (:id, 'CG-000997', :bod, :cam, :v, "
            "        CURRENT_DATE - 1, 'liquidada', now())"
        ),
        {"id": vieja, "bod": semilla["bodega"], "cam": semilla["camion"],
         "v": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO liquidaciones (folio, carga_id, vendedor_id, "
            "        fecha_operativa, estado, cerrada_en) "
            "VALUES ('LQ-000997', :c, :v, CURRENT_DATE - 1, 'cerrada', now())"
        ),
        {"c": vieja, "v": semilla["vendedor"]},
    )
    await sesion.commit()

    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    await _confirmar(cliente, carga)
    assert (
        await sesion.execute(
            text("SELECT estado, forzada FROM cargas WHERE id = :c"), {"c": carga}
        )
    ).first() == ("confirmada", False)


async def test_forzar_exige_casilla_Y_motivo(cliente, sesion, semilla, catalogo):
    """Marcar la casilla sin escribir por qué no alcanza: la constancia es el
    texto, no el clic."""
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=2)
    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    detalle = await cliente.get(f"/panel/cargas/{carga}")

    for datos in (
        {"confirmo_pendientes": "1", "motivo_forzado": "   "},
        {"confirmo_pendientes": "", "motivo_forzado": "el camión tiene que salir"},
    ):
        r = await cliente.post(
            f"/panel/cargas/{carga}/confirmar",
            data={"csrf": _csrf(cliente, detalle), **datos},
            follow_redirects=True,
        )
        assert "No se puede confirmar" in solo_texto(r)
    assert (
        await sesion.execute(text("SELECT estado FROM cargas"))
    ).scalar() == "borrador"


async def test_forzar_con_motivo_pasa_y_queda_en_la_auditoria(
    cliente, sesion, semilla, catalogo
):
    """A las 6 am el camión tiene que salir, y una regla que deja la ruta en la
    bodega se desactiva a la semana. Lo que no puede pasar es forzar en silencio.
    """
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=2)
    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    detalle = await cliente.get(f"/panel/cargas/{carga}")

    r = await cliente.post(
        f"/panel/cargas/{carga}/confirmar",
        data={
            "csrf": _csrf(cliente, detalle),
            "confirmo_pendientes": "1",
            "motivo_forzado": "el teléfono de Juan no prende y la ruta no puede quedarse",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    fila = (
        await sesion.execute(
            text("SELECT estado, pendientes_al_confirmar, forzada FROM cargas")
        )
    ).mappings().one()
    assert fila["estado"] == "confirmada"
    assert fila["pendientes_al_confirmar"] == 2
    assert fila["forzada"] is True

    # La constancia: en `auditoria`, con los bloqueos que había y no solo con que
    # hubo. Dentro de un mes la pregunta no es «¿se forzó?» sino «¿a pesar de qué?».
    auditado = (
        await sesion.execute(
            text(
                "SELECT entidad, accion, usuario_id, motivo, datos_despues "
                "  FROM auditoria WHERE entidad = 'carga'"
            )
        )
    ).mappings().one()
    assert auditado["accion"] == "carga_forzada"
    assert auditado["usuario_id"] == semilla["admin"]
    assert "no prende" in auditado["motivo"]
    assert auditado["datos_despues"]["pendientes_al_confirmar"] == 2
    assert len(auditado["datos_despues"]["bloqueos"]) == 1
    assert "sin subir" in auditado["datos_despues"]["bloqueos"][0]


async def test_la_razon_del_forzado_no_viaja_al_telefono(
    cliente, sesion, semilla, catalogo
):
    """`cargas` lleva disparador de change_log y publica la FILA COMPLETA.

    Por eso la razón vive en `auditoria`: un texto donde la oficina escribe «el
    teléfono de Juan no prende» acabaría en el SQLite de Juan. Es el mismo camino
    de fuga que la 0027 evitó con el costo.
    """
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=2)
    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga)
    detalle = await cliente.get(f"/panel/cargas/{carga}")
    await cliente.post(
        f"/panel/cargas/{carga}/confirmar",
        data={
            "csrf": _csrf(cliente, detalle),
            "confirmo_pendientes": "1",
            "motivo_forzado": "el teléfono de Juan no prende",
        },
        follow_redirects=True,
    )

    publicado = (
        await sesion.execute(
            text(
                # El cursor del pull es `cursor` (un BIGSERIAL), no `id`: ver
                # la migración 0007.
                "SELECT payload FROM change_log "
                " WHERE entidad = 'carga' ORDER BY cursor DESC LIMIT 1"
            )
        )
    ).scalar()
    assert publicado is not None, "la carga confirmada sí se publica"
    crudo = str(publicado)
    assert "no prende" not in crudo
    assert "forzada_motivo" not in crudo
    # Y `auditoria` NO se publica: es la otra mitad de la misma defensa.
    assert (
        await sesion.execute(
            text(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                " WHERE NOT t.tgisinternal AND c.relname = 'auditoria'"
            )
        )
    ).scalar() == 0


async def test_el_borrador_avisa_sin_bloquear(cliente, sesion, semilla, catalogo):
    """El borrador no mueve nada y el teléfono puede sincronizar en el patio
    mientras alguien captura: bloquear la creación detendría trabajo que muy
    seguido se vuelve innecesario."""
    await _entrar(cliente)
    await _equipo(sesion, semilla, cola=4)

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
    # El borrador SÍ se creó...
    assert r.status_code == 303
    assert (await sesion.execute(text("SELECT count(*) FROM cargas"))).scalar() == 1
    # ...y avisa de lo que va a impedir el confirmar.
    texto = solo_texto(await cliente.get(r.headers["location"]))
    assert "no se va a poder confirmar" in texto

    # Y si el teléfono sincroniza mientras se captura, el bloqueo desaparece solo.
    carga = r.headers["location"].split("/panel/cargas/")[1].split("?")[0]
    await _agregar(cliente, carga)
    await sesion.execute(
        text("UPDATE dispositivos SET cola_pendiente = 0, cola_reportada_en = now()")
    )
    await sesion.commit()
    await _confirmar(cliente, carga)
    assert (
        await sesion.execute(text("SELECT estado FROM cargas"))
    ).scalar() == "confirmada"


async def test_la_carga_confirmada_dice_cuanto_queda_en_el_camion(
    cliente, semilla, sesion, catalogo
):
    """«Vendo y el camión del panel nunca baja» (octubre 2026).

    La carga es un documento: lo que se cargó no se mueve. Pero quien la abría a
    mediodía veía las 240 piezas de la mañana y concluía que la venta no había
    bajado el camión. Ahora la carga confirmada trae al lado lo que el camión trae
    AHORA, con lo vendido descontado.
    """
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga, cantidad="10")
    await _confirmar(cliente, carga)

    # Una venta de 48 piezas descontada del camión, como lo hace la ingesta.
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = cantidad - 48 "
            " WHERE almacen_id = :c AND producto_id = :p"
        ),
        {"c": semilla["camion"], "p": catalogo["producto"]},
    )
    await sesion.commit()

    plano = solo_texto(await cliente.get(f"/panel/cargas/{carga}"))
    assert "En el camión ahora" in plano
    assert "Se cargó" in plano
    # 10 cajas de 24 = 240 cargadas; menos 48 vendidas = 192.
    assert "192" in plano


# ===========================================================================
# Cargar varios a la vez, desde lo que hay en la bodega (octubre 2026)
# ===========================================================================
@pytest.fixture
async def otro_producto(sesion, semilla) -> uuid.UUID:
    """Galletas: caja de 12, 60 piezas en bodega. Y un tercero SIN existencia."""
    galletas, sin_existencia = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) VALUES "
            "(:g, 'GALL-200', 'Galletas 200 g', 'PZA', 0.16), "
            "(:s, 'AGOT-1', 'Producto agotado', 'PZA', 0)"
        ),
        {"g": galletas, "s": sin_existencia},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:g, 'PZA', 1, true), (:g, 'CAJA', 12, false), "
            "       (:s, 'PZA', 1, true)"
        ),
        {"g": galletas, "s": sin_existencia},
    )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:b, :g, 60), (:b, :s, 0)"
        ),
        {"b": semilla["bodega"], "g": galletas, "s": sin_existencia},
    )
    await sesion.commit()
    return galletas


async def _agregar_varios(cliente, carga_id, campos: dict):
    detalle = await cliente.get(f"/panel/cargas/{carga_id}")
    return await cliente.post(
        f"/panel/cargas/{carga_id}/renglones",
        data={"csrf": _csrf(cliente, detalle), **campos},
        follow_redirects=True,
    )


async def _renglones_de(sesion, carga_id) -> dict:
    return {
        f["sku"]: f["cantidad"]
        for f in (
            await sesion.execute(
                text(
                    "SELECT p.sku, d.cantidad FROM carga_detalle d "
                    "  JOIN productos p ON p.id = d.producto_id "
                    " WHERE d.carga_id = :c"
                ),
                {"c": uuid.UUID(carga_id)},
            )
        ).mappings().all()
    }


async def test_la_carga_muestra_todo_lo_que_hay_en_la_bodega(
    cliente, semilla, catalogo, otro_producto
):
    """Sin teclear un solo SKU: lo que se puede subir es lo que hay en el anaquel."""
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    html = (await cliente.get(f"/panel/cargas/{carga}")).text

    assert f'name="cantidad_{catalogo["producto"]}"' in html
    assert f'name="cantidad_{otro_producto}"' in html
    # Lo que la bodega tiene en cero no se ofrece: no hay de dónde subirlo.
    assert "Producto agotado" not in html


async def test_varios_productos_se_agregan_con_un_solo_boton(
    cliente, semilla, sesion, catalogo, otro_producto
):
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    await _agregar_varios(
        cliente,
        carga,
        {
            f"cantidad_{catalogo['producto']}": "10",
            f"unidad_{catalogo['producto']}": "CAJA",
            f"cantidad_{otro_producto}": "3",
            f"unidad_{otro_producto}": "CAJA",
        },
    )

    renglones = await _renglones_de(sesion, carga)
    # 10 cajas de 24 y 3 cajas de 12, convertidas una sola vez a piezas.
    assert renglones == {"ATUN-140": Decimal("240.000"), "GALL-200": Decimal("36.000")}


async def test_los_renglones_vacios_no_se_tocan(
    cliente, semilla, sesion, catalogo, otro_producto
):
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    await _agregar_varios(
        cliente,
        carga,
        {
            f"cantidad_{catalogo['producto']}": "2",
            f"unidad_{catalogo['producto']}": "CAJA",
            f"cantidad_{otro_producto}": "",
            f"unidad_{otro_producto}": "CAJA",
        },
    )
    assert await _renglones_de(sesion, carga) == {"ATUN-140": Decimal("48.000")}


async def test_un_renglon_con_error_no_tumba_a_los_demas(
    cliente, semilla, sesion, catalogo, otro_producto
):
    """Perder veinte cantidades tecleadas por un dedazo en una sería peor que el
    dedazo: se guardan las buenas y se nombra la mala."""
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    r = await _agregar_varios(
        cliente,
        carga,
        {
            f"cantidad_{catalogo['producto']}": "10",
            f"unidad_{catalogo['producto']}": "CAJA",
            f"cantidad_{otro_producto}": "2.5",
            f"unidad_{otro_producto}": "CAJA",
        },
    )
    plano = solo_texto(r)

    assert await _renglones_de(sesion, carga) == {"ATUN-140": Decimal("240.000")}
    assert "NO entraron" in plano
    assert "Galletas 200 g" in plano


async def test_la_presentacion_por_omision_es_la_caja_no_la_de_vender(
    cliente, semilla, catalogo
):
    """`es_default` es la de VENDER (pieza). Quien carga escribe «10» pensando en
    cajas; con la pieza por omisión subiría diez piezas."""
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    html = (await cliente.get(f"/panel/cargas/{carga}")).text
    selector = html[html.index(f'name="unidad_{catalogo["producto"]}"') :]
    selector = selector[: selector.index("</select>")]
    assert '<option value="CAJA" selected>' in selector


async def test_agregar_otra_vez_suma_y_la_tabla_dice_cuanto_ya_va(
    cliente, semilla, sesion, catalogo
):
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    campos = {
        f"cantidad_{catalogo['producto']}": "5",
        f"unidad_{catalogo['producto']}": "CAJA",
    }
    await _agregar_varios(cliente, carga, campos)
    await _agregar_varios(cliente, carga, campos)

    assert await _renglones_de(sesion, carga) == {"ATUN-140": Decimal("240.000")}


async def test_el_filtro_acota_la_lista(cliente, semilla, catalogo, otro_producto):
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    html = (await cliente.get(f"/panel/cargas/{carga}?buscar=GALL")).text
    assert f'name="cantidad_{otro_producto}"' in html
    assert f'name="cantidad_{catalogo["producto"]}"' not in html


async def test_una_carga_confirmada_no_acepta_mas(
    cliente, semilla, sesion, catalogo
):
    await _entrar(cliente)
    carga = await _abrir(cliente, semilla)
    await _agregar(cliente, carga, cantidad="1")
    await _confirmar(cliente, carga)

    html = (await cliente.get(f"/panel/cargas/{carga}")).text
    assert 'id="forma_varios"' not in html
    r = await cliente.post(
        f"/panel/cargas/{carga}/renglones",
        data={
            "csrf": _csrf(cliente),
            f"cantidad_{catalogo['producto']}": "5",
            f"unidad_{catalogo['producto']}": "CAJA",
        },
        follow_redirects=True,
    )
    assert "ya salió de la bodega" in solo_texto(r)
    assert await _renglones_de(sesion, carga) == {"ATUN-140": Decimal("24.000")}
