"""Entradas de mercancía: la puerta por la que el inventario existe.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTAS PRUEBAS EXISTEN
────────────────────────────────────────────────────────────────────────────
El hueco que cierra esta pantalla no rompía ninguna prueba, y por eso duró: el
libro mayor contemplaba 'compra' y 'ajuste' desde la migración 0004, el permiso
`inventario.ajustar` existe desde la 0009, y ningún código lo consultaba. El
motor estaba y la puerta no.

Peor: cargar un camión desde una bodega sin existencia **funciona**
—`existencias` no lleva CHECK de signo a propósito (§0.1)— así que el sistema no
se quejaba. Dejaba la bodega en negativo.

Lo que estas pruebas defienden:

1. **El borrador no mueve nada.** Capturar quince renglones toma veinte minutos;
   un sistema que mueve al primer renglón deja la bodega a medio recibir.
2. **Confirmar es idempotente por estado.** Un doble clic duplicaría una
   remisión completa, y el sobrante aparecería semanas después en un conteo.
3. **El asiento y la caché van en la misma transacción**, y suman lo mismo.
4. **La mercancía nunca entra directo a un camión** (§0.2).
5. **Lo confirmado no se edita ni se cancela**: se compensa.
6. **La conversión caja→pieza se hace una vez**, con la función del dominio.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.entradas import MOTIVOS, tipo_de_movimiento
from tests.conftest import PASSWORD_VENDEDOR, solo_texto

# Sin `pytestmark = pytest.mark.asyncio`: `asyncio_mode = "auto"` ya está en
# pyproject.toml, y la marca a nivel de módulo se aplicaría también a las dos
# pruebas puras de este archivo —las del mapeo motivo→tipo— que no son
# corrutinas. Son 36 avisos de pytest por una línea que no hace falta.


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
    """El token, calculado de la cookie igual que lo hace el servidor.

    Lo obvio era rasparlo del HTML, y no sirve en dos casos que estas pruebas
    necesitan: una entrada ya confirmada no dibuja ningún formulario —no hay
    nada editable— y un rol sin `inventario.ajustar` tampoco. En los dos, la
    página no trae token y el raspado se cae por una razón que no tiene nada que
    ver con lo que se está probando.

    Es HMAC(secreto, "csrf:" + cookie), la misma fórmula de
    `sesion_web.token_csrf`. Si esa fórmula cambia, estas pruebas se caen — y es
    correcto que se caigan: significaría que el panel cambió su defensa.
    """
    import hashlib
    import hmac

    from app.core.config import obtener_config

    cookie = cliente.cookies.get("dsd_panel", "")
    return hmac.new(
        obtener_config().jwt_secreto.encode(),
        f"csrf:{cookie}".encode(),
        hashlib.sha256,
    ).hexdigest()


async def _producto(sesion, *, sku: str = "COCA600", factor: int = 24,
                    maneja_lote: bool = False) -> uuid.UUID:
    """Un producto con su presentación de caja. Lo mínimo para recibir algo."""
    categoria = (
        await sesion.execute(text("SELECT id FROM categorias LIMIT 1"))
    ).scalar()
    if categoria is None:
        categoria = uuid.uuid4()
        await sesion.execute(
            text("INSERT INTO categorias (id, codigo, nombre) VALUES (:i,'ABA','Abarrotes')"),
            {"i": categoria},
        )
    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, categoria_id, unidad_base, "
            "                       maneja_lote, creado_en, actualizado_en) "
            "VALUES (:i, :sku, :nom, :c, 'PZA', :lote, now(), now())"
        ),
        {"i": identificador, "sku": sku, "nom": f"Producto {sku}",
         "c": categoria, "lote": maneja_lote},
    )
    # `producto_unidades` tiene llave compuesta (producto_id, unidad_codigo) y
    # no columna `id`. Lo descubrió este andamio al primer intento.
    for codigo, f in (("PZA", 1), ("CAJA", factor)):
        await sesion.execute(
            text(
                "INSERT INTO producto_unidades (producto_id, unidad_codigo, "
                "                               factor, es_default, activo) "
                "VALUES (:p, :u, :f, :pred, true)"
            ),
            {"p": identificador, "u": codigo, "f": f, "pred": codigo == "CAJA"},
        )
    await sesion.commit()
    return identificador


async def _abrir(cliente, semilla, *, motivo: str = "compra", nota: str = "",
                 almacen=None, fecha: str = ""):
    csrf = _csrf(cliente)
    return await cliente.post(
        "/panel/entradas/nueva",
        data={
            "csrf": csrf,
            "motivo": motivo,
            "almacen_destino_id": str(almacen or semilla["bodega"]),
            "proveedor": "Abarrotes del Centro",
            "referencia": "F-45821",
            "fecha": fecha,
            "nota": nota,
        },
        follow_redirects=False,
    )


def _id_de(respuesta) -> uuid.UUID:
    destino = respuesta.headers["location"]
    return uuid.UUID(destino.split("/panel/entradas/")[1].split("?")[0])


async def _renglon(cliente, entrada_id, *, sku="COCA600", unidad="CAJA",
                   cantidad="10", lote="", caducidad=""):
    csrf = _csrf(cliente)
    return await cliente.post(
        f"/panel/entradas/{entrada_id}/renglon",
        data={"csrf": csrf, "producto": sku, "unidad_codigo": unidad,
              "cantidad": cantidad, "lote": lote, "caducidad": caducidad},
        follow_redirects=False,
    )


async def _confirmar(cliente, entrada_id):
    csrf = _csrf(cliente)
    return await cliente.post(
        f"/panel/entradas/{entrada_id}/confirmar",
        data={"csrf": csrf}, follow_redirects=False,
    )


async def _existencia(sesion, almacen, producto) -> Decimal:
    valor = (
        await sesion.execute(
            text("SELECT cantidad FROM existencias WHERE almacen_id = :a "
                 "  AND producto_id = :p"),
            {"a": almacen, "p": producto},
        )
    ).scalar()
    return Decimal(valor) if valor is not None else Decimal(0)


# ===========================================================================
# El motivo decide el tipo del asiento
# ===========================================================================
def test_cada_motivo_mapea_a_un_tipo_del_libro_mayor():
    """'inicial' y 'ajuste' comparten tipo: ninguno se le compró a nadie."""
    assert tipo_de_movimiento("compra") == "compra"
    assert tipo_de_movimiento("inicial") == "ajuste"
    assert tipo_de_movimiento("ajuste") == "ajuste"


def test_un_motivo_desconocido_no_se_adivina():
    """Devolver 'ajuste' por omisión dejaría pasar un dedazo como un ajuste
    silencioso de inventario."""
    with pytest.raises(ValueError, match="motivo de entrada desconocido"):
        tipo_de_movimiento("regalo")


async def test_los_tipos_que_usa_el_dominio_existen_en_el_libro_mayor(sesion):
    """Verificación de frescura: el CHECK de PostgreSQL es el que manda.

    Si alguien agrega un motivo con un tipo que el libro mayor no acepta, el
    INSERT fallaría al confirmar —en producción, con la remisión capturada— y no
    aquí.
    """
    definicion = (
        await sesion.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                " WHERE conrelid = 'movimientos_inventario'::regclass "
                "   AND conname = 'movimientos_inventario_tipo_check'"
            )
        )
    ).scalar_one()
    for motivo in MOTIVOS:
        assert f"'{tipo_de_movimiento(motivo)}'" in definicion, motivo


# ===========================================================================
# 1. El borrador no mueve nada
# ===========================================================================
async def test_un_borrador_con_renglones_no_mueve_inventario(cliente, sesion, semilla):
    await _entrar(cliente)
    producto = await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")

    assert await _existencia(sesion, semilla["bodega"], producto) == 0
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar() == 0
    # Y la pantalla lo dice, no lo deja suponer.
    texto = solo_texto(await cliente.get(f"/panel/entradas/{entrada}"))
    assert "todavía no mueve inventario" in texto


async def test_confirmar_escribe_el_asiento_y_la_existencia(cliente, sesion, semilla):
    """10 cajas de 24 = 240 piezas, y las dos tablas dicen 240."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")

    r = await _confirmar(cliente, entrada)
    assert r.status_code == 303

    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("240")
    asiento = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, almacen_destino_id, cantidad, "
                "       documento_tipo, documento_id, usuario_id "
                "  FROM movimientos_inventario"
            )
        )
    ).mappings().one()
    assert asiento["tipo"] == "compra"
    # Entra al sistema desde fuera: no hay almacén de origen.
    assert asiento["almacen_origen_id"] is None
    assert asiento["almacen_destino_id"] == semilla["bodega"]
    assert asiento["cantidad"] == Decimal("240.000")
    # El asiento apunta a su documento: es lo que contesta "¿de dónde salieron?".
    assert asiento["documento_tipo"] == "entrada"
    assert asiento["documento_id"] == entrada
    assert asiento["usuario_id"] == semilla["admin"]


