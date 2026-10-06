"""Registro de manejadores de operación.

El motor de sincronización **no sabe qué es una venta**. Sabe recibir sobres,
garantizar idempotencia, aislar fallos y dejar rastro. Qué significa cada tipo
de operación lo dice su manejador, y cada fase registra los suyos:

    Fase 2  cliente.crear
    Fase 3  venta.crear
    Fase 5  cobro.crear
    Fase 6  merma.crear, no_drop.crear
    Fase 7  traspaso.crear

Esa separación es lo que permitió probar el motor a fondo antes de que
existiera la primera pantalla de venta.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.importes import cantidad_base, importe_de_linea
from app.domain.sync.sobres import CodigoError
from app.infra.models import Cliente

__all__ = ["Contexto", "ErrorDeManejador", "manejador_de", "obtener_manejador"]


@dataclass
class Contexto:
    """Quién manda el sobre. Nunca se toma del payload.

    Si el dispositivo pudiera declarar a nombre de qué vendedor escribe, un
    equipo comprometido escribiría en la ruta de cualquier otro.
    """

    dispositivo_id: uuid.UUID
    usuario_id: uuid.UUID
    rutas: tuple[uuid.UUID, ...] = ()
    almacen_id: uuid.UUID | None = None
    recibido_en: datetime = field(default_factory=lambda: datetime.now(UTC))

    # El reloj DEL TELÉFONO en el instante de enviar el lote, si la app lo manda.
    #
    # Es lo único con lo que se puede medir un desfase de reloj de verdad. Ver
    # `_desfase_de_reloj`: comparar `fecha_dispositivo` contra `recibido_en` mide
    # el retraso de la cola, no el reloj, y en una operación offline ese retraso
    # es de horas por diseño.
    enviado_en: datetime | None = None


class ErrorDeManejador(Exception):
    """Fallo de negocio al aplicar una operación.

    Lleva un `CodigoError` porque el dispositivo decide qué hacer según ese
    código: los reintentables vuelven a la cola, los demás se dan por perdidos
    en el equipo y quedan en cuarentena del lado del servidor.
    """

    def __init__(self, codigo: CodigoError, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


Manejador = Callable[[AsyncSession, Contexto, uuid.UUID, dict[str, Any]], Awaitable[None]]

_REGISTRO: dict[str, Manejador] = {}


def manejador_de(tipo: str) -> Callable[[Manejador], Manejador]:
    def decorador(fn: Manejador) -> Manejador:
        if tipo in _REGISTRO:
            raise RuntimeError(f"ya hay un manejador registrado para '{tipo}'")
        _REGISTRO[tipo] = fn
        return fn

    return decorador


def obtener_manejador(tipo: str) -> Manejador:
    manejador = _REGISTRO.get(tipo)
    if manejador is None:
        raise ErrorDeManejador(
            CodigoError.TIPO_DESCONOCIDO,
            f"el servidor no conoce el tipo de operación '{tipo}'",
        )
    return manejador


def _texto(datos: dict[str, Any], clave: str, *, obligatorio: bool = False) -> str | None:
    valor = datos.get(clave)
    if valor is None:
        if obligatorio:
            raise ErrorDeManejador(
                CodigoError.PAYLOAD_INVALIDO, f"falta el campo obligatorio '{clave}'"
            )
        return None
    if not isinstance(valor, str):
        raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, f"'{clave}' debe ser texto")
    return valor.strip() or None


def _decimal(datos: dict[str, Any], clave: str) -> Decimal | None:
    """El dinero y las coordenadas viajan como string (contracts/README.md §1).

    Un número suelto en el JSON es un error del cliente, no algo que convertir
    en silencio: convertirlo aceptaría el float que el contrato prohíbe.
    """
    valor = datos.get(clave)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO,
            f"'{clave}' debe venir como string, no como número JSON",
        )
    try:
        return Decimal(valor)
    except InvalidOperation as e:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"'{clave}' no es un número válido: {valor!r}"
        ) from e


# ---------------------------------------------------------------------------
# Fase 2 · alta de cliente en campo
# ---------------------------------------------------------------------------

@manejador_de("cliente.crear")
async def crear_cliente(
    sesion: AsyncSession, ctx: Contexto, entidad_id: uuid.UUID, datos: dict[str, Any]
) -> None:
    """Alta de cliente hecha en la calle.

    Idempotente por construcción: el UUID lo generó el teléfono y es la llave
    primaria, así que reenviar el sobre no crea un segundo cliente.

    Un cliente de campo nace **sin línea de crédito** (ADR 0002): esa decisión
    es de la oficina, y aceptarla desde el dispositivo sería dejar que el
    vendedor se autorice su propia cartera.
    """
    if await sesion.get(Cliente, entidad_id) is not None:
        return

    nombre = _texto(datos, "nombre_comercial", obligatorio=True)
    origen = _texto(datos, "ubicacion_origen")
    if origen not in (None, "gps", "manual"):
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"ubicacion_origen inválido: {origen!r}"
        )

    ruta = datos.get("ruta_id")
    ruta_id = uuid.UUID(ruta) if isinstance(ruta, str) else None
    if ruta_id is not None and ctx.rutas and ruta_id not in ctx.rutas:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS, "la ruta indicada no pertenece al vendedor"
        )
    if ruta_id is None:
        ruta_id = ctx.rutas[0] if ctx.rutas else None

    lat, lng = _decimal(datos, "lat"), _decimal(datos, "lng")
    if (lat is None) != (lng is None):
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, "lat y lng deben venir juntas o ninguna"
        )
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, "coordenadas fuera de rango")

    ahora = ctx.recibido_en
    sesion.add(
        Cliente(
            id=entidad_id,
            nombre_comercial=nombre,
            ruta_id=ruta_id,
            canal_codigo=_texto(datos, "canal_codigo"),
            contacto_nombre=_texto(datos, "contacto_nombre"),
            telefono=_texto(datos, "telefono"),
            calle=_texto(datos, "calle"),
            numero=_texto(datos, "numero"),
            colonia=_texto(datos, "colonia"),
            municipio=_texto(datos, "municipio"),
            referencias=_texto(datos, "referencias"),
            lat=lat,
            lng=lng,
            ubicacion_precision_m=_decimal(datos, "ubicacion_precision_m"),
            ubicacion_origen=origen,
            ubicacion_capturada_en=ahora if lat is not None else None,
            permite_credito=False,
            limite_credito=Decimal("0"),
            dias_credito=0,
            bloqueado=False,
            estatus="prospecto",
            origen_alta="campo",
            creado_por=ctx.usuario_id,
            dispositivo_id=ctx.dispositivo_id,
            creado_en=ahora,
            actualizado_en=ahora,
        )
    )
    await sesion.flush()


# ---------------------------------------------------------------------------
# Fase 3 · la venta
# ---------------------------------------------------------------------------
#
# AQUÍ VIVE EL PRINCIPIO §0.1, Y ES EL MANEJADOR QUE MÁS SE PRESTA A VIOLARLO.
#
# Cuando este sobre llega, la mercancía YA SALIÓ DEL CAMIÓN y el cliente YA
# TIENE SU REMISIÓN IMPRESA EN LA MANO. El hecho ocurrió. Rechazar la venta no
# devuelve la mercancía ni recoge el papel: solo borra el registro de que pasó,
# y entonces el descuadre aparece en la liquidación del final del día sin nada
# que lo explique.
#
# Por eso todo incumplimiento de REGLA se marca (`requiere_revision` con su
# motivo) y la venta se guarda igual. Lo único que se rechaza es lo que no
# describe ninguna venta posible: un payload que no se puede leer, o una
# aritmética que no cuadra. Eso es bug del cliente o manipulación, y va a
# cuarentena con el payload íntegro.
#
# La tentación constante es "validar bien". Validar bien aquí significa
# REGISTRAR bien.
# ---------------------------------------------------------------------------

# Los motivos que la oficina va a ver en la pantalla de revisión. Se nombran
# como preguntas que alguien puede investigar, no como códigos de error.
MOTIVO_PRECIO_DESACTUALIZADO = "precio_desactualizado"
MOTIVO_EXCEDE_CREDITO = "excede_limite_credito"
MOTIVO_SIN_LISTA = "sin_lista_de_precios"
MOTIVO_CLIENTE_BLOQUEADO = "cliente_bloqueado"
MOTIVO_LEJOS_DEL_CLIENTE = "fuera_de_geocerca"
MOTIVO_SIN_GEOSELLO = "sin_ubicacion"
MOTIVO_RELOJ_DESFASADO = "reloj_desfasado"


# Más allá de esta distancia, la venta se marca. No se rechaza: el cliente pudo
# haber recibido la mercancía en la banqueta de enfrente, o el domicilio
# registrado puede estar mal. Es una pregunta para la oficina, no un veredicto.
DISTANCIA_SOSPECHOSA_M = Decimal("300")

# El reloj del teléfono miente, y a veces por mucho. Un desfase grande no
# invalida la venta, pero sí explica por qué los reportes por hora no cuadran.
DESFASE_SOSPECHOSO_SEG = 3600


def _desfase_de_reloj(ctx: Contexto, fecha_dispositivo: datetime) -> int | None:
    """Cuánto miente el reloj del teléfono, en segundos. `None` si no se sabe.

    ───────────────────────────────────────────────────────────────────────────
    ESTO MEDÍA OTRA COSA, Y MARCABA CASI TODAS LAS VENTAS
    ───────────────────────────────────────────────────────────────────────────
    Antes era `recibido_en - fecha_dispositivo`: el instante en que el SERVIDOR
    recibió el sobre menos el instante en que el teléfono capturó la venta. Eso no
    es el desfase del reloj, es **cuánto tardó la venta en sincronizarse**.

    Y en este sistema ese retraso es de horas POR DISEÑO: el vendedor sale a las
    siete, vende sin señal toda la mañana y sincroniza al regresar. Con el umbral
    en una hora, casi todas las ventas del día salían marcadas «el reloj del
    equipo está desfasado». La pantalla de revisión lo dice en su propio
    encabezado: marcar sin que nadie mire convierte la bandera en ruido. Una
    bandera que se enciende siempre no se mira.

    Lo que sí mide el reloj es `recibido_en - enviado_en`: las dos lecturas son
    del MISMO instante —uno lo dice el teléfono al mandar, el otro el servidor al
    recibir— y entre ellas solo hay red. Si difieren en una hora, es el reloj.

    Sin `enviado_en` —una app vieja— se devuelve `None` y no se marca nada: no
    saber no es lo mismo que estar bien, pero inventar un desfase es peor que
    admitir que no se puede medir.
    """
    if ctx.enviado_en is None:
        return None
    return int((ctx.recibido_en - ctx.enviado_en).total_seconds())


@manejador_de("venta.crear")
async def crear_venta(
    sesion: AsyncSession, ctx: Contexto, entidad_id: uuid.UUID, datos: dict[str, Any]
) -> None:
    """Registra una venta hecha offline.

    Idempotente: el UUID lo generó el teléfono y es la llave primaria. Reenviar
    el sobre no duplica el ticket.
    """
    # Idempotencia antes que nada: si ya está, no se toca. Ni para "mejorarla".
    ya = await sesion.execute(
        text("SELECT 1 FROM ventas WHERE id = :id"), {"id": entidad_id}
    )
    if ya.first() is not None:
        return

    cliente_id = _uuid_obligatorio(datos, "cliente_id")
    folio_consecutivo = _entero(datos, "folio_consecutivo", obligatorio=True)
    folio_local = _texto(datos, "folio_local", obligatorio=True)
    tipo = _texto(datos, "tipo", obligatorio=True)
    if tipo not in ("contado", "credito"):
        raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, f"tipo inválido: {tipo!r}")

    partidas = datos.get("partidas")
    if not isinstance(partidas, list) or not partidas:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, "una venta sin partidas no es una venta"
        )

    subtotal = _decimal_obligatorio(datos, "subtotal")
    descuento = _decimal(datos, "descuento") or Decimal("0")
    impuestos = _decimal(datos, "impuestos") or Decimal("0")
    total = _decimal_obligatorio(datos, "total")

    # ARITMÉTICA: esto sí se rechaza. Un total que no es la suma de sus partes no
    # describe ninguna venta posible.
    if total != subtotal - descuento + impuestos:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO,
            f"el total {total} no cuadra con {subtotal} - {descuento} + {impuestos}",
        )

    motivos: list[str] = []

    # ------------------------------------------------------------------
    # Reglas: se MARCAN. La venta entra de todos modos.
    # ------------------------------------------------------------------
    cliente = (
        await sesion.execute(
            text(
                "SELECT ruta_id, lista_precios_id, permite_credito, bloqueado, "
                "limite_credito, dias_credito, lat, lng FROM clientes WHERE id = :id"
            ),
            {"id": cliente_id},
        )
    ).mappings().first()

    if cliente is None:
        # El cliente viaja en el MISMO sobre cuando se dio de alta en la calle, y
        # el motor aplica las operaciones en orden. Si no está, el sobre está mal
        # armado y no hay a quién colgarle la venta: esto sí se rechaza, porque
        # una venta sin cliente no se puede cobrar ni auditar.
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            f"la venta referencia un cliente que no existe: {cliente_id}",
        )

    if cliente["ruta_id"] is not None and ctx.rutas and cliente["ruta_id"] not in ctx.rutas:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS, "el cliente no pertenece a la ruta del vendedor"
        )

    # La lista de precios: le falta trazabilidad, no coherencia. Se marca (ver la
    # sección "LO QUE AQUÍ NO SE PUSO" en 0012_importes_rigidos.sql).
    #
    # Una lista DESCONOCIDA se trata igual que una ausente, y no por comodidad:
    # `ventas.lista_precios_id` tiene llave foránea, así que escribir un id que
    # el servidor no conoce tumbaría el sobre completo a cuarentena. El
    # dispositivo pudo vender con una lista que la oficina borró después; eso es
    # un dato de trazabilidad perdido, no una venta inválida. Mandarla a
    # cuarentena por eso sería violar §0.1 por una llave foránea.
    lista_uuid = _uuid_opcional(datos, "lista_precios_id")
    lista_version = _entero(datos, "lista_precios_version")
    if lista_uuid is not None:
        existe = await sesion.execute(
            text("SELECT 1 FROM listas_precios WHERE id = :l"), {"l": lista_uuid}
        )
        if existe.first() is None:
            lista_uuid = None
    if lista_uuid is None or lista_version is None:
        motivos.append(MOTIVO_SIN_LISTA)
        lista_uuid = None

    if tipo == "credito":
        if cliente["bloqueado"]:
            motivos.append(MOTIVO_CLIENTE_BLOQUEADO)
        if not cliente["permite_credito"]:
            motivos.append(MOTIVO_EXCEDE_CREDITO)
        else:
            # `estado <> 'liquidada'`, no `= 'abierta'`.
            #
            # Una factura pasa a 'parcial' en cuanto el cliente abona algo. Con el
            # filtro anterior, el resto de esa factura dejaba de contar contra su
            # límite: abonaba un peso y recuperaba toda su línea de crédito.
            #
            # El defecto era invisible hasta la Fase 5, porque sin cobranza nada
            # producía el estado 'parcial'. Es el mismo criterio que usa
            # `v_cartera_cliente` y el manejador de cobro.
            saldo = (
                await sesion.execute(
                    text(
                        "SELECT COALESCE(SUM(importe_original - importe_pagado), 0) AS s "
                        "FROM cuentas_por_cobrar "
                        "WHERE cliente_id = :c AND estado <> 'liquidada'"
                    ),
                    {"c": cliente_id},
                )
            ).scalar_one()
            if Decimal(saldo) + total > Decimal(cliente["limite_credito"]):
                motivos.append(MOTIVO_EXCEDE_CREDITO)

    # El geosello: la mejor herramienta antifraude que tiene la operación.
    lat, lng = _decimal(datos, "lat"), _decimal(datos, "lng")
    distancia = None
    if lat is None or lng is None:
        motivos.append(MOTIVO_SIN_GEOSELLO)
    elif cliente["lat"] is not None and cliente["lng"] is not None:
        distancia = (
            await sesion.execute(
                text(
                    "SELECT ST_Distance("
                    "  ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,"
                    "  ST_SetSRID(ST_MakePoint(:clng, :clat), 4326)::geography"
                    ") AS d"
                ),
                {
                    "lat": float(lat),
                    "lng": float(lng),
                    "clat": float(cliente["lat"]),
                    "clng": float(cliente["lng"]),
                },
            )
        ).scalar_one()
        if Decimal(str(distancia)) > DISTANCIA_SOSPECHOSA_M:
            motivos.append(MOTIVO_LEJOS_DEL_CLIENTE)

    fecha_dispositivo = _instante_obligatorio(datos, "fecha_dispositivo")
    desfase_medido = _desfase_de_reloj(ctx, fecha_dispositivo)
    desfase = desfase_medido or 0
    if desfase_medido is not None and abs(desfase_medido) > DESFASE_SOSPECHOSO_SEG:
        motivos.append(MOTIVO_RELOJ_DESFASADO)

    fecha_operativa = _texto(datos, "fecha_operativa", obligatorio=True)

    await sesion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id,
                                carga_id, visita_id, tipo, estado,
                                lista_precios_id, lista_precios_version,
                                subtotal, descuento, impuestos, total,
                                lat, lng, ubicacion_precision_m, distancia_cliente_m,
                                fecha_dispositivo, fecha_operativa, desfase_reloj_seg,
                                requiere_revision, revision_motivos, observaciones)
            VALUES (:id, :dispositivo, :folio, :folio_local, :cliente, :vendedor,
                    :ruta, :almacen, :carga, :visita, :tipo, 'confirmada',
                    :lista, :lista_version, :subtotal, :descuento, :impuestos,
                    :total, :lat, :lng, :precision, :distancia,
                    :fecha_dispositivo, :fecha_operativa, :desfase,
                    :revision, :motivos, :observaciones)
            """
        ),
        {
            "id": entidad_id,
            "dispositivo": ctx.dispositivo_id,
            "folio": folio_consecutivo,
            "folio_local": folio_local,
            "cliente": cliente_id,
            # Del token, nunca del payload: si el dispositivo pudiera declarar a
            # nombre de quién vende, un equipo comprometido escribiría en la ruta
            # de cualquier otro.
            "vendedor": ctx.usuario_id,
            "ruta": cliente["ruta_id"],
            "almacen": ctx.almacen_id,
            "carga": _uuid_opcional(datos, "carga_id"),
            "visita": _uuid_opcional(datos, "visita_id"),
            "tipo": tipo,
            "lista": lista_uuid,
            "lista_version": lista_version,
            "subtotal": subtotal,
            "descuento": descuento,
            "impuestos": impuestos,
            "total": total,
            "lat": lat,
            "lng": lng,
            "precision": _decimal(datos, "ubicacion_precision_m"),
            "distancia": Decimal(str(distancia)) if distancia is not None else None,
            "fecha_dispositivo": fecha_dispositivo,
            "fecha_operativa": fecha_operativa,
            "desfase": desfase,
            "revision": bool(motivos),
            "motivos": motivos,
            "observaciones": _texto(datos, "observaciones"),
        },
    )

    salidas: list[tuple[uuid.UUID, Decimal]] = []
    for cruda in partidas:
        if not isinstance(cruda, dict):
            raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, "partida mal formada")
        salidas.append(
            await _insertar_partida(sesion, ctx, entidad_id, cruda, motivos, lista_uuid)
        )

    # ------------------------------------------------------------------
    # LA MERCANCÍA SALE DEL CAMIÓN. Esto faltaba por completo.
    # ------------------------------------------------------------------
    # El esquema tiene el tipo de movimiento `'venta'` desde la migración 0004
    # —«camión → sale del sistema»— y NADA en el código lo insertaba nunca. O sea
    # que una venta registraba el documento, su cartera y su revisión, y el camión
    # seguía marcando la misma mercancía que al cargar.
    #
    # Lo que eso rompía, y no se ve desde la pantalla de ventas:
    #
    # · El inventario del camión en el panel se quedaba en lo cargado todo el día,
    #   así que nadie podía saber qué le queda a un vendedor a media ruta —ni él
    #   ni la oficina—.
    # · `existencias` nunca podía quedar en negativo por una venta, y MODELO-DATOS
    #   §4 describe justamente ese caso como el comportamiento correcto del §0.1:
    #   «una venta que llega tarde cuando el camión ya marcaba cero no se rechaza:
    #   la mercancía ya salió». No se rechazaba porque nunca se intentaba.
    # · `movimientos_inventario` —el libro mayor— no tenía una sola salida por
    #   venta, así que la historia del inventario era incompleta por diseño
    #   accidental: cargas y mermas sí, ventas no.
    #
    # El destino es NULL porque la mercancía sale del sistema: se la llevó el
    # cliente. Y se descuenta SIN comprobar disponible, a propósito: §0.1, el mundo
    # físico ya ocurrió. El negativo queda visible y la liquidación lo cobra.
    #
    # Es seguro hacerlo aquí: la ingesta descarta por `operacion_id` antes de
    # llamar al manejador, así que un reenvío no vuelve a descontar.
    await _sacar_del_camion(sesion, ctx, entidad_id, fecha_dispositivo, salidas, motivos)

    # ------------------------------------------------------------------
    # LA CUENTA POR COBRAR: una venta a crédito es una deuda, o no es nada.
    # ------------------------------------------------------------------
    # Esto faltaba, y el síntoma no se veía hasta que existió la cobranza: sin la
    # fila en `cuentas_por_cobrar`, el saldo del cliente se quedaba en cero para
    # siempre. Consecuencias, todas silenciosas:
    #
    #   · El límite de crédito NUNCA se alcanzaba: el vendedor podía dar crédito
    #     sin tope y la validación de arriba siempre pasaba.
    #   · Un cobro no tenía a qué aplicarse y caía como saldo a favor de un
    #     cliente que sí debía.
    #   · El panel y el delta de cartera mostraban una cartera vacía.
    #
    # Va en la MISMA transacción que la venta: una venta a crédito sin su deuda es
    # mercancía entregada que el sistema cree regalada.
    #
    # Idempotente por `venta_id`, que es la llave primaria de la tabla: reenviar el
    # sobre no crea una segunda deuda (y de todos modos el manejador ya salió por
    # arriba si la venta existía).
    if tipo == "credito":
        dias = _entero_de_fila(cliente, "dias_credito")
        await sesion.execute(
            text(
                """
                INSERT INTO cuentas_por_cobrar
                  (venta_id, cliente_id, importe_original, importe_pagado,
                   fecha_emision, fecha_vencimiento, estado, actualizado_en)
                VALUES (:venta, :cliente, :total, 0, :emision,
                        CAST(:emision AS date) + CAST(:dias AS integer),
                        'abierta', :ahora)
                ON CONFLICT (venta_id) DO NOTHING
                """
            ),
            {
                "venta": entidad_id,
                "cliente": cliente_id,
                "total": total,
                "emision": fecha_operativa,
                "dias": dias,
                "ahora": ctx.recibido_en,
            },
        )

    if motivos:
        # Se vuelve a escribir porque las partidas pueden agregar motivos
        # (un precio que no coincide con la lista vigente).
        await sesion.execute(
            text(
                "UPDATE ventas SET requiere_revision = true, revision_motivos = :m "
                "WHERE id = :id"
            ),
            {"m": sorted(set(motivos)), "id": entidad_id},
        )

    await sesion.flush()


