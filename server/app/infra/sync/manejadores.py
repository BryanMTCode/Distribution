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

from sqlalchemy.ext.asyncio import AsyncSession

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