async def test_la_cache_y_el_libro_mayor_suman_lo_mismo(cliente, sesion, semilla):
    """Si divergieran, el job de reconciliación nocturno estaría arreglando un
    error evitable. Van en la misma transacción."""
    await _entrar(cliente)
    await _producto(sesion, sku="COCA600")
    await _producto(sesion, sku="SABRITAS", factor=12)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, sku="COCA600", cantidad="10")
    await _renglon(cliente, entrada, sku="SABRITAS", cantidad="5")
    await _confirmar(cliente, entrada)

    descuadre = (
        await sesion.execute(
            text(
                """
                SELECT count(*) FROM (
                    SELECT e.producto_id, e.cantidad AS cache,
                           COALESCE(sum(m.cantidad), 0) AS mayor
                      FROM existencias e
                      LEFT JOIN movimientos_inventario m
                             ON m.producto_id = e.producto_id
                            AND m.almacen_destino_id = e.almacen_id
                     WHERE e.almacen_id = :a
                     GROUP BY e.producto_id, e.cantidad
                ) x WHERE cache <> mayor
                """
            ),
            {"a": semilla["bodega"]},
        )
    ).scalar()
    assert descuadre == 0


# ===========================================================================
# 2. Confirmar dos veces no mete la mercancía dos veces
# ===========================================================================
async def test_confirmar_dos_veces_es_idempotente(cliente, sesion, semilla):
    """Un doble clic en una pantalla lenta duplicaría una remisión completa, y
    el sobrante solo aparecería en el siguiente conteo físico."""
    await _entrar(cliente)
    producto = await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")

    assert (await _confirmar(cliente, entrada)).status_code == 303
    segunda = await _confirmar(cliente, entrada)
    assert "ya%20est%C3%A1%20confirmada" in segunda.headers["location"]

    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("240")
    assert (
        await sesion.execute(text("SELECT count(*) FROM movimientos_inventario"))
    ).scalar() == 1