async def _sacar_del_camion(
    sesion: AsyncSession,
    ctx: Contexto,
    venta_id: uuid.UUID,
    fecha_dispositivo: datetime,
    salidas: list[tuple[uuid.UUID, Decimal]],
    motivos: list[str],
) -> None:
    """Un movimiento `venta` por producto, y el descuento en `existencias`.

    Se agrupa por producto antes de escribir: una venta puede traer el mismo
    producto en dos renglones —una caja y tres piezas— y el libro mayor se lee
    mejor con una salida por producto que con una por renglón. El total es el
    mismo y `existencias` tampoco cambia.
    """
    # Si se llegó aquí, hay camión: `ventas.almacen_id` es NOT NULL, así que una
    # venta de un vendedor sin almacén asignado revienta en el INSERT de arriba y
    # el sobre entero va a la cuarentena del servidor con su error de integridad.
    #
    # Lo escribo porque mi primera versión de esta función hacía `return` en
    # silencio cuando `ctx.almacen_id` era None, "por si acaso". Era código
    # inalcanzable que además inventaba un tercer estado —venta registrada sin
    # inventario y sin avisar— para un caso que la base ya impide. Un `return`
    # defensivo sobre algo imposible solo añade un camino que nadie prueba.
    assert ctx.almacen_id is not None

    por_producto: dict[uuid.UUID, Decimal] = {}
    for producto_id, cantidad in salidas:
        por_producto[producto_id] = por_producto.get(producto_id, Decimal(0)) + cantidad

    for producto_id, cantidad in por_producto.items():
        if cantidad <= 0:
            continue
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
                   documento_tipo, documento_id, usuario_id, dispositivo_id,
                   fecha_dispositivo, fecha_servidor)
                VALUES ('venta', :origen, NULL, :p, :cantidad, 'venta', :doc,
                        :quien, :equipo, :fecha_dispositivo, :ahora)
                """
            ),
            {
                "origen": ctx.almacen_id,
                "p": producto_id,
                "cantidad": cantidad,
                "doc": venta_id,
                "quien": ctx.usuario_id,
                "equipo": ctx.dispositivo_id,
                "fecha_dispositivo": fecha_dispositivo,
                "ahora": ctx.recibido_en,
            },
        )
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:a, :p, :delta, :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad + :delta, actualizado_en = :ahora
                """
            ),
            {
                "a": ctx.almacen_id,
                "p": producto_id,
                "delta": -cantidad,
                "ahora": ctx.recibido_en,
            },
        )


