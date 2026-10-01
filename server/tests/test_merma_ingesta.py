"""Fase 6 · La merma y el no-drop que llegan del teléfono.

────────────────────────────────────────────────────────────────────────────
QUÉ DEFIENDEN ESTAS PRUEBAS
────────────────────────────────────────────────────────────────────────────
Estos dos documentos no mueven dinero, y por eso es fácil tratarlos como
papeleo. No lo son: **son los únicos que explican una diferencia.**

Sin la merma, el refresco que se reventó en el camión llega a la liquidación
como faltante, y un faltante sin explicación se le carga al vendedor. Es el
caso que le duele a un vendedor honesto y el único que no puede corregir
después, porque para el cierre el cartón roto ya se tiró.

Sin el no-drop, un día de 20 visitas con 12 ventas se ve igual que uno de 12
visitas con 12 ventas. Desde la oficina son indistinguibles, y el primero tiene
ocho clientes que necesitan algo.

Cuatro cosas que se rompen con consecuencias de campo:

1. **El signo.** `merma` saca del camión, `devolucion_cliente` mete. La
   liquidación calcula `esperado = cargado − vendido − merma + devuelto`, así que
   equivocarlo produce un descuadre del **doble** del tamaño de la operación.
2. **Reenviar el sobre no puede mermar dos veces.** Un reintento duplicaría la
   pérdida en el inventario y en la liquidación.
3. **La falta de existencia se marca, no se rechaza** (§0.1). Si el camión dice 2
   y se rompieron 3, el que está mal es el conteo: rechazar la merma convierte la
   pérdida en faltante del vendedor, que es justo lo que el documento evita.
4. **El no-drop sin GPS sí se rechaza**, y es la única excepción del sistema a
   «marcar, no rechazar»: sin el sello de ubicación no queda ningún hecho que
   preservar, y guardarlo metería una visita no verificable a cada reporte.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sync.sobres import CodigoError
from app.infra.sync.manejadores import (
    MOTIVO_MOTIVO_INACTIVO,
    MOTIVO_NO_DROP_SIN_NOTA,
    MOTIVO_SIN_EXISTENCIA_PARA_MERMA,
    Contexto,
    ErrorDeManejador,
    obtener_manejador,
)

pytestmark = pytest.mark.asyncio

MOMENTO = "2026-09-29T17:42:03.250Z"
DIA = "2026-09-29"


@pytest.fixture
async def escenario(sesion: AsyncSession, semilla: dict) -> dict:
    """Un camión con 48 piezas de atún, un cliente de la ruta y un almacén de merma."""
    dispositivo = uuid.uuid4()
    cliente = uuid.uuid4()
    producto = uuid.uuid4()
    almacen_merma = uuid.uuid4()

    await sesion.execute(
        text(
            "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
            "VALUES (:d, :u, 'POCO M5s de Juan', 'activo', now())"
        ),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'C00001', 'Abarrotes Doña Mary', :r, now(), now())"
        ),
        {"c": cliente, "r": semilla["ruta"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:p, 'ATUN-140', 'Atún en agua 140 g', 'PZA', 0.0000)"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            "INSERT INTO almacenes (id, codigo, nombre, tipo) "
            "VALUES (:a, 'MERMA_01', 'Almacén de merma', 'merma')"
        ),
        {"a": almacen_merma},
    )
    # 48 piezas = dos cajas de 24 en el camión.
    await sesion.execute(
        text(
            "INSERT INTO existencias (almacen_id, producto_id, cantidad) "
            "VALUES (:a, :p, 48)"
        ),
        {"a": semilla["camion"], "p": producto},
    )
    await sesion.commit()

    return {
        "dispositivo": dispositivo,
        "cliente": cliente,
        "producto": producto,
        "almacen_merma": almacen_merma,
    }


def _contexto(escenario: dict, semilla: dict) -> Contexto:
    return Contexto(
        dispositivo_id=escenario["dispositivo"],
        usuario_id=semilla["vendedor"],
        rutas=(semilla["ruta"],),
        almacen_id=semilla["camion"],
    )


def _merma(escenario: dict, **campos) -> dict:
    datos = {
        "folio_consecutivo": 1,
        "folio_local": "VEND01-000001",
        "tipo": "merma",
        "motivo_codigo": "ROTO",
        "fecha_dispositivo": MOMENTO,
        "fecha_operativa": DIA,
        "detalle": [{"producto_id": str(escenario["producto"]), "cantidad_base": "6.000"}],
    }
    datos.update(campos)
    return datos


def _no_drop(escenario: dict, **campos) -> dict:
    datos = {
        "folio_consecutivo": 1,
        "cliente_id": str(escenario["cliente"]),
        "motivo_codigo": "CERRADO",
        "lat": "20.6736000",
        "lng": "-103.3440000",
        "ubicacion_precision_m": "12.50",
        "fecha_dispositivo": MOMENTO,
        "fecha_operativa": DIA,
    }
    datos.update(campos)
    return datos


async def _aplicar(sesion, escenario, semilla, tipo, datos, entidad_id=None) -> uuid.UUID:
    manejador = obtener_manejador(tipo)
    identificador = entidad_id or uuid.uuid4()
    await manejador(sesion, _contexto(escenario, semilla), identificador, datos)
    return identificador


async def _existencia(sesion, almacen, producto) -> Decimal | None:
    return (
        await sesion.execute(
            text(
                "SELECT cantidad FROM existencias "
                " WHERE almacen_id = :a AND producto_id = :p"
            ),
            {"a": almacen, "p": producto},
        )
    ).scalar_one_or_none()


async def _movimientos(sesion, documento) -> list[dict]:
    filas = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, almacen_destino_id, cantidad "
                "  FROM movimientos_inventario WHERE documento_id = :d "
                " ORDER BY id"
            ),
            {"d": documento},
        )
    ).mappings().all()
    return [dict(f) for f in filas]


# ---------------------------------------------------------------------------
# El signo, que es lo que descuadra la liquidación si se equivoca
# ---------------------------------------------------------------------------


async def test_la_merma_saca_mercancia_del_camion(sesion, semilla, escenario):
    await _aplicar(sesion, escenario, semilla, "merma.crear", _merma(escenario))
    await sesion.commit()

    # 48 − 6 = 42.
    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "42.000"
    )


async def test_la_devolucion_mete_mercancia_al_camion(sesion, semilla, escenario):
    await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(
            escenario,
            tipo="devolucion_cliente",
            motivo_codigo="DEVOLUCION_CLIENTE",
            cliente_id=str(escenario["cliente"]),
        ),
    )
    await sesion.commit()

    # 48 + 6 = 54. Es el mismo documento visto al revés.
    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "54.000"
    )


async def test_lo_mermado_queda_contabilizado_en_el_almacen_de_merma(
    sesion, semilla, escenario
):
    """La pérdida no desaparece: queda en un almacén donde se puede contar.

    Si solo se restara del camión, la mercancía se evaporaría del libro mayor y
    nadie podría preguntar cuánto se mermó en el mes.
    """
    await _aplicar(sesion, escenario, semilla, "merma.crear", _merma(escenario))
    await sesion.commit()

    assert await _existencia(
        sesion, escenario["almacen_merma"], escenario["producto"]
    ) == Decimal("6.000")


async def test_la_devolucion_no_toca_el_almacen_de_merma(sesion, semilla, escenario):
    """Lo que el cliente devuelve es mercancía vendible, no merma.

    Confundirlos haría que una devolución en buen estado se contabilizara como
    pérdida del mes.
    """
    await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(
            escenario,
            tipo="devolucion_cliente",
            motivo_codigo="DEVOLUCION_CLIENTE",
            cliente_id=str(escenario["cliente"]),
        ),
    )
    await sesion.commit()

    assert await _existencia(sesion, escenario["almacen_merma"], escenario["producto"]) is None


async def test_el_movimiento_de_merma_lleva_su_direccion(sesion, semilla, escenario):
    merma_id = await _aplicar(sesion, escenario, semilla, "merma.crear", _merma(escenario))
    await sesion.commit()

    movimientos = await _movimientos(sesion, merma_id)
    assert len(movimientos) == 1
    assert movimientos[0]["tipo"] == "merma"
    assert movimientos[0]["almacen_origen_id"] == semilla["camion"]
    assert movimientos[0]["almacen_destino_id"] == escenario["almacen_merma"]
    assert movimientos[0]["cantidad"] == Decimal("6.000")


async def test_el_movimiento_de_devolucion_entra_sin_origen(sesion, semilla, escenario):
    """La devolución no sale de ningún almacén nuestro: viene de la tienda."""
    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(
            escenario,
            tipo="devolucion_cliente",
            motivo_codigo="DEVOLUCION_CLIENTE",
            cliente_id=str(escenario["cliente"]),
        ),
    )
    await sesion.commit()

    movimientos = await _movimientos(sesion, merma_id)
    assert movimientos[0]["tipo"] == "devolucion"
    assert movimientos[0]["almacen_origen_id"] is None
    assert movimientos[0]["almacen_destino_id"] == semilla["camion"]


async def test_sin_almacen_de_merma_la_salida_queda_registrada(sesion, semilla, escenario):
    """Una empresa que no configuró almacén de merma no pierde el registro.

    La mercancía sale del sistema —no hay a dónde mandarla— pero el movimiento de
    salida existe, y es lo que la liquidación necesita para no cobrarle el
    faltante al vendedor.
    """
    await sesion.execute(
        text("UPDATE almacenes SET activo = false WHERE id = :a"),
        {"a": escenario["almacen_merma"]},
    )
    await sesion.commit()

    merma_id = await _aplicar(sesion, escenario, semilla, "merma.crear", _merma(escenario))
    await sesion.commit()

    movimientos = await _movimientos(sesion, merma_id)
    assert movimientos[0]["almacen_destino_id"] is None
    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "42.000"
    )


# ---------------------------------------------------------------------------
# Idempotencia: el reintento es la regla, no la excepción
# ---------------------------------------------------------------------------


async def test_reenviar_la_misma_merma_no_merma_dos_veces(sesion, semilla, escenario):
    merma_id = uuid.uuid4()
    datos = _merma(escenario)

    await _aplicar(sesion, escenario, semilla, "merma.crear", datos, merma_id)
    await sesion.commit()
    await _aplicar(sesion, escenario, semilla, "merma.crear", datos, merma_id)
    await sesion.commit()

    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "42.000"
    )
    assert len(await _movimientos(sesion, merma_id)) == 1
    renglones = (
        await sesion.execute(
            text("SELECT count(*) FROM merma_detalle WHERE merma_id = :m"),
            {"m": merma_id},
        )
    ).scalar_one()
    assert renglones == 1


async def test_reenviar_el_no_drop_no_lo_duplica(sesion, semilla, escenario):
    no_drop_id = uuid.uuid4()
    datos = _no_drop(escenario)

    await _aplicar(sesion, escenario, semilla, "no_drop.crear", datos, no_drop_id)
    await sesion.commit()
    await _aplicar(sesion, escenario, semilla, "no_drop.crear", datos, no_drop_id)
    await sesion.commit()

    cuantos = (
        await sesion.execute(text("SELECT count(*) FROM no_drops"))
    ).scalar_one()
    assert cuantos == 1


async def test_dos_renglones_del_mismo_producto_se_suman(sesion, semilla, escenario):
    """Dos cajas del mismo atún capturadas por separado son una sola pérdida de 12.

    El `ON CONFLICT` del detalle las suma en vez de descartar la segunda, que
    perdería la mitad de la merma.
    """
    producto = str(escenario["producto"])
    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(
            escenario,
            detalle=[
                {"producto_id": producto, "cantidad_base": "6.000"},
                {"producto_id": producto, "cantidad_base": "6.000"},
            ],
        ),
    )
    await sesion.commit()

    cantidad = (
        await sesion.execute(
            text("SELECT cantidad_base FROM merma_detalle WHERE merma_id = :m"),
            {"m": merma_id},
        )
    ).scalar_one()
    assert cantidad == Decimal("12.000")
    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "36.000"
    )


# ---------------------------------------------------------------------------
# §0.1 — se marca, no se rechaza
# ---------------------------------------------------------------------------


async def test_mermar_mas_de_lo_que_hay_se_marca_y_entra(sesion, semilla, escenario):
    """El camión dice 48 y se reventaron 60: el que está mal es el conteo.

    Rechazarla haría que la pérdida apareciera como faltante del vendedor, que es
    exactamente lo que este documento existe para evitar.
    """
    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(
            escenario,
            detalle=[
                {"producto_id": str(escenario["producto"]), "cantidad_base": "60.000"}
            ],
        ),
    )
    await sesion.commit()

    merma = (
        await sesion.execute(
            text(
                "SELECT requiere_revision, revision_motivos FROM mermas WHERE id = :m"
            ),
            {"m": merma_id},
        )
    ).mappings().one()
    assert merma["requiere_revision"] is True
    assert merma["revision_motivos"] == [MOTIVO_SIN_EXISTENCIA_PARA_MERMA]

    # Y la existencia queda NEGATIVA, a propósito: la cache no tiene CHECK >= 0
    # porque el mundo físico ya ocurrió y mentir sobre el inventario sería peor.
    assert await _existencia(sesion, semilla["camion"], escenario["producto"]) == Decimal(
        "-12.000"
    )


async def test_un_producto_que_el_camion_nunca_tuvo_tambien_se_marca(
    sesion, semilla, escenario
):
    otro = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'GALLETA-100', 'Galletas 100 g', 'PZA')"
        ),
        {"p": otro},
    )
    await sesion.commit()

    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(escenario, detalle=[{"producto_id": str(otro), "cantidad_base": "3.000"}]),
    )
    await sesion.commit()

    marcada = (
        await sesion.execute(
            text("SELECT requiere_revision FROM mermas WHERE id = :m"), {"m": merma_id}
        )
    ).scalar_one()
    assert marcada is True


async def test_el_motivo_repetido_no_se_marca_dos_veces(sesion, semilla, escenario):
    """Dos renglones sin existencia dan UN motivo, no dos iguales.

    La oficina lee esta lista; repetir el mismo texto solo hace ruido.
    """
    otro = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'GALLETA-100', 'Galletas 100 g', 'PZA')"
        ),
        {"p": otro},
    )
    await sesion.commit()

    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(
            escenario,
            detalle=[
                {"producto_id": str(otro), "cantidad_base": "3.000"},
                {"producto_id": str(escenario["producto"]), "cantidad_base": "99.000"},
            ],
        ),
    )
    await sesion.commit()

    motivos = (
        await sesion.execute(
            text("SELECT revision_motivos FROM mermas WHERE id = :m"), {"m": merma_id}
        )
    ).scalar_one()
    assert motivos == [MOTIVO_SIN_EXISTENCIA_PARA_MERMA]


async def test_una_devolucion_nunca_se_marca_por_existencia(sesion, semilla, escenario):
    """Lo que entra no necesita existencia previa: el cliente lo trae en la mano."""
    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(
            escenario,
            tipo="devolucion_cliente",
            motivo_codigo="DEVOLUCION_CLIENTE",
            cliente_id=str(escenario["cliente"]),
            detalle=[{"producto_id": str(escenario["producto"]), "cantidad_base": "999.000"}],
        ),
    )
    await sesion.commit()

    marcada = (
        await sesion.execute(
            text("SELECT requiere_revision FROM mermas WHERE id = :m"), {"m": merma_id}
        )
    ).scalar_one()
    assert marcada is False


async def test_el_no_drop_sin_nota_obligatoria_se_marca_y_entra(sesion, semilla, escenario):
    """`NO_LE_INTERESA` exige explicación, pero el dato de la visita vale sin ella.

    Que el cliente no compró es un hecho; que el vendedor no escribió por qué es
    un pendiente de la oficina, no razón para borrar la visita.
    """
    no_drop_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "no_drop.crear",
        _no_drop(escenario, motivo_codigo="NO_LE_INTERESA"),
    )
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT requiere_revision, revision_motivos FROM no_drops WHERE id = :n"
            ),
            {"n": no_drop_id},
        )
    ).mappings().one()
    assert fila["requiere_revision"] is True
    assert fila["revision_motivos"] == [MOTIVO_NO_DROP_SIN_NOTA]


async def test_el_no_drop_con_su_nota_entra_limpio(sesion, semilla, escenario):
    no_drop_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "no_drop.crear",
        _no_drop(
            escenario,
            motivo_codigo="NO_LE_INTERESA",
            nota="Dice que sus clientes piden la marca grande",
        ),
    )
    await sesion.commit()

    fila = (
        await sesion.execute(
            text("SELECT requiere_revision, nota FROM no_drops WHERE id = :n"),
            {"n": no_drop_id},
        )
    ).mappings().one()
    assert fila["requiere_revision"] is False
    assert fila["nota"] == "Dice que sus clientes piden la marca grande"


# ---------------------------------------------------------------------------
# La única excepción: el no-drop sin GPS
# ---------------------------------------------------------------------------


async def test_el_no_drop_sin_ubicacion_se_rechaza(sesion, semilla, escenario):
    """Sin coordenadas, el documento no afirma nada verificable.

    Es la única excepción a «marcar, no rechazar» de todo el sistema, y no pierde
    el dato: el rechazo manda el sobre completo a cuarentena.
    """
    datos = _no_drop(escenario)
    datos.pop("lat")

    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(sesion, escenario, semilla, "no_drop.crear", datos)
    assert error.value.codigo is CodigoError.CONFLICTO_DE_DATOS
    assert "visita" in str(error.value).lower()


async def test_el_no_drop_guarda_su_geosello(sesion, semilla, escenario):
    no_drop_id = await _aplicar(
        sesion, escenario, semilla, "no_drop.crear", _no_drop(escenario)
    )
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT lat, lng, ubicacion_precision_m FROM no_drops WHERE id = :n"
            ),
            {"n": no_drop_id},
        )
    ).mappings().one()
    assert fila["lat"] == Decimal("20.6736000")
    assert fila["lng"] == Decimal("-103.3440000")
    assert fila["ubicacion_precision_m"] == Decimal("12.50")


# ---------------------------------------------------------------------------
# Catálogos cerrados y pertenencia
# ---------------------------------------------------------------------------


async def test_un_motivo_de_merma_inventado_se_rechaza(sesion, semilla, escenario):
    """El catálogo es cerrado por diseño: texto libre no se puede analizar.

    «roto», «se rompio» y «rroto» serían tres categorías distintas para cualquier
    reporte, y el dispositivo recibe el catálogo sincronizado justo para esto.
    """
    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            "merma.crear",
            _merma(escenario, motivo_codigo="SE_ROMPIO_SOLITO"),
        )
    assert error.value.codigo is CodigoError.CONFLICTO_DE_DATOS


async def test_un_motivo_de_no_drop_inventado_se_rechaza(sesion, semilla, escenario):
    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            "no_drop.crear",
            _no_drop(escenario, motivo_codigo="NO_ME_CAE_BIEN"),
        )
    assert error.value.codigo is CodigoError.CONFLICTO_DE_DATOS


async def test_un_motivo_desactivado_se_marca_pero_entra(sesion, semilla, escenario):
    """La oficina retiró el motivo mientras el vendedor andaba en la calle.

    El teléfono le ofreció ese motivo porque era el catálogo que tenía. Rechazarlo
    castigaría al vendedor por una edición de escritorio, y en el caso de la merma
    convertiría la pérdida en un faltante suyo. Se marca y entra; un motivo que no
    existe en absoluto sí se rechaza, porque no hay nada a lo que mapearlo.
    """
    await sesion.execute(
        text("UPDATE motivos_no_drop SET activo = false WHERE codigo = 'CERRADO'")
    )
    await sesion.execute(
        text("UPDATE motivos_merma SET activo = false WHERE codigo = 'ROTO'")
    )
    await sesion.commit()
    try:
        no_drop_id = await _aplicar(
            sesion, escenario, semilla, "no_drop.crear", _no_drop(escenario)
        )
        merma_id = await _aplicar(
            sesion, escenario, semilla, "merma.crear", _merma(escenario)
        )
        await sesion.commit()

        for tabla, identificador in (("no_drops", no_drop_id), ("mermas", merma_id)):
            fila = (
                await sesion.execute(
                    text(
                        f"SELECT requiere_revision, revision_motivos FROM {tabla} "
                        " WHERE id = :id"
                    ),
                    {"id": identificador},
                )
            ).mappings().one()
            assert fila["requiere_revision"] is True
            assert MOTIVO_MOTIVO_INACTIVO in fila["revision_motivos"]
    finally:
        await sesion.execute(
            text("UPDATE motivos_no_drop SET activo = true WHERE codigo = 'CERRADO'")
        )
        await sesion.execute(
            text("UPDATE motivos_merma SET activo = true WHERE codigo = 'ROTO'")
        )
        await sesion.commit()


async def test_el_no_drop_de_un_cliente_de_otra_ruta_se_rechaza(
    sesion, semilla, escenario
):
    """Un vendedor no reporta visitas de la ruta de otro.

    No es paranoia: los reportes de efectividad se leen por ruta, y una visita
    cruzada los ensucia a los dos.
    """
    otra_ruta, ajeno = uuid.uuid4(), uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre) VALUES (:r, 'R09', 'Ruta 9')"),
        {"r": otra_ruta},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
            "                      creado_en, actualizado_en) "
            "VALUES (:c, 'C00099', 'Tienda de otra ruta', :r, now(), now())"
        ),
        {"c": ajeno, "r": otra_ruta},
    )
    await sesion.commit()

    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            "no_drop.crear",
            _no_drop(escenario, cliente_id=str(ajeno)),
        )
    assert error.value.codigo is CodigoError.CONFLICTO_DE_DATOS


async def test_el_no_drop_de_un_cliente_inexistente_se_rechaza(sesion, semilla, escenario):
    with pytest.raises(ErrorDeManejador):
        await _aplicar(
            sesion,
            escenario,
            semilla,
            "no_drop.crear",
            _no_drop(escenario, cliente_id=str(uuid.uuid4())),
        )


# ---------------------------------------------------------------------------
# Payloads que no describen ninguna operación
# ---------------------------------------------------------------------------


async def test_una_merma_sin_renglones_no_es_una_merma(sesion, semilla, escenario):
    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion, escenario, semilla, "merma.crear", _merma(escenario, detalle=[])
        )
    assert error.value.codigo is CodigoError.PAYLOAD_INVALIDO


async def test_un_renglon_de_cero_se_rechaza(sesion, semilla, escenario):
    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            "merma.crear",
            _merma(
                escenario,
                detalle=[
                    {"producto_id": str(escenario["producto"]), "cantidad_base": "0.000"}
                ],
            ),
        )
    assert error.value.codigo is CodigoError.PAYLOAD_INVALIDO


async def test_un_tipo_que_no_existe_se_rechaza(sesion, semilla, escenario):
    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion, escenario, semilla, "merma.crear", _merma(escenario, tipo="regalo")
        )
    assert error.value.codigo is CodigoError.PAYLOAD_INVALIDO


async def test_una_devolucion_sin_cliente_se_rechaza(sesion, semilla, escenario):
    """Sin cliente no se sabe a quién se le recibió ni contra qué venta revisarlo."""
    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            "merma.crear",
            _merma(
                escenario,
                tipo="devolucion_cliente",
                motivo_codigo="DEVOLUCION_CLIENTE",
            ),
        )
    assert error.value.codigo is CodigoError.PAYLOAD_INVALIDO


async def test_la_cantidad_debe_venir_como_texto(sesion, semilla, escenario):
    """Un flotante en el payload es un error de contrato, no un redondeo.

    `contracts/README.md` §1.4 los prohíbe: 0.1 + 0.2 no es 0.3 en binario, y una
    cantidad es dinero en cuanto se multiplica por un precio.
    """
    with pytest.raises(ErrorDeManejador) as error:
        await _aplicar(
            sesion,
            escenario,
            semilla,
            "merma.crear",
            _merma(
                escenario,
                detalle=[{"producto_id": str(escenario["producto"]), "cantidad_base": 6.0}],
            ),
        )
    assert error.value.codigo is CodigoError.PAYLOAD_INVALIDO


# ---------------------------------------------------------------------------
# El vendedor y el equipo salen del contexto, nunca del payload
# ---------------------------------------------------------------------------


async def test_el_vendedor_sale_del_contexto_no_del_payload(sesion, semilla, escenario):
    """Si el payload pudiera declarar al vendedor, un equipo podría mermar a nombre
    de otro y ensuciarle la liquidación."""
    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(escenario, vendedor_id=str(uuid.uuid4()), dispositivo_id=str(uuid.uuid4())),
    )
    await sesion.commit()

    fila = (
        await sesion.execute(
            text("SELECT vendedor_id, dispositivo_id, almacen_id FROM mermas WHERE id = :m"),
            {"m": merma_id},
        )
    ).mappings().one()
    assert fila["vendedor_id"] == semilla["vendedor"]
    assert fila["dispositivo_id"] == escenario["dispositivo"]
    assert fila["almacen_id"] == semilla["camion"]


async def test_la_merma_de_otro_almacen_no_se_puede_declarar(sesion, semilla, escenario):
    """El almacén es el camión del contexto: declararlo permitiría mermar la bodega."""
    merma_id = await _aplicar(
        sesion,
        escenario,
        semilla,
        "merma.crear",
        _merma(escenario, almacen_id=str(semilla["bodega"])),
    )
    await sesion.commit()

    almacen = (
        await sesion.execute(
            text("SELECT almacen_id FROM mermas WHERE id = :m"), {"m": merma_id}
        )
    ).scalar_one()
    assert almacen == semilla["camion"]


async def test_el_no_drop_hereda_la_ruta_del_cliente(sesion, semilla, escenario):
    no_drop_id = await _aplicar(
        sesion, escenario, semilla, "no_drop.crear", _no_drop(escenario)
    )
    await sesion.commit()

    fila = (
        await sesion.execute(
            text("SELECT ruta_id, vendedor_id FROM no_drops WHERE id = :n"),
            {"n": no_drop_id},
        )
    ).mappings().one()
    assert fila["ruta_id"] == semilla["ruta"]
    assert fila["vendedor_id"] == semilla["vendedor"]


async def test_la_merma_queda_confirmada_con_su_folio(sesion, semilla, escenario):
    merma_id = await _aplicar(sesion, escenario, semilla, "merma.crear", _merma(escenario))
    await sesion.commit()

    fila = (
        await sesion.execute(
            text(
                "SELECT estado, folio_local, folio_consecutivo, fecha_operativa, "
                "       motivo_codigo FROM mermas WHERE id = :m"
            ),
            {"m": merma_id},
        )
    ).mappings().one()
    assert fila["estado"] == "confirmada"
    assert fila["folio_local"] == "VEND01-000001"
    assert fila["folio_consecutivo"] == 1
    assert fila["fecha_operativa"].isoformat() == DIA
    assert fila["motivo_codigo"] == "ROTO"