# ===========================================================================
# 3. La mercancía nueva no entra directo a un camión (§0.2)
# ===========================================================================
async def test_no_se_puede_recibir_mercancia_en_un_camion(cliente, sesion, semilla):
    """Le cambiaría el inventario bajo los pies a un vendedor que está vendiendo
    offline con otra cifra, y el descuadre le saldría en su liquidación como un
    sobrante del que no sabe nada."""
    await _entrar(cliente)
    r = await _abrir(cliente, semilla, almacen=semilla["camion"])
    assert r.status_code == 303
    destino = r.headers["location"]
    assert destino.startswith("/panel/entradas?")
    assert (await sesion.execute(text("SELECT count(*) FROM entradas"))).scalar() == 0
    # El mensaje dice el camino correcto, no solo que no se puede.
    texto = solo_texto(await cliente.get(destino))
    assert "no una bodega" in texto
    assert "sube al camión con una carga" in texto


async def test_el_formulario_solo_ofrece_bodegas(cliente, sesion, semilla):
    """Y no es cosmético: ofrecer el camión sería invitar al error que la prueba
    anterior rechaza."""
    await _entrar(cliente)
    r = await cliente.get("/panel/entradas")
    assert str(semilla["bodega"]) in r.text
    assert str(semilla["camion"]) not in r.text


# ===========================================================================
# 4. Lo confirmado no se edita ni se cancela
# ===========================================================================
async def test_no_se_agregan_renglones_a_una_entrada_confirmada(
    cliente, sesion, semilla
):
    await _entrar(cliente)
    await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")
    await _confirmar(cliente, entrada)

    r = await _renglon(cliente, entrada, cantidad="5")
    assert "ya%20est%C3%A1%20confirmada" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM entrada_detalle"))
    ).scalar() == 1