async def _insertar_partida(
    sesion: AsyncSession,
    ctx: Contexto,
    venta_id: uuid.UUID,
    datos: dict[str, Any],
    motivos: list[str],
    lista_id: uuid.UUID | None,
) -> tuple[uuid.UUID, Decimal]:
    """Una partida. La aritmética se rechaza; el precio desactualizado se marca."""
    producto_id = _uuid_obligatorio(datos, "producto_id")
    unidad = _texto(datos, "unidad_codigo", obligatorio=True)
    cantidad = _decimal_obligatorio(datos, "cantidad")
    factor = _decimal_obligatorio(datos, "factor_unidad")
    precio = _decimal_obligatorio(datos, "precio_unitario")
    importe = _decimal_obligatorio(datos, "importe")
    descuento = _decimal(datos, "descuento") or Decimal("0")

    # El MISMO cálculo que hizo el teléfono y que va a verificar el CHECK de
    # PostgreSQL. Si los tres no coinciden al centavo, aquí se ve primero.
    esperado = importe_de_linea(cantidad, precio) - descuento
    if importe != esperado:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO,
            f"el importe {importe} de la línea no cuadra: se esperaba {esperado}",
        )

    base_declarada = _decimal(datos, "cantidad_base")
    base_esperada = cantidad_base(cantidad, factor)
    if base_declarada is not None and base_declarada != base_esperada:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO,
            f"cantidad_base {base_declarada} no cuadra: se esperaba {base_esperada}",
        )

    # El precio contra la lista VIGENTE. Si cambió mientras el teléfono estaba
    # sin señal, la venta se hizo con precios viejos: se marca, no se corrige.
    # Corregirlo sería cobrarle al cliente algo distinto de lo que dice su papel.
    if lista_id is not None:
        vigente = (
            await sesion.execute(
                text(
                    "SELECT precio FROM precios WHERE lista_id = :l "
                    "AND producto_id = :p AND unidad_codigo = :u"
                ),
                {"l": lista_id, "p": producto_id, "u": unidad},
            )
        ).scalar_one_or_none()
        if vigente is None or Decimal(vigente) != precio:
            motivos.append(MOTIVO_PRECIO_DESACTUALIZADO)

    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id,
                                        unidad_codigo, factor_unidad, cantidad,
                                        cantidad_base, precio_unitario, descuento,
                                        tasa_iva, importe)
            VALUES (:id, :venta, :linea, :producto, :unidad, :factor, :cantidad,
                    :base, :precio, :descuento, :iva, :importe)
            """
        ),
        {
            "id": _uuid_opcional(datos, "id") or uuid.uuid4(),
            "venta": venta_id,
            "linea": _entero(datos, "linea", obligatorio=True),
            "producto": producto_id,
            "unidad": unidad,
            "factor": factor,
            "cantidad": cantidad,
            "base": base_esperada,
            "precio": precio,
            "descuento": descuento,
            "iva": _decimal(datos, "tasa_iva") or Decimal("0"),
            "importe": importe,
        },
    )

    # Qué y cuánto salió, en unidad base. Lo usa `_sacar_del_camion`: se devuelve
    # desde aquí en vez de volver a leer el payload, porque `base_esperada` es el
    # valor ya VALIDADO contra cantidad × factor, y descontar del camión una
    # cantidad distinta de la que se facturó es la forma de descuadrar un arqueo.
    return producto_id, base_esperada


# ---------------------------------------------------------------------------
# Fase 5 · el cobro
# ---------------------------------------------------------------------------
#
# AQUÍ ENTRA DINERO, Y EL DINERO NO SE PUEDE RECONTAR
#
# Una venta entrega mercancía que se cuenta al final del día. Un cobro recibe
# efectivo, y el efectivo no se cuenta: se cuadra. Si este manejador rechaza un
# cobro legítimo, el dinero existe en la bolsa del vendedor y no en el sistema, y
# en la liquidación aparece como un descuadre que nadie puede explicar.
#
# Por eso aquí §0.1 es todavía más estricto que en la venta: **nada se rechaza
# salvo un payload que no describe ningún cobro posible.**

# El cobro excedió lo que el cliente debía. No es un error: el cliente pudo
# liquidar y dejar anticipo, o el saldo del teléfono estaba viejo. Se marca para
# que la oficina decida si es anticipo o devolución.
MOTIVO_EXCEDE_DEUDA = "cobro_excede_deuda"

# El cliente no tenía ninguna factura abierta. El dinero entró y queda todo como
# saldo a favor.
MOTIVO_SIN_DEUDA = "cobro_sin_deuda"

# Lo que el teléfono creía que debía, contra lo que el servidor sabe. Una
# diferencia grande significa que el equipo llevaba horas sin sincronizar, y
# explica por qué el vendedor cobró lo que cobró.
MOTIVO_SALDO_DESFASADO = "saldo_del_equipo_desfasado"
DESFASE_DE_SALDO_SOSPECHOSO = Decimal("500.00")

FORMAS_DE_PAGO = ("efectivo", "transferencia", "cheque")


@manejador_de("cobro.crear")
async def crear_cobro(
    sesion: AsyncSession, ctx: Contexto, entidad_id: uuid.UUID, datos: dict[str, Any]
) -> None:
    """Registra un abono y lo aplica a las facturas en orden FIFO.

    Idempotente: el UUID lo generó el teléfono y es la llave primaria. Reenviar el
    sobre no abona dos veces — que es la peor consecuencia posible de un reintento
    en este manejador.

    ────────────────────────────────────────────────────────────────────────
    POR QUÉ EL FIFO LO DECIDE EL SERVIDOR
    ────────────────────────────────────────────────────────────────────────
    El teléfono no conoce la cartera completa: trae un saldo en caché que puede
    tener horas y que no incluye los cobros que otros equipos hicieron hoy. Si
    decidiera la aplicación, dos dispositivos cobrando al mismo cliente aplicarían
    los dos abonos a la misma factura, y el servidor tendría que deshacer una
    decisión que ya está impresa en un papel.

    El orden es por **vencimiento más antiguo**: se paga primero lo que lleva más
    tiempo vencido, que es lo que reduce el riesgo real de la cartera.
    """
    ya = await sesion.execute(
        text("SELECT 1 FROM cobros WHERE id = :id"), {"id": entidad_id}
    )
    if ya.first() is not None:
        return

    cliente_id = _uuid_obligatorio(datos, "cliente_id")
    folio_consecutivo = _entero(datos, "folio_consecutivo", obligatorio=True)
    folio_local = _texto(datos, "folio_local", obligatorio=True)
    importe = _decimal_obligatorio(datos, "importe")
    forma_pago = _texto(datos, "forma_pago") or "efectivo"
    fecha_dispositivo = _instante_obligatorio(datos, "fecha_dispositivo")

    # Lo único que se rechaza: un payload que no describe ningún cobro posible.
    if importe <= 0:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"un cobro de {importe} no es un cobro"
        )
    if forma_pago not in FORMAS_DE_PAGO:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"forma de pago inválida: {forma_pago!r}"
        )

    cliente = (
        await sesion.execute(
            text("SELECT ruta_id, nombre_comercial FROM clientes WHERE id = :id"),
            {"id": cliente_id},
        )
    ).mappings().first()
    if cliente is None:
        # El cliente viaja en el MISMO sobre cuando se dio de alta en la calle, y
        # el sobre se aplica en orden. Si no está, el payload referencia algo que
        # no existe: un cobro sin cliente no se puede aplicar ni auditar.
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            f"el cobro referencia un cliente que no existe: {cliente_id}",
        )
    if cliente["ruta_id"] is not None and ctx.rutas and cliente["ruta_id"] not in ctx.rutas:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS, "el cliente no pertenece a la ruta del vendedor"
        )

    motivos: list[str] = []

    # ------------------------------------------------------------------
    # El saldo que traía el teléfono, contra el real. Forense, no autoridad.
    # ------------------------------------------------------------------
    saldo_real = (
        await sesion.execute(
            text(
                "SELECT COALESCE(sum(saldo), 0) FROM cuentas_por_cobrar "
                " WHERE cliente_id = :c AND estado <> 'liquidada'"
            ),
            {"c": cliente_id},
        )
    ).scalar_one()
    saldo_real = Decimal(saldo_real)

    saldo_del_equipo = _decimal(datos, "saldo_cache_disp")
    if (
        saldo_del_equipo is not None
        and abs(saldo_del_equipo - saldo_real) > DESFASE_DE_SALDO_SOSPECHOSO
    ):
        motivos.append(MOTIVO_SALDO_DESFASADO)

    await sesion.execute(
        text(
            """
            INSERT INTO cobros (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, visita_id, importe,
                                forma_pago, referencia, saldo_cache_disp,
                                lat, lng, estado, fecha_dispositivo, fecha_servidor,
                                fecha_operativa, requiere_revision, revision_motivos)
            VALUES (:id, :dispositivo, :folio, :folio_local, :cliente, :vendedor,
                    :visita, :importe, :forma, :referencia, :saldo_disp,
                    :lat, :lng, 'confirmado', :fecha_dispositivo, :ahora,
                    :fecha_operativa, :revision, :motivos)
            """
        ),
        {
            "id": entidad_id,
            "dispositivo": ctx.dispositivo_id,
            "folio": folio_consecutivo,
            "folio_local": folio_local,
            "cliente": cliente_id,
            "vendedor": ctx.usuario_id,
            "visita": _uuid_opcional(datos, "visita_id"),
            "importe": importe,
            "forma": forma_pago,
            "referencia": _texto(datos, "referencia"),
            "saldo_disp": saldo_del_equipo,
            "lat": _decimal(datos, "lat"),
            "lng": _decimal(datos, "lng"),
            "fecha_dispositivo": fecha_dispositivo,
            "ahora": ctx.recibido_en,
            "fecha_operativa": _texto(datos, "fecha_operativa")
            or fecha_dispositivo.date().isoformat(),
            "revision": bool(motivos),
            "motivos": motivos,
        },
    )

    # ------------------------------------------------------------------
    # La aplicación FIFO.
    # ------------------------------------------------------------------
    # `FOR UPDATE` sobre las facturas: dos cobros del mismo cliente llegando en
    # el mismo lote aplicarían los dos sobre el mismo saldo leído, y el segundo
    # sobrepasaría `pago_no_excede_original`. Serializar aquí es correcto porque
    # son las facturas de UN cliente, no de la cartera.
    facturas = (
        await sesion.execute(
            text(
                """
                SELECT venta_id, saldo FROM cuentas_por_cobrar
                 WHERE cliente_id = :c AND estado <> 'liquidada' AND saldo > 0
                 ORDER BY fecha_vencimiento, fecha_emision
                 FOR UPDATE
                """
            ),
            {"c": cliente_id},
        )
    ).mappings().all()

    if not facturas:
        motivos.append(MOTIVO_SIN_DEUDA)

    por_aplicar = importe
    aplicado_total = Decimal("0")
    for factura in facturas:
        if por_aplicar <= 0:
            break
        # Lo que cabe en esta factura. El sobrante pasa a la siguiente, y lo que
        # quede al final es saldo a favor.
        cabe = min(por_aplicar, Decimal(factura["saldo"]))
        if cabe <= 0:
            continue

        await sesion.execute(
            text(
                """
                INSERT INTO cobros_aplicaciones (cobro_id, venta_id, importe, aplicado_en)
                VALUES (:cobro, :venta, :importe, :ahora)
                """
            ),
            {
                "cobro": entidad_id,
                "venta": factura["venta_id"],
                "importe": cabe,
                "ahora": ctx.recibido_en,
            },
        )
        # `saldo` es una columna GENERADA (importe_original − importe_pagado): se
        # actualiza el pagado y la base recalcula. Escribir el saldo a mano daría
        # dos verdades que tarde o temprano no coinciden.
        await sesion.execute(
            text(
                """
                UPDATE cuentas_por_cobrar
                   SET importe_pagado = importe_pagado + :importe,
                       estado = CASE
                                  WHEN importe_pagado + :importe >= importe_original
                                    THEN 'liquidada'
                                  ELSE 'parcial'
                                END,
                       actualizado_en = :ahora
                 WHERE venta_id = :venta
                """
            ),
            {"importe": cabe, "venta": factura["venta_id"], "ahora": ctx.recibido_en},
        )
        por_aplicar -= cabe
        aplicado_total += cabe

    # ------------------------------------------------------------------
    # El sobrante es saldo a favor, nunca un error.
    # ------------------------------------------------------------------
    # El dinero ya cambió de manos. Rechazar el excedente haría que el vendedor se
    # guardara efectivo sin documento, que es exactamente lo que este manejador
    # existe para evitar. Se marca para que la oficina decida si es anticipo o
    # devolución.
    if por_aplicar > 0 and facturas:
        motivos.append(MOTIVO_EXCEDE_DEUDA)

    await sesion.execute(
        text(
            "UPDATE cobros SET importe_aplicado = :aplicado, saldo_a_favor = :favor, "
            "       requiere_revision = :revision, revision_motivos = :motivos "
            " WHERE id = :id"
        ),
        {
            "aplicado": aplicado_total,
            "favor": por_aplicar,
            "revision": bool(motivos),
            "motivos": motivos,
            "id": entidad_id,
        },
    )


# ---------------------------------------------------------------------------
# Fase 6 · mermas, devoluciones y no-drops
# ---------------------------------------------------------------------------
#
# LO QUE ESTOS DOCUMENTOS EVITAN
#
# Sin la merma, una caja que se rompe en el camión aparece en la liquidación como
# un faltante que el sistema no puede explicar, y **se le carga al vendedor**. Es
# el caso que le duele a un vendedor honesto y el único que no puede corregir
# después: para el cierre, el cartón roto ya se tiró.
#
# Sin el no-drop, un día de 20 visitas y 12 ventas se ve igual que un día de 12
# visitas y 12 ventas. Desde la oficina son indistinguibles, y el primero tiene
# ocho clientes que necesitan algo.

MOTIVO_SIN_EXISTENCIA_PARA_MERMA = "merma_sin_existencia"
MOTIVO_NO_DROP_SIN_NOTA = "no_drop_sin_nota"

# Un motivo que la oficina retiró DESPUÉS de que el vendedor capturó.
#
# No se rechaza: el teléfono le ofreció ese motivo porque era el catálogo que
# tenía, y mandar su merma a cuarentena lo castigaría por una edición de
# escritorio que ocurrió mientras él andaba en la calle. Un motivo que no existe
# en absoluto sí se rechaza, porque no hay nada a lo que mapearlo.
MOTIVO_MOTIVO_INACTIVO = "motivo_fuera_de_catalogo"

TIPOS_DE_MERMA = ("merma", "devolucion_cliente")


@manejador_de("merma.crear")
async def crear_merma(
    sesion: AsyncSession, ctx: Contexto, entidad_id: uuid.UUID, datos: dict[str, Any]
) -> None:
    """Registra una merma o una devolución de cliente.

    ────────────────────────────────────────────────────────────────────────
    LOS DOS SIGNOS, Y POR QUÉ IMPORTAN TANTO
    ────────────────────────────────────────────────────────────────────────
        merma               la mercancía SALE del camión
        devolucion_cliente  la mercancía ENTRA al camión

    Es el mismo documento visto al revés, y la liquidación los necesita juntos:
    `esperado = cargado − vendido − merma + devuelto`. Equivocar el signo produce
    un descuadre del **doble** del tamaño de la operación.

    ────────────────────────────────────────────────────────────────────────
    UNA MERMA NO SE RECHAZA POR FALTA DE EXISTENCIA
    ────────────────────────────────────────────────────────────────────────
    Si el camión marca 2 y se rompieron 3, el que está mal es el conteo. Se marca
    para que la oficina lo revise y se registra igual: rechazarla haría que la
    pérdida apareciera como faltante del vendedor, que es exactamente lo que este
    documento existe para evitar.
    """
    ya = await sesion.execute(
        text("SELECT 1 FROM mermas WHERE id = :id"), {"id": entidad_id}
    )
    if ya.first() is not None:
        return

    tipo = _texto(datos, "tipo", obligatorio=True)
    if tipo not in TIPOS_DE_MERMA:
        raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, f"tipo inválido: {tipo!r}")

    motivo_codigo = _texto(datos, "motivo_codigo", obligatorio=True)
    cliente_id = _uuid_opcional(datos, "cliente_id")
    if tipo == "devolucion_cliente" and cliente_id is None:
        # Lo impone también un CHECK de la base (`devolucion_requiere_cliente`),
        # pero su error no explica nada: sin cliente no se sabe a quién se le
        # recibió ni contra qué venta revisarlo.
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO,
            "una devolución sin cliente no se puede revisar contra su venta",
        )

    detalle = datos.get("detalle")
    if not isinstance(detalle, list) or not detalle:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, "una merma sin renglones no es una merma"
        )

    motivo = (
        await sesion.execute(
            text("SELECT activo FROM motivos_merma WHERE codigo = :c"),
            {"c": motivo_codigo},
        )
    ).mappings().first()
    if motivo is None:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            f"el motivo de merma {motivo_codigo!r} no existe en el catálogo",
        )

    # El almacén sale del contexto si el payload no lo trae: es el camión del
    # vendedor, y dejar que el dispositivo lo declare permitiría mermar el
    # inventario de otro.
    almacen_id = ctx.almacen_id or _uuid_opcional(datos, "almacen_id")
    if almacen_id is None:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            "no se sabe de qué almacén sale la mercancía",
        )

    fecha_dispositivo = _instante_obligatorio(datos, "fecha_dispositivo")
    motivos: list[str] = []
    if not motivo["activo"]:
        motivos.append(MOTIVO_MOTIVO_INACTIVO)

    await sesion.execute(
        text(
            """
            INSERT INTO mermas (id, dispositivo_id, folio_consecutivo, folio_local,
                                tipo, almacen_id, vendedor_id, cliente_id,
                                venta_origen_id, visita_id, motivo_codigo,
                                observaciones, lat, lng, estado,
                                fecha_dispositivo, fecha_servidor, fecha_operativa)
            VALUES (:id, :dispositivo, :folio, :folio_local, :tipo, :almacen,
                    :vendedor, :cliente, :venta_origen, :visita, :motivo,
                    :observaciones, :lat, :lng, 'confirmada',
                    :fecha_dispositivo, :ahora, :fecha_operativa)
            """
        ),
        {
            "id": entidad_id,
            "dispositivo": ctx.dispositivo_id,
            "folio": _entero(datos, "folio_consecutivo", obligatorio=True),
            "folio_local": _texto(datos, "folio_local", obligatorio=True),
            "tipo": tipo,
            "almacen": almacen_id,
            "vendedor": ctx.usuario_id,
            "cliente": cliente_id,
            "venta_origen": _uuid_opcional(datos, "venta_origen_id"),
            "visita": _uuid_opcional(datos, "visita_id"),
            "motivo": motivo_codigo,
            "observaciones": _texto(datos, "observaciones"),
            "lat": _decimal(datos, "lat"),
            "lng": _decimal(datos, "lng"),
            "fecha_dispositivo": fecha_dispositivo,
            "ahora": ctx.recibido_en,
            "fecha_operativa": _texto(datos, "fecha_operativa")
            or fecha_dispositivo.date().isoformat(),
        },
    )

    # El signo: una merma sale del camión, una devolución entra.
    sale = tipo == "merma"

    # Hacia dónde va lo mermado. Si la empresa configuró un almacén de merma, la
    # pérdida queda contabilizada ahí y el libro mayor cuadra en los dos lados; si
    # no existe, la mercancía sale del sistema y solo queda el movimiento de
    # salida. Lo que no se puede perder es el registro de que salió.
    #
    # Se busca UNA vez, no por renglón: una merma de quince productos no necesita
    # quince consultas para contestar siempre lo mismo.
    destino_merma: uuid.UUID | None = None
    if sale:
        destino_merma = (
            await sesion.execute(
                text(
                    "SELECT id FROM almacenes WHERE tipo = 'merma' AND activo "
                    " ORDER BY codigo LIMIT 1"
                )
            )
        ).scalar_one_or_none()

    for cruda in detalle:
        if not isinstance(cruda, dict):
            raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, "renglón mal formado")

        producto_id = _uuid_obligatorio(cruda, "producto_id")
        cantidad = _decimal_obligatorio(cruda, "cantidad_base")
        if cantidad <= 0:
            raise ErrorDeManejador(
                CodigoError.PAYLOAD_INVALIDO,
                f"un renglón de {cantidad} no describe ninguna merma",
            )

        if sale:
            disponible = (
                await sesion.execute(
                    text(
                        "SELECT cantidad FROM existencias "
                        " WHERE almacen_id = :a AND producto_id = :p"
                    ),
                    {"a": almacen_id, "p": producto_id},
                )
            ).scalar_one_or_none()
            # Se MARCA, no se rechaza. El cartón ya está roto.
            if (
                disponible is None or Decimal(disponible) < cantidad
            ) and MOTIVO_SIN_EXISTENCIA_PARA_MERMA not in motivos:
                motivos.append(MOTIVO_SIN_EXISTENCIA_PARA_MERMA)

        await sesion.execute(
            text(
                """
                INSERT INTO merma_detalle (id, merma_id, producto_id, cantidad_base, lote)
                VALUES (:id, :merma, :p, :cantidad, :lote)
                ON CONFLICT (merma_id, producto_id) DO UPDATE
                   SET cantidad_base = merma_detalle.cantidad_base + excluded.cantidad_base
                """
            ),
            {
                "id": _uuid_opcional(cruda, "id") or uuid.uuid4(),
                "merma": entidad_id,
                "p": producto_id,
                "cantidad": cantidad,
                "lote": _texto(cruda, "lote"),
            },
        )

        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
                   documento_tipo, documento_id, usuario_id, dispositivo_id,
                   fecha_dispositivo, fecha_servidor)
                VALUES (:tipo, :origen, :destino, :p, :cantidad, 'merma', :doc,
                        :quien, :equipo, :fecha_dispositivo, :ahora)
                """
            ),
            {
                "tipo": "merma" if sale else "devolucion",
                "origen": almacen_id if sale else None,
                "destino": destino_merma if sale else almacen_id,
                "p": producto_id,
                "cantidad": cantidad,
                "doc": entidad_id,
                "quien": ctx.usuario_id,
                "equipo": ctx.dispositivo_id,
                "fecha_dispositivo": fecha_dispositivo,
                "ahora": ctx.recibido_en,
            },
        )
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:a, :p, :delta, :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad + :delta, actualizado_en = :ahora
                """
            ),
            {
                "a": almacen_id,
                "p": producto_id,
                "delta": -cantidad if sale else cantidad,
                "ahora": ctx.recibido_en,
            },
        )
        if sale and destino_merma is not None:
            await sesion.execute(
                text(
                    """
                    INSERT INTO existencias (almacen_id, producto_id, cantidad,
                                             actualizado_en)
                    VALUES (:a, :p, :cantidad, :ahora)
                    ON CONFLICT (almacen_id, producto_id) DO UPDATE
                       SET cantidad = existencias.cantidad + :cantidad,
                           actualizado_en = :ahora
                    """
                ),
                {
                    "a": destino_merma,
                    "p": producto_id,
                    "cantidad": cantidad,
                    "ahora": ctx.recibido_en,
                },
            )

    if motivos:
        await sesion.execute(
            text(
                "UPDATE mermas SET requiere_revision = true, revision_motivos = :m "
                " WHERE id = :id"
            ),
            {"id": entidad_id, "m": motivos},
        )

    await sesion.flush()


@manejador_de("no_drop.crear")
async def crear_no_drop(
    sesion: AsyncSession, ctx: Contexto, entidad_id: uuid.UUID, datos: dict[str, Any]
) -> None:
    """Registra una visita que no terminó en venta.

    No mueve inventario ni dinero: es un **dato de efectividad**. Lo que mide es la
    diferencia entre un día de 20 visitas con 12 ventas y uno de 12 visitas con 12
    ventas, que desde la oficina son indistinguibles sin esto.

    Es el único documento del sistema que **rechaza** por falta de ubicación en vez
    de marcarla, y la razón está escrita donde se hace la validación: sin el sello
    de GPS no queda ningún hecho que preservar.
    """
    ya = await sesion.execute(
        text("SELECT 1 FROM no_drops WHERE id = :id"), {"id": entidad_id}
    )
    if ya.first() is not None:
        return

    cliente_id = _uuid_obligatorio(datos, "cliente_id")
    motivo_codigo = _texto(datos, "motivo_codigo", obligatorio=True)
    fecha_dispositivo = _instante_obligatorio(datos, "fecha_dispositivo")

    motivo = (
        await sesion.execute(
            text(
                "SELECT requiere_nota, activo FROM motivos_no_drop WHERE codigo = :c"
            ),
            {"c": motivo_codigo},
        )
    ).mappings().first()
    if motivo is None:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            f"el motivo de no-drop {motivo_codigo!r} no existe en el catálogo",
        )

    cliente = (
        await sesion.execute(
            text("SELECT ruta_id FROM clientes WHERE id = :id"), {"id": cliente_id}
        )
    ).mappings().first()
    if cliente is None:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            f"el no-drop referencia un cliente que no existe: {cliente_id}",
        )
    if cliente["ruta_id"] is not None and ctx.rutas and cliente["ruta_id"] not in ctx.rutas:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS, "el cliente no pertenece a la ruta del vendedor"
        )

    motivos: list[str] = []
    if not motivo["activo"]:
        motivos.append(MOTIVO_MOTIVO_INACTIVO)
    lat, lng = _decimal(datos, "lat"), _decimal(datos, "lng")
    nota = _texto(datos, "nota")

    if lat is None or lng is None:
        # LA ÚNICA EXCEPCIÓN A «MARCAR, NO RECHAZAR» (§0.1).
        #
        # En todo lo demás el servidor acepta y marca, porque el hecho físico ya
        # ocurrió y negarlo no lo deshace. Un no-drop sin ubicación es distinto:
        # NO HAY HECHO QUE PRESERVAR. Lo único que afirma el documento es "estuve
        # ahí y no compró", y sin coordenadas es indistinguible de "no fui".
        # Guardarlo marcado metería una visita no verificable a cada reporte de
        # efectividad; `no_drops.lat` es NOT NULL en las dos bases por esto.
        #
        # Y no se pierde: el rechazo manda el SOBRE COMPLETO a cuarentena, donde
        # la oficina lo ve con su payload intacto y decide. El dispositivo ni lo
        # produce —el registro local exige `Ubicacion`— así que llegar aquí sin
        # ella significa un cliente viejo o alterado, justo lo que la cuarentena
        # existe para atrapar.
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            "un no-drop sin ubicación no se distingue de una visita que no se hizo",
        )

    if motivo["requiere_nota"] and not nota:
        # Se marca y entra: el dato de que el cliente no compró vale aunque la
        # explicación venga vacía, y la oficina puede pedirla.
        motivos.append(MOTIVO_NO_DROP_SIN_NOTA)

    await sesion.execute(
        text(
            """
            INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                  vendedor_id, ruta_id, visita_id, motivo_codigo,
                                  nota, lat, lng, ubicacion_precision_m,
                                  fecha_dispositivo, fecha_servidor, fecha_operativa,
                                  requiere_revision, revision_motivos)
            VALUES (:id, :dispositivo, :folio, :cliente, :vendedor, :ruta, :visita,
                    :motivo, :nota, :lat, :lng, :precision,
                    :fecha_dispositivo, :ahora, :fecha_operativa, :revision, :motivos)
            """
        ),
        {
            "id": entidad_id,
            "dispositivo": ctx.dispositivo_id,
            "folio": _entero(datos, "folio_consecutivo", obligatorio=True),
            "cliente": cliente_id,
            "vendedor": ctx.usuario_id,
            "ruta": cliente["ruta_id"],
            "visita": _uuid_opcional(datos, "visita_id"),
            "motivo": motivo_codigo,
            "nota": nota,
            "lat": lat,
            "lng": lng,
            "precision": _decimal(datos, "ubicacion_precision_m"),
            "fecha_dispositivo": fecha_dispositivo,
            "ahora": ctx.recibido_en,
            "fecha_operativa": _texto(datos, "fecha_operativa")
            or fecha_dispositivo.date().isoformat(),
            "revision": bool(motivos),
            "motivos": motivos,
        },
    )
    await sesion.flush()


# ---------------------------------------------------------------------------
# Fase 7 · el vendedor devuelve mercancía a la bodega
# ---------------------------------------------------------------------------
#
# Hasta hoy la única forma de bajar mercancía de un camión eran dos ajustes
# independientes, uno en cada almacén. Las dos cifras acaban bien y no queda
# ningún documento que ate los dos lados: el día que alguien pregunte «¿quién
# bajó esas 18 cajas y quién las recibió?», no hay qué leer.

CODIGO_TRANSITO_SIN_SUCURSAL = "TRANSITO"


@manejador_de("traspaso.crear")
async def crear_traspaso(
    sesion: AsyncSession, ctx: Contexto, entidad_id: uuid.UUID, datos: dict[str, Any]
) -> None:
    """El vendedor declara mercancía que bajó del camión.

    ────────────────────────────────────────────────────────────────────────
    MUEVE CAMIÓN → TRÁNSITO, NUNCA CAMIÓN → BODEGA
    ────────────────────────────────────────────────────────────────────────
    Si la declaración del vendedor subiera la bodega, un faltante se podría cubrir
    escribiendo una devolución que nunca se entregó: su camión baja, la bodega
    sube, y nadie contó nada. Sería la única operación del sistema donde la palabra
    de una persona mueve dos almacenes.

    La bodega sube cuando alguien recibe y dice cuánto contó, desde el panel. Lo
    que no cuadre se queda en tránsito, con nombre y con fecha.

    ────────────────────────────────────────────────────────────────────────
    SIN GUARDA DE EXISTENCIA, COMO LA MERMA
    ────────────────────────────────────────────────────────────────────────
    Si el vendedor dice que bajó 18 cajas, bajó 18 cajas (§0.1). Que el sistema
    crea que traía 12 no cambia el hecho físico: el camión queda en −6, que es una
    señal honesta y visible, y rechazar el documento no devolvería la mercancía.

    ────────────────────────────────────────────────────────────────────────
    LA LIQUIDACIÓN NO NECESITÓ NINGÚN CAMBIO, Y VALE LA PENA SABER POR QUÉ
    ────────────────────────────────────────────────────────────────────────
    La ecuación del cierre no tiene término para «traspasado», y aun así un
    traspaso no se le cobra al vendedor: `inicial` se **deduce** del saldo vivo del
    camión (ver `saldo_inicial`), así que bajar las existencias baja `inicial` y
    baja `esperado` en la misma cantidad. El cierre siempre compara lo contado
    contra lo que el sistema tiene AHORA. Es el mismo mecanismo que absorbe los
    ajustes de la oficina, y es la razón por la que esta operación no toca
    `liquidacion_detalle`.
    """
    ya = await sesion.execute(
        text("SELECT 1 FROM traspasos WHERE id = :id"), {"id": entidad_id}
    )
    if ya.first() is not None:
        return

    # Del token, nunca del payload: dejar que el dispositivo declare el origen
    # permitiría vaciar el camión de otro vendedor.
    origen_id = ctx.almacen_id or _uuid_opcional(datos, "almacen_origen_id")
    if origen_id is None:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            "no se sabe de qué almacén sale la mercancía",
        )

    detalle = datos.get("detalle")
    if not isinstance(detalle, list) or not detalle:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO,
            "un traspaso sin renglones no devuelve nada",
        )

    origen = (
        await sesion.execute(
            text("SELECT tipo, codigo, sucursal_id FROM almacenes WHERE id = :a"),
            {"a": origen_id},
        )
    ).mappings().first()
    if origen is None:
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            "el almacén de origen no existe",
        )
    # Este documento describe UNA cosa: mercancía que baja de un camión. Si el
    # almacén del token fuera una bodega, el traspaso quedaría parado en tránsito
    # **sin pantalla que lo reciba** —la lista de recepción solo mira orígenes de
    # tipo camión— y la mercancía desaparecería de los dos almacenes sin que nadie
    # lo notara. Lo que sale de una bodega tiene su propio documento: una salida.
    #
    # Se rechaza y no se marca, aunque §0.1 diga lo contrario para los documentos de
    # campo: aquí no se está negando un hecho físico, se está rechazando un equipo
    # mal configurado. Un sobre en cuarentena se ve; un traspaso varado, no.
    if origen["tipo"] != "camion":
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            f"{origen['codigo']} no es un camión (es {origen['tipo']!r}): lo que sale "
            "de una bodega se registra con una salida, no con una devolución",
        )

    fecha_dispositivo = _instante_obligatorio(datos, "fecha_dispositivo")
    transito_id = await _almacen_de_transito(sesion, origen["sucursal_id"])

    folio = (
        await sesion.execute(text("SELECT nextval('seq_folio_traspaso')"))
    ).scalar_one()

    await sesion.execute(
        text(
            """
            INSERT INTO traspasos (id, folio, almacen_origen_id, almacen_destino_id,
                                   estado, solicitado_por, dispositivo_id,
                                   observaciones, fecha_dispositivo, fecha_operativa)
            VALUES (:id, :folio, :origen, :destino, 'propuesto', :quien, :equipo,
                    :observaciones, :fecha_dispositivo, :fecha_operativa)
            """
        ),
        {
            "id": entidad_id,
            "folio": f"TR-{folio:06d}",
            "origen": origen_id,
            "destino": transito_id,
            "quien": ctx.usuario_id,
            "equipo": ctx.dispositivo_id,
            "observaciones": _texto(datos, "observaciones"),
            "fecha_dispositivo": fecha_dispositivo,
            "fecha_operativa": _texto(datos, "fecha_operativa")
            or fecha_dispositivo.date().isoformat(),
        },
    )

    for cruda in detalle:
        if not isinstance(cruda, dict):
            raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, "renglón mal formado")

        producto_id = _uuid_obligatorio(cruda, "producto_id")
        cantidad = _decimal_obligatorio(cruda, "cantidad")
        if cantidad <= 0:
            raise ErrorDeManejador(
                CodigoError.PAYLOAD_INVALIDO,
                f"un renglón de {cantidad} no devuelve nada",
            )

        await sesion.execute(
            text(
                """
                INSERT INTO traspaso_detalle (id, traspaso_id, producto_id, cantidad)
                VALUES (:id, :traspaso, :p, :cantidad)
                ON CONFLICT (traspaso_id, producto_id) DO UPDATE
                   SET cantidad = traspaso_detalle.cantidad + excluded.cantidad
                """
            ),
            {
                "id": _uuid_opcional(cruda, "id") or uuid.uuid4(),
                "traspaso": entidad_id,
                "p": producto_id,
                "cantidad": cantidad,
            },
        )

        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
                   documento_tipo, documento_id, usuario_id, dispositivo_id,
                   fecha_dispositivo, fecha_servidor)
                VALUES ('traspaso', :origen, :destino, :p, :cantidad, 'traspaso', :doc,
                        :quien, :equipo, :fecha_dispositivo, :ahora)
                """
            ),
            {
                "origen": origen_id,
                "destino": transito_id,
                "p": producto_id,
                "cantidad": cantidad,
                "doc": entidad_id,
                "quien": ctx.usuario_id,
                "equipo": ctx.dispositivo_id,
                "fecha_dispositivo": fecha_dispositivo,
                "ahora": ctx.recibido_en,
            },
        )

        for almacen, delta in ((origen_id, -cantidad), (transito_id, cantidad)):
            await sesion.execute(
                text(
                    """
                    INSERT INTO existencias (almacen_id, producto_id, cantidad,
                                             actualizado_en)
                    VALUES (:a, :p, :delta, :ahora)
                    ON CONFLICT (almacen_id, producto_id) DO UPDATE
                       SET cantidad = existencias.cantidad + :delta,
                           actualizado_en = :ahora
                    """
                ),
                {
                    "a": almacen,
                    "p": producto_id,
                    "delta": delta,
                    "ahora": ctx.recibido_en,
                },
            )

    await sesion.flush()


