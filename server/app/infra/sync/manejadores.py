"""Registro de manejadores de operación.

El motor de sincronización **no sabe qué es una venta**. Sabe recibir sobres,
garantizar idempotencia, aislar fallos y dejar rastro. Qué significa cada tipo
de operación lo dice su manejador, y cada fase registra los suyos:

    Fase 2  cliente.crear
    Fase 3  venta.crear
    Fase 5  cobro.crear
    Fase 6  merma.crear, no_drop.crear

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
                "limite_credito, lat, lng FROM clientes WHERE id = :id"
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
            saldo = (
                await sesion.execute(
                    text(
                        "SELECT COALESCE(SUM(importe_original - importe_pagado), 0) AS s "
                        "FROM cuentas_por_cobrar "
                        "WHERE cliente_id = :c AND estado = 'abierta'"
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
    desfase = int((ctx.recibido_en - fecha_dispositivo).total_seconds())
    if abs(desfase) > DESFASE_SOSPECHOSO_SEG:
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

    for cruda in partidas:
        if not isinstance(cruda, dict):
            raise ErrorDeManejador(CodigoError.PAYLOAD_INVALIDO, "partida mal formada")
        await _insertar_partida(sesion, ctx, entidad_id, cruda, motivos, lista_uuid)

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


async def _insertar_partida(
    sesion: AsyncSession,
    ctx: Contexto,
    venta_id: uuid.UUID,
    datos: dict[str, Any],
    motivos: list[str],
    lista_id: uuid.UUID | None,
) -> None:
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