async def test_no_se_cancela_una_entrada_confirmada(cliente, sesion, semilla):
    """Cancelarla obligaría a restar existencias por un camino que no es un
    documento, y el libro mayor quedaría con una entrada sin contraparte."""
    await _entrar(cliente)
    await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")
    await _confirmar(cliente, entrada)

    csrf = _csrf(cliente)
    r = await cliente.post(
        f"/panel/entradas/{entrada}/cancelar",
        data={"csrf": csrf, "motivo": "me equivoqué"}, follow_redirects=False,
    )
    assert "documento%20en%20contra" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT estado FROM entradas"))
    ).scalar() == "confirmada"


async def test_cancelar_un_borrador_exige_motivo(cliente, sesion, semilla):
    await _entrar(cliente)
    entrada = _id_de(await _abrir(cliente, semilla))
    csrf = _csrf(cliente)

    vacio = await cliente.post(
        f"/panel/entradas/{entrada}/cancelar",
        data={"csrf": csrf, "motivo": "  "}, follow_redirects=False,
    )
    assert "por%20qu%C3%A9" in vacio.headers["location"]

    bueno = await cliente.post(
        f"/panel/entradas/{entrada}/cancelar",
        data={"csrf": csrf, "motivo": "la remisión era de otra sucursal"},
        follow_redirects=False,
    )
    assert bueno.status_code == 303
    fila = (
        await sesion.execute(
            text("SELECT estado, cancelacion_motivo FROM entradas")
        )
    ).mappings().one()
    assert fila["estado"] == "cancelada"
    assert "otra sucursal" in fila["cancelacion_motivo"]


async def test_una_entrada_sin_renglones_no_se_confirma(cliente, sesion, semilla):
    await _entrar(cliente)
    entrada = _id_de(await _abrir(cliente, semilla))
    r = await _confirmar(cliente, entrada)
    assert "sin%20un%20solo%20rengl%C3%B3n" in r.headers["location"]
    assert (await sesion.execute(text("SELECT estado FROM entradas"))).scalar() == "borrador"


# ===========================================================================
# 5. La captura: conversión, lote, topes
# ===========================================================================
async def test_la_conversion_caja_pieza_se_hace_una_vez_y_se_dice(
    cliente, sesion, semilla
):
    await _entrar(cliente)
    await _producto(sesion, factor=24)
    entrada = _id_de(await _abrir(cliente, semilla))
    r = await _renglon(cliente, entrada, unidad="CAJA", cantidad="10")
    assert "240" in r.headers["location"]

    fila = (
        await sesion.execute(
            text(
                "SELECT cantidad, unidad_codigo, unidades_capturadas "
                "  FROM entrada_detalle"
            )
        )
    ).mappings().one()
    assert fila["cantidad"] == Decimal("240.000")
    # Y además lo que se tecleó: «240» no se puede revisar contra una remisión
    # que dice «10 cajas».
    assert fila["unidad_codigo"] == "CAJA"
    assert fila["unidades_capturadas"] == Decimal("10.000")


async def test_media_caja_se_rechaza(cliente, sesion, semilla):
    """Un 2.5 es un dedazo, y `cantidad_base` lo convertiría en 60 piezas con
    cara de dato bueno."""
    await _entrar(cliente)
    await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    r = await _renglon(cliente, entrada, cantidad="2.5")
    assert "bultos%20completos" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM entrada_detalle"))
    ).scalar() == 0


async def test_un_cero_de_mas_se_detiene(cliente, sesion, semilla):
    """Confirmado deja la existencia inservible hasta que alguien capture el
    ajuste contrario."""
    await _entrar(cliente)
    await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    r = await _renglon(cliente, entrada, cantidad="1000000")
    assert "cero%20de%20m%C3%A1s" in r.headers["location"]


async def test_el_mismo_producto_y_lote_se_suma(cliente, sesion, semilla):
    """Es lo que espera quien captura de dos tarimas; reemplazar perdería la
    primera captura sin avisar."""
    await _entrar(cliente)
    await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")
    await _renglon(cliente, entrada, cantidad="5")

    fila = (
        await sesion.execute(
            text("SELECT cantidad, unidades_capturadas FROM entrada_detalle")
        )
    ).mappings().one()
    assert fila["cantidad"] == Decimal("360.000")      # 15 cajas de 24
    assert fila["unidades_capturadas"] == Decimal("15.000")