async def _almacen_de_transito(
    sesion: AsyncSession, sucursal_id: uuid.UUID | None
) -> uuid.UUID:
    """El almacén de tránsito de la sucursal del camión, creándolo si falta.

    Se crea solo si no existe, y la razón es §0.1: la mercancía ya bajó del camión.
    Rechazar el documento porque nadie configuró un almacén de paso dejaría esas 18
    cajas en el camión de un vendedor que ya no las trae, y lo convertiría en un
    faltante suyo por una omisión de la oficina.

    Uno por sucursal: la mercancía en tránsito de la sucursal de Querétaro no tiene
    nada que ver con la de Guadalajara, y juntarlas haría imposible leer qué falta
    por recibir en cada bodega. Un camión sin sucursal cae a un tránsito general.
    """
    if sucursal_id is not None:
        existente = (
            await sesion.execute(
                text(
                    "SELECT id FROM almacenes "
                    " WHERE tipo = 'transito' AND sucursal_id = :s AND activo "
                    " ORDER BY codigo LIMIT 1"
                ),
                {"s": sucursal_id},
            )
        ).scalar_one_or_none()
    else:
        existente = (
            await sesion.execute(
                text(
                    "SELECT id FROM almacenes "
                    " WHERE tipo = 'transito' AND sucursal_id IS NULL AND activo "
                    " ORDER BY codigo LIMIT 1"
                )
            )
        ).scalar_one_or_none()
    if existente is not None:
        return existente

    codigo = CODIGO_TRANSITO_SIN_SUCURSAL
    nombre = "Tránsito"
    if sucursal_id is not None:
        sucursal = (
            await sesion.execute(
                text("SELECT codigo, nombre FROM sucursales WHERE id = :s"),
                {"s": sucursal_id},
            )
        ).mappings().first()
        if sucursal is not None:
            codigo = f"TRANSITO_{sucursal['codigo']}"
            nombre = f"Tránsito · {sucursal['nombre']}"

    # `ON CONFLICT (codigo)` y no un INSERT a secas: dos sobres del mismo lote (o de
    # dos vendedores de la misma sucursal) pueden llegar a crearlo a la vez, y el
    # segundo no debe reventar por una carrera que no es suya. `DO UPDATE SET
    # activo = true` porque un tránsito desactivado a mano tiene que volver: la
    # mercancía necesita dónde estar.
    fila = (
        await sesion.execute(
            text(
                """
                INSERT INTO almacenes (codigo, nombre, tipo, sucursal_id, activo)
                VALUES (:codigo, :nombre, 'transito', :sucursal, true)
                ON CONFLICT (codigo) DO UPDATE SET activo = true
                RETURNING id, tipo
                """
            ),
            {"codigo": codigo, "nombre": nombre, "sucursal": sucursal_id},
        )
    ).mappings().one()

    # Se revisa el TIPO de lo que devolvió el conflicto, y esto no es paranoia de
    # más: `codigo` es único en toda la tabla, así que si alguien ya bautizó una
    # BODEGA con este nombre, el `ON CONFLICT` devolvería esa bodega y la mercancía
    # del camión entraría derecho a ella —sin que nadie contara nada—, que es
    # exactamente el agujero que el almacén de tránsito existe para cerrar. Antes de
    # eso, el sobre se va a cuarentena con un mensaje que dice qué renombrar.
    if fila["tipo"] != "transito":
        raise ErrorDeManejador(
            CodigoError.CONFLICTO_DE_DATOS,
            f"el almacén {codigo!r} existe y no es de tránsito (es {fila['tipo']!r}): "
            "renómbralo, porque si no la mercancía del camión entraría a él sin que "
            "nadie la cuente",
        )
    return fila["id"]


