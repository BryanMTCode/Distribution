"""La venta que llega del teléfono.

────────────────────────────────────────────────────────────────────────────
LO QUE ESTAS PRUEBAS DEFIENDEN
────────────────────────────────────────────────────────────────────────────
Cuando este sobre llega, la mercancía YA SALIÓ DEL CAMIÓN y el cliente YA TIENE
SU REMISIÓN IMPRESA. El hecho ocurrió (§0.1).

Así que la mitad de estas pruebas verifican que el servidor **NO rechace** cosas
que un programador con buenas intenciones rechazaría: un cliente pasado de su
límite de crédito, una venta lejos del domicilio registrado, un precio viejo, un
reloj desfasado. Todas esas ventas entran, y quedan marcadas para que la oficina
las revise.

La otra mitad verifica que sí rechace lo que no describe ninguna venta posible:
un total que no cuadra con sus partidas, un importe que no es cantidad × precio.
Eso es bug del cliente o manipulación, y va a cuarentena con el payload íntegro.

La tentación constante en este archivo es "validar bien". Validar bien aquí
significa REGISTRAR bien.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sync.sobres import CodigoError
from app.infra.sync.manejadores import (
    MOTIVO_CLIENTE_BLOQUEADO,
    MOTIVO_EXCEDE_CREDITO,
    MOTIVO_LEJOS_DEL_CLIENTE,
    MOTIVO_PRECIO_DESACTUALIZADO,
    MOTIVO_RELOJ_DESFASADO,
    MOTIVO_SIN_GEOSELLO,
    MOTIVO_SIN_LISTA,
    Contexto,
    ErrorDeManejador,
    obtener_manejador,
)

pytestmark = pytest.mark.asyncio

PRECIO_CAJA = "296.0000"

# El instante que trae el payload, y el que el servidor "recibe" siete segundos
# después. Fijos a propósito: con `now()` real, el reloj del payload quedaría
# horas en el futuro y TODA venta saldría marcada por desfase — la prueba del
# camino feliz fallaría por una razón que no tiene que ver con lo que prueba.
RELOJ_DISPOSITIVO = "2026-09-29T17:42:03.250Z"
RELOJ_SERVIDOR = datetime(2026, 9, 29, 17, 42, 10, tzinfo=UTC)


@pytest.fixture
async def escenario(sesion: AsyncSession, semilla: dict) -> dict:
    """Un cliente con línea de crédito, un producto en caja de 24, y su precio."""
    ids = {
        "dispositivo": uuid.uuid4(),
        "cliente": uuid.uuid4(),
        "producto": uuid.uuid4(),
    }
    await sesion.execute(
        text(
            "INSERT INTO dispositivos(id, usuario_id, etiqueta) "
            "VALUES (:id, :u, 'POCO M5s de pruebas')"
        ),
        {"id": ids["dispositivo"], "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO clientes(id, codigo, nombre_comercial, ruta_id, "
            "lista_precios_id, permite_credito, limite_credito, dias_credito, "
            "lat, lng, creado_en, actualizado_en) "
            "VALUES (:id, :cod, 'Abarrotes Doña Mary', :r, :l, true, 5000, 15, "
            "19.4326000, -99.1332000, now(), now())"
        ),
        {
            "id": ids["cliente"],
            "cod": f"CLI-{uuid.uuid4().hex[:8]}",
            "r": semilla["ruta"],
            "l": semilla["lista_precios"],
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO productos(id, sku, nombre, unidad_base) "
            "VALUES (:id, :sku, 'Sopa de fideo 70 g', 'PZA')"
        ),
        {"id": ids["producto"], "sku": f"SOPA-{uuid.uuid4().hex[:6]}"},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades(producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true), (:p, 'CAJA', 24, false)"
        ),
        {"p": ids["producto"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO precios(lista_id, producto_id, unidad_codigo, precio, version) "
            "VALUES (:l, :p, 'CAJA', :precio, 7)"
        ),
        {"l": semilla["lista_precios"], "p": ids["producto"], "precio": Decimal(PRECIO_CAJA)},
    )
    await sesion.commit()
    return {**semilla, **ids}


def contexto(escenario: dict, **extra) -> Contexto:
    extra.setdefault("recibido_en", RELOJ_SERVIDOR)
    return Contexto(
        dispositivo_id=escenario["dispositivo"],
        usuario_id=escenario["vendedor"],
        rutas=(escenario["ruta"],),
        almacen_id=escenario["camion"],
        **extra,
    )


def payload(escenario: dict, **cambios) -> dict:
    """El payload que arma el cliente Dart, con dos cajas."""
    base = {
        "folio_consecutivo": 124,
        "folio_local": "VEND01-000124",
        "cliente_id": str(escenario["cliente"]),
        "vendedor_id": str(escenario["vendedor"]),
        "dispositivo_id": str(escenario["dispositivo"]),
        "almacen_id": str(escenario["camion"]),
        "tipo": "contado",
        "lista_precios_id": str(escenario["lista_precios"]),
        "lista_precios_version": 7,
        "subtotal": "592.00",
        "descuento": "0.00",
        "impuestos": "0.00",
        "total": "592.00",
        "lat": "19.4326000",
        "lng": "-99.1332000",
        "ubicacion_precision_m": "8.50",
        "fecha_dispositivo": RELOJ_DISPOSITIVO,
        "fecha_operativa": "2026-09-29",
        "visita_id": str(uuid.uuid4()),
        "partidas": [
            {
                "id": str(uuid.uuid4()),
                "linea": 1,
                "producto_id": str(escenario["producto"]),
                "unidad_codigo": "CAJA",
                "factor_unidad": "24.0000",
                "cantidad": "2.000",
                "cantidad_base": "48.000",
                "precio_unitario": PRECIO_CAJA,
                "descuento": "0.00",
                "importe": "592.00",
            }
        ],
    }
    base.update(cambios)
    return base


async def aplicar(sesion: AsyncSession, escenario: dict, datos: dict, **ctx_extra):
    venta_id = uuid.uuid4()
    manejador = obtener_manejador("venta.crear")
    await manejador(sesion, contexto(escenario, **ctx_extra), venta_id, datos)
    return venta_id


async def leer_venta(sesion: AsyncSession, venta_id: uuid.UUID):
    return (
        await sesion.execute(
            text("SELECT * FROM ventas WHERE id = :id"), {"id": venta_id}
        )
    ).mappings().one()


# ---------------------------------------------------------------------------
# El camino feliz
# ---------------------------------------------------------------------------


async def test_la_venta_se_registra_completa(sesion: AsyncSession, escenario: dict):
    venta_id = await aplicar(sesion, escenario, payload(escenario))
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert venta["folio_local"] == "VEND01-000124"
    assert venta["total"] == Decimal("592.00")
    assert venta["requiere_revision"] is False
    assert venta["revision_motivos"] == []
    # El folio global lo asigna el servidor, no el teléfono.
    assert venta["folio_servidor"] > 0
    # El vendedor sale del token, nunca del payload.
    assert venta["vendedor_id"] == escenario["vendedor"]

    partida = (
        await sesion.execute(
            text("SELECT * FROM venta_partidas WHERE venta_id = :v"), {"v": venta_id}
        )
    ).mappings().one()
    assert partida["cantidad_base"] == Decimal("48.000")
    assert partida["importe"] == Decimal("592.00")


async def test_reenviar_el_mismo_sobre_no_duplica_el_ticket(
    sesion: AsyncSession, escenario: dict
):
    """Idempotencia: el UUID lo generó el teléfono y es la llave primaria."""
    venta_id = uuid.uuid4()
    manejador = obtener_manejador("venta.crear")
    datos = payload(escenario)

    await manejador(sesion, contexto(escenario), venta_id, datos)
    await sesion.commit()
    await manejador(sesion, contexto(escenario), venta_id, datos)
    await sesion.commit()

    cuantas = (
        await sesion.execute(
            text("SELECT count(*) FROM ventas WHERE id = :id"), {"id": venta_id}
        )
    ).scalar_one()
    assert cuantas == 1
    partidas = (
        await sesion.execute(
            text("SELECT count(*) FROM venta_partidas WHERE venta_id = :v"),
            {"v": venta_id},
        )
    ).scalar_one()
    assert partidas == 1, "el reenvío duplicó las partidas"


async def test_el_vendedor_no_puede_declararse_otro(
    sesion: AsyncSession, escenario: dict
):
    """Si el dispositivo pudiera decir a nombre de quién vende, un equipo
    comprometido escribiría en la ruta de cualquier otro."""
    otro = uuid.uuid4()
    venta_id = await aplicar(
        sesion, escenario, payload(escenario, vendedor_id=str(otro))
    )
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert venta["vendedor_id"] == escenario["vendedor"]


# ---------------------------------------------------------------------------
# §0.1 — LO QUE SE MARCA Y NO SE RECHAZA
# ---------------------------------------------------------------------------


async def test_pasarse_del_limite_de_credito_se_marca_y_la_venta_entra(
    sesion: AsyncSession, escenario: dict
):
    """El caso más tentador de rechazar, y el que más daño haría.

    El teléfono ya bloqueó lo que podía bloquear, con el saldo que tenía. Si el
    saldo real era mayor, la mercancía ya salió igual: rechazarla aquí borra el
    registro de una deuda que existe.
    """
    await sesion.execute(
        text("UPDATE clientes SET limite_credito = 100 WHERE id = :c"),
        {"c": escenario["cliente"]},
    )
    await sesion.commit()

    venta_id = await aplicar(sesion, escenario, payload(escenario, tipo="credito"))
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert venta["requiere_revision"] is True
    assert MOTIVO_EXCEDE_CREDITO in venta["revision_motivos"]
    assert venta["total"] == Decimal("592.00"), "la venta tiene que existir igual"


async def test_un_cliente_bloqueado_a_credito_se_marca_y_entra(
    sesion: AsyncSession, escenario: dict
):
    await sesion.execute(
        text("UPDATE clientes SET bloqueado = true WHERE id = :c"),
        {"c": escenario["cliente"]},
    )
    await sesion.commit()

    venta_id = await aplicar(sesion, escenario, payload(escenario, tipo="credito"))
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_CLIENTE_BLOQUEADO in venta["revision_motivos"]


async def test_un_precio_viejo_se_marca_y_NO_se_corrige(
    sesion: AsyncSession, escenario: dict
):
    """El teléfono vendió con la lista que tenía sincronizada. Si la oficina
    subió el precio mientras el vendedor estaba sin señal, corregir el importe
    aquí sería cobrarle al cliente algo distinto de lo que dice su papel."""
    await sesion.execute(
        text(
            "UPDATE precios SET precio = 310.0000, version = 8 "
            "WHERE lista_id = :l AND producto_id = :p AND unidad_codigo = 'CAJA'"
        ),
        {"l": escenario["lista_precios"], "p": escenario["producto"]},
    )
    await sesion.commit()

    venta_id = await aplicar(sesion, escenario, payload(escenario))
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_PRECIO_DESACTUALIZADO in venta["revision_motivos"]
    # El importe es el que se imprimió, no el nuevo.
    assert venta["total"] == Decimal("592.00")
    partida = (
        await sesion.execute(
            text("SELECT precio_unitario FROM venta_partidas WHERE venta_id = :v"),
            {"v": venta_id},
        )
    ).scalar_one()
    assert partida == Decimal("296.0000")


async def test_vender_lejos_del_domicilio_se_marca_con_su_distancia(
    sesion: AsyncSession, escenario: dict
):
    """El geosello es la mejor herramienta antifraude de la operación, y sigue
    siendo una pregunta para la oficina, no un veredicto: el cliente pudo
    recibir la mercancía en la banqueta de enfrente, o el domicilio registrado
    puede estar mal."""
    # Unos 2 km al norte.
    venta_id = await aplicar(
        sesion, escenario, payload(escenario, lat="19.4506000")
    )
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_LEJOS_DEL_CLIENTE in venta["revision_motivos"]
    assert venta["distancia_cliente_m"] > 1000


async def test_vender_sin_GPS_se_marca_y_la_venta_entra(
    sesion: AsyncSession, escenario: dict
):
    """Dentro de un mercado techado no hay satélite. Exigir coordenadas sería
    impedir vender justo donde más se vende."""
    datos = payload(escenario)
    datos.pop("lat")
    datos.pop("lng")

    venta_id = await aplicar(sesion, escenario, datos)
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_SIN_GEOSELLO in venta["revision_motivos"]
    assert venta["lat"] is None


async def test_un_reloj_desfasado_se_marca_y_se_guardan_los_dos(
    sesion: AsyncSession, escenario: dict
):
    """El reloj del teléfono miente. No invalida la venta, pero explica por qué
    los reportes por hora no cuadran.

    Se mide con `enviado_en` —el reloj del teléfono AL MANDAR— contra el instante
    en que el servidor recibe. Entre esas dos lecturas solo hay red, así que una
    diferencia de horas es el reloj.
    """
    venta_id = await aplicar(
        sesion,
        escenario,
        payload(escenario),
        enviado_en=RELOJ_SERVIDOR - timedelta(hours=5),
    )
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_RELOJ_DESFASADO in venta["revision_motivos"]
    assert abs(venta["desfase_reloj_seg"]) > 3600
    # Los dos relojes se conservan.
    assert venta["fecha_dispositivo"] is not None
    assert venta["fecha_servidor"] is not None


async def test_una_venta_que_espero_en_la_cola_NO_es_un_reloj_desfasado(
    sesion: AsyncSession, escenario: dict
):
    """El fallo que esta prueba cierra marcaba casi todas las ventas de una ruta.

    El desfase se calculaba como `recibido_en - fecha_dispositivo`: el instante en
    que el servidor recibe menos el instante en que el teléfono capturó la venta.
    Eso no es el reloj, es **cuánto esperó la venta en la cola** — y en este
    sistema eso es de horas POR DISEÑO: el vendedor sale a las siete, vende sin
    señal toda la mañana y sincroniza al regresar.

    Con el umbral en una hora, toda venta capturada con más de una hora de
    antelación salía marcada «el reloj del equipo está desfasado». La pantalla de
    revisión lo dice en su encabezado: marcar sin que nadie mire convierte la
    bandera en ruido. Una bandera que se enciende siempre no se mira, y entonces
    el día que de verdad haya un reloj mal nadie lo va a ver.
    """
    # El reloj del servidor en las pruebas es fijo: se mide contra ése.
    venta_id = await aplicar(
        sesion,
        escenario,
        # Capturada hace ocho horas: una venta normal de la mañana.
        payload(
            escenario,
            fecha_dispositivo=(RELOJ_SERVIDOR - timedelta(hours=8)).isoformat(),
        ),
        # Y mandada ahora, con el reloj del teléfono EN HORA con el servidor.
        enviado_en=RELOJ_SERVIDOR,
    )
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_RELOJ_DESFASADO not in (venta["revision_motivos"] or []), (
        "una venta que esperó en la cola se marcó como reloj desfasado: con eso "
        "se marca casi toda la ruta y la pantalla de revisión deja de servir"
    )


async def test_sin_el_reloj_del_telefono_no_se_inventa_un_desfase(
    sesion: AsyncSession, escenario: dict
):
    """Una app vieja no manda `enviado_en`, y entonces no se puede medir.

    No saber no es lo mismo que estar bien, pero inventar un desfase es peor:
    marcaría ventas legítimas y escondería las que de verdad tienen el reloj mal.
    """
    venta_id = await aplicar(
        sesion,
        escenario,
        payload(escenario, fecha_dispositivo="2026-09-27T17:42:03.250Z"),
    )
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_RELOJ_DESFASADO not in (venta["revision_motivos"] or [])


async def test_sin_lista_de_precios_se_marca_y_la_venta_entra(
    sesion: AsyncSession, escenario: dict
):
    """Le falta trazabilidad, no coherencia. Es el constraint que quité de la
    migración 0012 por esta misma razón."""
    datos = payload(escenario)
    datos.pop("lista_precios_id")
    datos.pop("lista_precios_version")

    venta_id = await aplicar(sesion, escenario, datos)
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    assert MOTIVO_SIN_LISTA in venta["revision_motivos"]
    assert venta["lista_precios_id"] is None


async def test_varios_motivos_se_acumulan_sin_repetirse(
    sesion: AsyncSession, escenario: dict
):
    """La oficina necesita ver TODO lo que hay que revisar de una venta, no el
    primer problema que apareció."""
    datos = payload(escenario, tipo="credito")
    datos.pop("lat")
    datos.pop("lng")
    await sesion.execute(
        text("UPDATE clientes SET limite_credito = 1, bloqueado = true WHERE id = :c"),
        {"c": escenario["cliente"]},
    )
    await sesion.commit()

    venta_id = await aplicar(sesion, escenario, datos)
    await sesion.commit()

    venta = await leer_venta(sesion, venta_id)
    motivos = venta["revision_motivos"]
    assert MOTIVO_SIN_GEOSELLO in motivos
    assert MOTIVO_CLIENTE_BLOQUEADO in motivos
    assert MOTIVO_EXCEDE_CREDITO in motivos
    assert len(motivos) == len(set(motivos)), "los motivos se repitieron"


# ---------------------------------------------------------------------------
# LO QUE SÍ SE RECHAZA: lo que no describe ninguna venta posible
# ---------------------------------------------------------------------------


async def test_un_total_que_no_cuadra_va_a_cuarentena(
    sesion: AsyncSession, escenario: dict
):
    with pytest.raises(ErrorDeManejador) as e:
        await aplicar(sesion, escenario, payload(escenario, total="500.00"))
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO


async def test_un_importe_de_linea_que_no_cuadra_va_a_cuarentena(
    sesion: AsyncSession, escenario: dict
):
    """El mismo cálculo que hizo el teléfono y que verifica el CHECK de
    PostgreSQL. Si los tres no coinciden al centavo, aquí se ve primero."""
    datos = payload(escenario)
    datos["partidas"][0]["importe"] = "580.00"
    datos["subtotal"] = "580.00"
    datos["total"] = "580.00"

    with pytest.raises(ErrorDeManejador) as e:
        await aplicar(sesion, escenario, datos)
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO
    assert "no cuadra" in e.value.mensaje


async def test_una_cantidad_base_que_no_cuadra_va_a_cuarentena(
    sesion: AsyncSession, escenario: dict
):
    """Si la partida y el movimiento de inventario cuentan historias distintas,
    el descuadre aparece en la liquidación sin forma de investigarlo."""
    datos = payload(escenario)
    datos["partidas"][0]["cantidad_base"] = "24.000"

    with pytest.raises(ErrorDeManejador):
        await aplicar(sesion, escenario, datos)


async def test_una_venta_sin_partidas_no_es_una_venta(
    sesion: AsyncSession, escenario: dict
):
    with pytest.raises(ErrorDeManejador) as e:
        await aplicar(sesion, escenario, payload(escenario, partidas=[]))
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO


async def test_el_dinero_como_numero_JSON_se_rechaza(
    sesion: AsyncSession, escenario: dict
):
    """El contrato exige string. Convertirlo en silencio aceptaría el float que
    el contrato prohíbe (contracts/README.md §1)."""
    with pytest.raises(ErrorDeManejador) as e:
        await aplicar(sesion, escenario, payload(escenario, total=592.00))
    assert e.value.codigo == CodigoError.PAYLOAD_INVALIDO


async def test_un_folio_como_string_se_rechaza(sesion: AsyncSession, escenario: dict):
    with pytest.raises(ErrorDeManejador):
        await aplicar(sesion, escenario, payload(escenario, folio_consecutivo="124"))


async def test_una_venta_de_otra_ruta_se_rechaza(
    sesion: AsyncSession, escenario: dict
):
    """El alcance por ruta no es una regla de negocio blanda: es la frontera que
    impide que un equipo comprometido escriba en la cartera de otro vendedor."""
    otra_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas(id, codigo, nombre) VALUES (:r, 'R99', 'Otra')"),
        {"r": otra_ruta},
    )
    await sesion.execute(
        text("UPDATE clientes SET ruta_id = :r WHERE id = :c"),
        {"r": otra_ruta, "c": escenario["cliente"]},
    )
    await sesion.commit()

    with pytest.raises(ErrorDeManejador) as e:
        await aplicar(sesion, escenario, payload(escenario))
    assert e.value.codigo == CodigoError.CONFLICTO_DE_DATOS


async def test_una_venta_de_un_cliente_que_no_existe_se_rechaza(
    sesion: AsyncSession, escenario: dict
):
    """Una venta sin cliente no se puede cobrar ni auditar. El cliente viaja en
    el mismo sobre cuando se dio de alta en la calle, y el motor aplica las
    operaciones en orden; si no está, el sobre está mal armado."""
    with pytest.raises(ErrorDeManejador) as e:
        await aplicar(
            sesion, escenario, payload(escenario, cliente_id=str(uuid.uuid4()))
        )
    assert e.value.codigo == CodigoError.CONFLICTO_DE_DATOS


# ---------------------------------------------------------------------------
# La mercancía sale del camión
# ---------------------------------------------------------------------------
# Esto no existía: el esquema tiene el movimiento `'venta'` desde la migración
# 0004 y nada lo insertaba. El camión seguía marcando lo cargado todo el día.


async def test_una_venta_descuenta_la_mercancia_del_camion(
    sesion: AsyncSession, escenario: dict
):
    """El inventario del camión del vendedor no bajaba al vender.

    Síntoma en producción: se registra la venta, aparece en el tablero, y el
    inventario del camión en el panel sigue mostrando lo cargado. Ni el vendedor
    ni la oficina podían saber qué le queda a media ruta.
    """
    antes = await _existencia(sesion, escenario["camion"], escenario["producto"])

    await aplicar(sesion, escenario, payload(escenario))
    await sesion.commit()

    despues = await _existencia(sesion, escenario["camion"], escenario["producto"])
    assert despues < antes, (
        "la venta no descontó del camión: el panel seguiría mostrando la "
        "mercancía cargada y el arqueo no cuadraría con lo que hay en la caja"
    )


async def test_la_venta_queda_en_el_libro_mayor_de_inventario(
    sesion: AsyncSession, escenario: dict
):
    """`movimientos_inventario` es el libro mayor, y no tenía ni una venta.

    Sin esto la historia del inventario era incompleta por diseño accidental:
    cargas y mermas sí, ventas no. Y es el libro con el que se audita un
    descuadre.
    """
    venta_id = await aplicar(sesion, escenario, payload(escenario))
    await sesion.commit()

    filas = (
        await sesion.execute(
            text(
                "SELECT tipo, almacen_origen_id, almacen_destino_id, cantidad "
                "FROM movimientos_inventario WHERE documento_id = :v"
            ),
            {"v": venta_id},
        )
    ).mappings().all()

    assert filas, "la venta no dejó ningún movimiento de inventario"
    for fila in filas:
        assert fila["tipo"] == "venta"
        assert fila["almacen_origen_id"] == escenario["camion"]
        assert fila["almacen_destino_id"] is None, (
            "la mercancía vendida sale del sistema: se la llevó el cliente"
        )


async def test_el_camion_puede_quedar_en_negativo_por_una_venta_tardia(
    sesion: AsyncSession, escenario: dict
):
    """§0.1: el mundo físico ya ocurrió.

    MODELO-DATOS §4 describe este caso como el comportamiento correcto —«una
    venta que llega tarde cuando el camión ya marcaba cero no se rechaza: la
    mercancía ya salió»— y era inalcanzable, porque la venta nunca intentaba
    descontar. El negativo tiene que quedar VISIBLE: es lo que la liquidación
    cobra.
    """
    await sesion.execute(
        text(
            "UPDATE existencias SET cantidad = 0 "
            "WHERE almacen_id = :a AND producto_id = :p"
        ),
        {"a": escenario["camion"], "p": escenario["producto"]},
    )

    await aplicar(sesion, escenario, payload(escenario))
    await sesion.commit()

    assert await _existencia(sesion, escenario["camion"], escenario["producto"]) < 0, (
        "la venta tardía se rechazó o no se aplicó: la mercancía ya salió del "
        "camión y el sistema tiene que poder decirlo"
    )


async def _existencia(sesion: AsyncSession, almacen, producto) -> Decimal:
    valor = (
        await sesion.execute(
            text(
                "SELECT cantidad FROM existencias "
                "WHERE almacen_id = :a AND producto_id = :p"
            ),
            {"a": almacen, "p": producto},
        )
    ).scalar_one_or_none()
    return Decimal(valor or 0)