async def test_dos_presentaciones_del_mismo_producto_no_inventan_una_suma(
    cliente, sesion, semilla
):
    """10 cajas más 5 piezas son 245 piezas, y NO «15» de ninguna unidad.

    Sumar cajas con piezas en `unidades_capturadas` daría un renglón que no se
    puede revisar contra ninguna remisión, así que se deja en nulo y la pantalla
    dice «varias presentaciones».
    """
    await _entrar(cliente)
    await _producto(sesion, factor=24)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, unidad="CAJA", cantidad="10")
    await _renglon(cliente, entrada, unidad="PZA", cantidad="5")

    fila = (
        await sesion.execute(
            text("SELECT cantidad, unidad_codigo, unidades_capturadas "
                 "  FROM entrada_detalle")
        )
    ).mappings().one()
    assert fila["cantidad"] == Decimal("245.000")
    assert fila["unidad_codigo"] is None
    assert fila["unidades_capturadas"] is None
    assert "varias presentaciones" in solo_texto(
        await cliente.get(f"/panel/entradas/{entrada}")
    )


async def test_la_proyeccion_suma_todos_los_lotes_del_producto(
    cliente, sesion, semilla
):
    """Con dos lotes del mismo producto, la flecha tiene que dar el total real.

    Calculada por renglón, cada uno partiría de la misma existencia y sumaría
    solo lo suyo: dos flechas, y ninguna el número en el que queda la bodega. La
    columna existe para ver un cero de más antes de confirmar, así que una cifra
    que miente ahí es peor que no tenerla.
    """
    await _entrar(cliente)
    producto = await _producto(sesion, sku="LECHE", maneja_lote=True, factor=12)
    # 100 piezas ya en la bodega, para que la proyección no empiece en cero.
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 100)"
        ),
        {"a": semilla["bodega"], "p": producto},
    )
    await sesion.commit()

    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, sku="LECHE", cantidad="10", lote="L-A")
    await _renglon(cliente, entrada, sku="LECHE", cantidad="5", lote="L-B")

    # 100 + 120 + 60 = 280, y los DOS renglones tienen que decir 280.
    texto = solo_texto(await cliente.get(f"/panel/entradas/{entrada}"))
    assert texto.count("→ 280") == 2
    assert "→ 220" not in texto     # lo que daría la cuenta por renglón
    assert "→ 160" not in texto

    await _confirmar(cliente, entrada)
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("280")


async def test_un_producto_de_lote_no_se_recibe_sin_lote(cliente, sesion, semilla):
    """Sin lote, una merma por caducidad no se puede rastrear a su tarima."""
    await _entrar(cliente)
    await _producto(sesion, sku="LECHE", maneja_lote=True)
    entrada = _id_de(await _abrir(cliente, semilla))

    sin_lote = await _renglon(cliente, entrada, sku="LECHE", cantidad="10")
    assert "maneja%20lote" in sin_lote.headers["location"]

    con_lote = await _renglon(
        cliente, entrada, sku="LECHE", cantidad="10", lote="L-2026-10",
        caducidad="2027-03-01",
    )
    assert "240" in con_lote.headers["location"]
    fila = (
        await sesion.execute(text("SELECT lote, caducidad FROM entrada_detalle"))
    ).mappings().one()
    assert fila["lote"] == "L-2026-10"
    assert fila["caducidad"] == date(2027, 3, 1)


async def test_una_clave_que_no_existe_lo_dice_con_la_clave(cliente, sesion, semilla):
    await _entrar(cliente)
    entrada = _id_de(await _abrir(cliente, semilla))
    r = await _renglon(cliente, entrada, sku="NOEXISTE", cantidad="1")
    assert "NOEXISTE" in r.headers["location"]


async def test_quitar_un_renglon_del_borrador(cliente, sesion, semilla):
    await _entrar(cliente)
    await _producto(sesion)
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")
    renglon = (
        await sesion.execute(text("SELECT id FROM entrada_detalle"))
    ).scalar()

    csrf = _csrf(cliente)
    r = await cliente.post(
        f"/panel/entradas/{entrada}/renglon/{renglon}/quitar",
        data={"csrf": csrf}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        await sesion.execute(text("SELECT count(*) FROM entrada_detalle"))
    ).scalar() == 0


