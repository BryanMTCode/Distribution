"""Recibir en la bodega lo que bajó de un camión.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA NO ES UN BOTÓN DE «ACEPTAR»
────────────────────────────────────────────────────────────────────────────
La versión fácil de esta función habría sido un botón: el vendedor declara 18
cajas, alguien en la oficina da clic en «aceptar», y la bodega sube 18. Con eso,
un vendedor con un faltante de 18 cajas podría capturar una devolución, no
entregar nada y cuadrar — su camión baja, la bodega sube, y nadie contó nada.

Así que esta pantalla **cuenta**, y lo que entra a la bodega es lo contado. Lo que
no se contó se queda en tránsito: una diferencia con nombre y con fecha en vez de
una confianza invisible.

Lo que estas pruebas defienden:

1. **Lo que entra es lo contado, no lo declarado.** Si falla, el panel vuelve a
   ser un botón de «le creo».
2. **Lo que falta se queda en tránsito.** No se ajusta solo ni desaparece.
3. **Contar cero es una respuesta válida** y no deja asiento de inventario.
4. **Recibir dos veces no mete la mercancía dos veces** (doble clic).
5. **No se puede recibir «en un camión»** (§0.2), ni con un documento legítimo.
6. **Gerencia no recibe**: quien mide no es quien ajusta (ADR 0002 §0).
7. **El vendedor se entera**: el cambio de estado publica un delta a su teléfono.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import Contexto, obtener_manejador
from tests.conftest import PASSWORD_VENDEDOR, solo_texto

MOMENTO = "2026-10-06T19:15:00.000Z"
DIA = "2026-10-06"


# ===========================================================================
# Andamios
# ===========================================================================
async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


def _csrf(cliente) -> str:
    import hashlib
    import hmac

    from app.core.config import obtener_config

    cookie = cliente.cookies.get("dsd_panel", "")
    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cookie}".encode(),
        hashlib.sha256,
    ).hexdigest()


@pytest.fixture
async def devolucion(sesion, semilla) -> dict:
    """Una devolución ya declarada por el teléfono: 18 piezas en tránsito.

    Se crea con el manejador de sincronización de verdad, no a mano: lo que esta
    pantalla recibe es exactamente lo que el teléfono manda, y un andamio que
    inserte las filas por su cuenta podría estar probando una forma del documento
    que ya no existe.
    """
    dispositivo = uuid.uuid4()
    producto = uuid.uuid4()
    otro = uuid.uuid4()

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    for identificador, sku, nombre in (
        (producto, "ATUN-140", "Atún en agua 140 g"),
        (otro, "GALL-200", "Galletas 200 g"),
    ):
        await sesion.execute(
            text(
                "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
                "VALUES (:p, :sku, :nom, 'PZA', 0.0000)"
            ),
            {"p": identificador, "sku": sku, "nom": nombre},
        )
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 48), (:a, :o, 10)"
        ),
        {"a": semilla["camion"], "p": producto, "o": otro},
    )
    await sesion.commit()

    traspaso_id = uuid.uuid4()
    await obtener_manejador("traspaso.crear")(
        sesion,
        Contexto(
            dispositivo_id=dispositivo,
            usuario_id=semilla["vendedor"],
            rutas=(semilla["ruta"],),
            almacen_id=semilla["camion"],
        ),
        traspaso_id,
        {
            "fecha_dispositivo": MOMENTO,
            "fecha_operativa": DIA,
            "observaciones": "Lo que no se vendió",
            "detalle": [
                {"producto_id": str(producto), "cantidad": "18.000"},
                {"producto_id": str(otro), "cantidad": "4.000"},
            ],
        },
    )
    await sesion.commit()

    transito = (
        await sesion.execute(
            text("SELECT id FROM almacenes WHERE tipo = 'transito'")
        )
    ).scalar_one()
    renglones = {
        f["sku"]: f["id"]
        for f in (
            await sesion.execute(
                text(
                    "SELECT d.id, p.sku FROM traspaso_detalle d "
                    "  JOIN productos p ON p.id = d.producto_id "
                    " WHERE d.traspaso_id = :t"
                ),
                {"t": traspaso_id},
            )
        ).mappings().all()
    }
    return {
        "id": traspaso_id,
        "dispositivo": dispositivo,
        "atun": producto,
        "galletas": otro,
        "transito": transito,
        "renglones": renglones,
    }


async def _recibir(cliente, devolucion, semilla, *, atun="18", galletas="4",
                   bodega=None):
    datos = {
        "csrf": _csrf(cliente),
        "almacen_destino_id": str(bodega if bodega is not None else semilla["bodega"]),
        f"contado_{devolucion['renglones']['ATUN-140']}": atun,
        f"contado_{devolucion['renglones']['GALL-200']}": galletas,
    }
    return await cliente.post(
        f"/panel/entradas/devolucion/{devolucion['id']}/recibir",
        data=datos,
        follow_redirects=False,
    )


async def _existencia(sesion, almacen, producto) -> Decimal:
    valor = (
        await sesion.execute(
            text(
                "SELECT cantidad FROM existencias "
                " WHERE almacen_id = :a AND producto_id = :p"
            ),
            {"a": almacen, "p": producto},
        )
    ).scalar()
    return Decimal(valor) if valor is not None else Decimal("0")


# ===========================================================================
# Lo que entra es lo contado
# ===========================================================================
async def test_lo_que_entra_a_la_bodega_es_lo_contado(
    cliente, sesion, semilla, devolucion
):
    """Declaró 18 y llegaron 16: entran 16.

    Si esto fallara, esta pantalla sería un botón de «le creo» y la devolución
    serviría para tapar un faltante.
    """
    await _entrar(cliente)
    r = await _recibir(cliente, devolucion, semilla, atun="16")
    assert r.status_code == 303, r.text

    assert await _existencia(sesion, semilla["bodega"], devolucion["atun"]) == Decimal(
        "16.000"
    )


async def test_lo_que_falto_se_queda_en_transito(cliente, sesion, semilla, devolucion):
    """Las 2 que no llegaron no se ajustan solas: quedan donde se puede preguntar."""
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla, atun="16")

    assert await _existencia(
        sesion, devolucion["transito"], devolucion["atun"]
    ) == Decimal("2.000")


async def test_la_diferencia_queda_escrita_renglon_por_renglon(
    cliente, sesion, semilla, devolucion
):
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla, atun="16")

    fila = (
        await sesion.execute(
            text(
                "SELECT cantidad, cantidad_recibida FROM traspaso_detalle "
                " WHERE id = :id"
            ),
            {"id": devolucion["renglones"]["ATUN-140"]},
        )
    ).mappings().one()
    # Lo declarado NO se sobrescribe. La diferencia entre las dos es el dato.
    assert Decimal(fila["cantidad"]) == Decimal("18.000")
    assert Decimal(fila["cantidad_recibida"]) == Decimal("16.000")


async def test_contar_mas_de_lo_declarado_tambien_entra(
    cliente, sesion, semilla, devolucion
):
    """El vendedor bajó una caja que no anotó. Es un hecho físico (§0.1)."""
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla, atun="20")

    assert await _existencia(sesion, semilla["bodega"], devolucion["atun"]) == Decimal(
        "20.000"
    )
    # El tránsito queda en negativo, que es la señal honesta de lo que pasó.
    assert await _existencia(
        sesion, devolucion["transito"], devolucion["atun"]
    ) == Decimal("-2.000")


async def test_contar_cero_es_una_respuesta_valida(cliente, sesion, semilla, devolucion):
    """«Dijo que traía 18 y no llegó nada» no se captura rechazando el documento.

    Rechazarlo le devolvería 18 piezas a un camión que ya no las trae. Contar cero
    las deja en tránsito, que es donde están: en ningún lado.
    """
    await _entrar(cliente)
    r = await _recibir(cliente, devolucion, semilla, atun="0", galletas="0")
    assert r.status_code == 303

    assert await _existencia(sesion, semilla["bodega"], devolucion["atun"]) == Decimal(
        "0"
    )
    assert await _existencia(
        sesion, devolucion["transito"], devolucion["atun"]
    ) == Decimal("18.000")
    # Un renglón en cero no deja asiento: no se movió nada.
    assert (
        await sesion.execute(
            text(
                "SELECT count(*) FROM movimientos_inventario "
                " WHERE documento_id = :d AND almacen_origen_id = :t"
            ),
            {"d": devolucion["id"], "t": devolucion["transito"]},
        )
    ).scalar_one() == 0
    # Y el documento SÍ queda cerrado: alguien fue, contó y no había nada.
    estado = (
        await sesion.execute(
            text("SELECT estado FROM traspasos WHERE id = :t"), {"t": devolucion["id"]}
        )
    ).scalar_one()
    assert estado == "aceptado"


async def test_un_renglon_en_blanco_se_rechaza(cliente, sesion, semilla, devolucion):
    """En blanco no se sabe si alguien contó cero o si nadie contó."""
    await _entrar(cliente)
    r = await _recibir(cliente, devolucion, semilla, atun="")
    assert r.status_code == 303
    assert "error=" in r.headers["location"]

    estado = (
        await sesion.execute(
            text("SELECT estado FROM traspasos WHERE id = :t"), {"t": devolucion["id"]}
        )
    ).scalar_one()
    assert estado == "propuesto"
    assert await _existencia(sesion, semilla["bodega"], devolucion["atun"]) == Decimal(
        "0"
    )


# ===========================================================================
# El asiento: quién bajó y quién recibió
# ===========================================================================
async def test_el_asiento_va_de_transito_a_la_bodega(
    cliente, sesion, semilla, devolucion
):
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla)

    movimientos = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, almacen_destino_id, cantidad, "
                "       usuario_id "
                "  FROM movimientos_inventario "
                " WHERE documento_id = :d AND almacen_origen_id = :t "
                " ORDER BY cantidad DESC"
            ),
            {"d": devolucion["id"], "t": devolucion["transito"]},
        )
    ).mappings().all()

    assert len(movimientos) == 2
    assert all(m["almacen_destino_id"] == semilla["bodega"] for m in movimientos)
    assert all(m["tipo"] == "traspaso" for m in movimientos)
    # Quién recibió: la otra mitad de la pregunta que este documento contesta.
    assert all(m["usuario_id"] == semilla["admin"] for m in movimientos)


async def test_queda_quien_recibio_y_cuando(cliente, sesion, semilla, devolucion):
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla)

    fila = (
        await sesion.execute(
            text(
                "SELECT estado, resuelto_por, resuelto_en FROM traspasos WHERE id = :t"
            ),
            {"t": devolucion["id"]},
        )
    ).mappings().one()
    assert fila["estado"] == "aceptado"
    assert fila["resuelto_por"] == semilla["admin"]
    assert fila["resuelto_en"] is not None


async def test_recibir_dos_veces_no_mete_la_mercancia_dos_veces(
    cliente, sesion, semilla, devolucion
):
    """Doble clic en una pantalla lenta."""
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla)
    r = await _recibir(cliente, devolucion, semilla)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]

    assert await _existencia(sesion, semilla["bodega"], devolucion["atun"]) == Decimal(
        "18.000"
    )


# ===========================================================================
# §0.2 · la oficina no le escribe el inventario a un camión
# ===========================================================================
async def test_no_se_puede_recibir_en_un_camion(cliente, sesion, semilla, devolucion):
    """Un documento legítimo tampoco es una puerta para escribir un camión."""
    await _entrar(cliente)
    r = await _recibir(cliente, devolucion, semilla, bodega=semilla["camion"])
    assert r.status_code == 303
    assert "error=" in r.headers["location"]

    estado = (
        await sesion.execute(
            text("SELECT estado FROM traspasos WHERE id = :t"), {"t": devolucion["id"]}
        )
    ).scalar_one()
    assert estado == "propuesto"


# ===========================================================================
# Quién puede recibir
# ===========================================================================
async def _gerente(sesion, semilla, *, revocado: bool = False) -> uuid.UUID:
    """Un gerente, con la opción de quitarle `inventario.ajustar` por persona.

    La revocación NO se hace borrando el renglón del rol: `roles_permisos` son datos
    de referencia que las migraciones siembran y que ninguna prueba vacía, así que
    un DELETE ahí dejaría a todas las pruebas que corran después midiendo un sistema
    con otra política. Ya pasó una vez. `usuarios_permisos` es la excepción POR
    PERSONA que el sistema ya soporta (`otorgado = false`), y sí se vacía entre
    pruebas.
    """
    from app.core.seguridad import hashear_password

    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, "
            "rol_codigo, creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {
            "id": identificador,
            "s": semilla["sucursal"],
            "h": hashear_password(PASSWORD_VENDEDOR),
        },
    )
    if revocado:
        await sesion.execute(
            text(
                "INSERT INTO usuarios_permisos (usuario_id, permiso_codigo, otorgado) "
                "VALUES (:u, 'inventario.ajustar', false)"
            ),
            {"u": identificador},
        )
    await sesion.commit()
    return identificador


async def test_GERENCIA_SI_RECIBE(cliente, sesion, semilla, devolucion):
    """Decisión de la dirección, octubre 2026: la 0031 le dio `inventario.ajustar`.

    Durante meses la política fue la contraria —quien mide no es quien ajusta, ADR
    0002 §0— y esta prueba existe para que el cambio sea una decisión escrita y no
    una regresión silenciosa. La separación sigue donde más importa: el cierre de la
    liquidación, que gerencia no puede firmar.
    """
    await _gerente(sesion, semilla)
    await _entrar(cliente, "GER01")
    r = await _recibir(cliente, devolucion, semilla)
    assert r.status_code == 303, r.text

    assert await _existencia(sesion, semilla["bodega"], devolucion["atun"]) == Decimal(
        "18.000"
    )


async def test_sin_el_permiso_no_se_recibe(cliente, sesion, semilla, devolucion):
    """La guarda existe y muerde, aunque hoy los tres roles del panel la pasen.

    Recibir mercancía crea inventario desde el punto de vista del sistema, y es una
    de las operaciones con las que se puede tapar un faltante: el día que la oficina
    le quite el permiso a alguien, tiene que dejar de poder.
    """
    await _gerente(sesion, semilla, revocado=True)
    await _entrar(cliente, "GER01")
    r = await _recibir(cliente, devolucion, semilla)
    assert r.status_code == 403, r.text

    assert await _existencia(sesion, semilla["bodega"], devolucion["atun"]) == Decimal(
        "0"
    )


# ===========================================================================
# Que el vendedor se entere
# ===========================================================================
async def test_el_vendedor_recibe_el_delta_con_lo_contado(
    cliente, sesion, semilla, devolucion
):
    """Su comprobante de que la mercancía dejó de ser su responsabilidad.

    Y acotado a él: el traspaso de Juan no le importa al teléfono de Pedro.
    """
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla, atun="16")

    fila = (
        await sesion.execute(
            text(
                "SELECT vendedor_id, payload FROM change_log "
                " WHERE entidad = 'traspaso' AND entidad_id = :t "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"t": devolucion["id"]},
        )
    ).mappings().first()

    assert fila is not None, "el vendedor no se enteraría nunca"
    assert fila["vendedor_id"] == semilla["vendedor"]
    payload = fila["payload"]
    assert payload["estado"] == "aceptado"
    assert payload["folio"].startswith("TR-")
    # Cuándo se recibió: es la fecha que la app le muestra como «recibida el…».
    assert payload["resuelto_en"] is not None
    renglon = next(
        d for d in payload["detalle"] if d["producto_id"] == str(devolucion["atun"])
    )
    # Cantidades como texto con tres decimales: contracts §1.4.
    assert renglon["cantidad"] == "18.000"
    assert renglon["cantidad_recibida"] == "16.000"


# ===========================================================================
# La pantalla lo dice
# ===========================================================================
async def test_entradas_avisa_de_lo_que_falta_por_contar(cliente, semilla, devolucion):
    """Una devolución sin recibir es mercancía que no está en el inventario.

    El mismo defecto que un borrador sin confirmar: una carga hecha con esa cifra
    deja la bodega en negativo.
    """
    await _entrar(cliente)
    r = await cliente.get("/panel/entradas")
    texto = solo_texto(r)

    assert "devolución(es) de camión por recibir" in texto
    assert "TR-" in texto


async def test_la_pantalla_de_la_devolucion_muestra_lo_declarado(
    cliente, semilla, devolucion
):
    await _entrar(cliente)
    r = await cliente.get(f"/panel/entradas/devolucion/{devolucion['id']}")
    texto = solo_texto(r)

    assert "Atún en agua 140 g" in texto
    assert "Galletas 200 g" in texto
    assert "tránsito" in texto
    assert "Juan Pérez" in texto


async def test_despues_de_recibir_la_pantalla_muestra_la_diferencia(
    cliente, semilla, devolucion
):
    await _entrar(cliente)
    await _recibir(cliente, devolucion, semilla, atun="16")

    r = await cliente.get(f"/panel/entradas/devolucion/{devolucion['id']}")
    texto = solo_texto(r)

    assert "Recibida" in texto
    assert "no cuadraron" in texto
    assert "sigue en tránsito" in texto