def _entero_de_fila(fila: Any, clave: str, *, por_omision: int = 0) -> int:
    """Un entero de una fila de la base, con omisión.

    `dias_credito` es `NOT NULL DEFAULT 0`, pero la consulta que trae al cliente
    puede no pedirlo: leerlo con `.get` y caer a cero es más seguro que un
    `KeyError` que mandaría la venta a cuarentena por un campo de catálogo.
    """
    try:
        valor = fila[clave]
    except (KeyError, TypeError):
        return por_omision
    return int(valor) if valor is not None else por_omision


def _entero(datos: dict[str, Any], clave: str, *, obligatorio: bool = False) -> int | None:
    valor = datos.get(clave)
    if valor is None:
        if obligatorio:
            raise ErrorDeManejador(
                CodigoError.PAYLOAD_INVALIDO, f"falta el campo obligatorio '{clave}'"
            )
        return None
    # `bool` es subclase de `int` en Python: `True` pasaría como 1.
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"'{clave}' debe ser un entero sin comillas"
        )
    return valor


def _decimal_obligatorio(datos: dict[str, Any], clave: str) -> Decimal:
    valor = _decimal(datos, clave)
    if valor is None:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"falta el campo obligatorio '{clave}'"
        )
    return valor


def _uuid_obligatorio(datos: dict[str, Any], clave: str) -> uuid.UUID:
    valor = _uuid_opcional(datos, clave)
    if valor is None:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"falta el campo obligatorio '{clave}'"
        )
    return valor


def _uuid_opcional(datos: dict[str, Any], clave: str) -> uuid.UUID | None:
    valor = datos.get(clave)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, f"'{clave}' debe ser texto")
    try:
        return uuid.UUID(valor)
    except ValueError as e:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"'{clave}' no es un UUID: {valor!r}"
        ) from e


def _instante_obligatorio(datos: dict[str, Any], clave: str) -> datetime:
    texto = _texto(datos, clave, obligatorio=True)
    try:
        momento = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError as e:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"'{clave}' no es una fecha RFC 3339: {texto!r}"
        ) from e
    if momento.tzinfo is None:
        raise ErrorDeManejador(
            CodigoError.PAYLOAD_INVALIDO, f"'{clave}' debe traer zona horaria"
        )
    return momento.astimezone(UTC)