# ===========================================================================
# 6. El documento: motivos, fechas, nota
# ===========================================================================
async def test_el_inventario_inicial_exige_nota(cliente, sesion, semilla):
    """Es el documento que explica de dónde salió TODO el inventario del
    arranque, y se lee el día que algo no cuadra."""
    await _entrar(cliente)
    sin_nota = await _abrir(cliente, semilla, motivo="inicial")
    assert "necesita%20una%20nota" in sin_nota.headers["location"]
    assert (await sesion.execute(text("SELECT count(*) FROM entradas"))).scalar() == 0

    con_nota = await _abrir(
        cliente, semilla, motivo="inicial",
        nota="Conteo del 1 de octubre con Beto y Juan",
    )
    assert con_nota.status_code == 303
    fila = (
        await sesion.execute(text("SELECT motivo, nota FROM entradas"))
    ).mappings().one()
    assert fila["motivo"] == "inicial"
    assert "Beto" in fila["nota"]


async def test_el_inventario_inicial_entra_como_ajuste_al_libro_mayor(
    cliente, sesion, semilla
):
    """No se le compró a nadie: 'compra' sería mentirle al libro mayor."""
    await _entrar(cliente)
    await _producto(sesion)
    entrada = _id_de(
        await _abrir(cliente, semilla, motivo="inicial", nota="arranque")
    )
    await _renglon(cliente, entrada, cantidad="10")
    await _confirmar(cliente, entrada)

    assert (
        await sesion.execute(text("SELECT tipo FROM movimientos_inventario"))
    ).scalar() == "ajuste"


async def test_una_fecha_futura_se_rechaza(cliente, sesion, semilla):
    """Es mercancía que todavía no llegó, y entraría al libro mayor con una
    fecha en la que no existía."""
    await _entrar(cliente)
    manana = (date.today() + timedelta(days=1)).isoformat()
    r = await _abrir(cliente, semilla, fecha=manana)
    assert "futura" in r.headers["location"]
    assert (await sesion.execute(text("SELECT count(*) FROM entradas"))).scalar() == 0


async def test_una_fecha_atrasada_se_acepta(cliente, sesion, semilla):
    """La remisión del viernes capturada el lunes es el caso normal."""
    await _entrar(cliente)
    viernes = (date.today() - timedelta(days=3)).isoformat()
    r = await _abrir(cliente, semilla, fecha=viernes)
    assert r.status_code == 303
    assert (
        await sesion.execute(text("SELECT fecha_operativa FROM entradas"))
    ).scalar() == date.today() - timedelta(days=3)


async def test_el_folio_es_consecutivo_y_no_max_mas_uno(cliente, sesion, semilla):
    """Dos personas recibiendo dos remisiones a la vez es la mañana normal."""
    await _entrar(cliente)
    for _ in range(3):
        await _abrir(cliente, semilla)
    folios = (
        await sesion.execute(text("SELECT folio FROM entradas ORDER BY folio"))
    ).scalars().all()
    assert len(folios) == 3
    assert all(f.startswith("EN-") for f in folios)
    assert len(set(folios)) == 3


# ===========================================================================
# 7. Los permisos
# ===========================================================================
async def test_sin_el_permiso_se_ve_y_no_se_captura(cliente, sesion, semilla):
    """El gerente mide y no ajusta: una entrada es la operación con la que se
    puede tapar un faltante (ADR 0002 §0)."""
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {"u": uuid.uuid4(), "s": semilla["sucursal"],
         "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()
    await _entrar(cliente, "GER01")

    r = await cliente.get("/panel/entradas")
    assert r.status_code == 200
    texto = solo_texto(r)
    assert "inventario.ajustar" in texto
    assert "Recibir mercancía" not in texto

    # Y el POST tampoco: la pantalla sin botón no es la defensa.
    csrf = _csrf(cliente)
    bloqueado = await cliente.post(
        "/panel/entradas/nueva",
        data={"csrf": csrf, "motivo": "compra",
              "almacen_destino_id": str(semilla["bodega"])},
        follow_redirects=False,
    )
    assert bloqueado.status_code == 403


async def test_el_permiso_tiene_dueno(sesion):
    """`inventario.ajustar` se define en la 0009 y la 0009 no se lo da a NADIE.

    Sin el GRANT de la 0025, la pantalla existiría y solo `admin` podría usarla
    —porque salta los permisos por código— y el supervisor, que es quien está en
    la bodega a las seis de la mañana, no. Un permiso sin dueño es una función
    que parece construida.
    """
    duenos = (
        await sesion.execute(
            text(
                "SELECT rol_codigo FROM roles_permisos "
                " WHERE permiso_codigo = 'inventario.ajustar' ORDER BY rol_codigo"
            )
        )
    ).scalars().all()
    assert duenos == ["supervisor"], (
        "quien mide no ajusta: gerencia no lleva este permiso"
    )


async def test_el_supervisor_si_puede_recibir(cliente, sesion, semilla):
    """Es quien está físicamente en la bodega cuando llega el camión del
    proveedor."""
    from app.core.seguridad import hashear_password

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'SUP01', 'Supervisor', :h, 'supervisor', now(), now())"
        ),
        {"u": uuid.uuid4(), "s": semilla["sucursal"],
         "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()
    await _entrar(cliente, "SUP01")
    assert "Recibir mercancía" in solo_texto(await cliente.get("/panel/entradas"))


# ===========================================================================
# 8. El flujo completo: los tres requisitos, de punta a punta
# ===========================================================================
async def test_recibir_consultar_y_cargar_al_camion(cliente, sesion, semilla):
    """La prueba que habría faltado desde el principio.

    Recorre lo que un día de bodega necesita y que hasta ahora no se podía hacer
    sin SQL: entra mercancía, se ve en el inventario, y sube al camión con una
    carga. Si cualquiera de los tres eslabones se rompe, el sistema no se puede
    operar — y ninguna prueba anterior cubría el primero.
    """
    await _entrar(cliente)
    producto = await _producto(sesion, sku="COCA600", factor=24)

    # 1. Entra mercancía a la bodega.
    entrada = _id_de(await _abrir(cliente, semilla))
    await _renglon(cliente, entrada, cantidad="10")
    await _confirmar(cliente, entrada)
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("240")

    # 2. Se consulta, y la pantalla de inventario la muestra.
    inventario = await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}")
    assert inventario.status_code == 200
    assert "COCA600" in inventario.text

    # 3. Sube al camión con una carga.
    csrf = _csrf(cliente)
    nueva = await cliente.post(
        "/panel/cargas/nueva",
        data={"csrf": csrf, "vendedor_id": str(semilla["vendedor"]),
              "almacen_origen_id": str(semilla["bodega"]),
              "fecha": date.today().isoformat()},
        follow_redirects=False,
    )
    assert nueva.status_code == 303, nueva.headers.get("location")
    carga = uuid.UUID(nueva.headers["location"].split("/panel/cargas/")[1].split("?")[0])

    csrf = _csrf(cliente)
    await cliente.post(
        f"/panel/cargas/{carga}/renglon",
        data={"csrf": csrf, "producto": "COCA600", "unidad_codigo": "CAJA",
              "cantidad": "4"},
        follow_redirects=False,
    )
    csrf = _csrf(cliente)
    await cliente.post(
        f"/panel/cargas/{carga}/confirmar", data={"csrf": csrf},
        follow_redirects=False,
    )

    # 96 piezas al camión: la bodega queda con 144 y NINGUNA en negativo.
    assert await _existencia(sesion, semilla["bodega"], producto) == Decimal("144")
    assert await _existencia(sesion, semilla["camion"], producto) == Decimal("96")
    negativos = (
        await sesion.execute(
            text("SELECT count(*) FROM existencias WHERE cantidad < 0")
        )
    ).scalar()
    assert negativos == 0


async def test_sin_entradas_la_pantalla_de_inventario_dice_que_hacer(
    cliente, sesion, semilla
):
    """Una bodega en ceros es el estado de un sistema recién instalado, y la
    pantalla tiene que decir qué sigue en vez de solo constatarlo."""
    await _entrar(cliente)
    texto = solo_texto(
        await cliente.get(f"/panel/inventario?almacen={semilla['bodega']}")
    )
    assert "está vacío" in texto
    assert "inventario inicial" in texto
