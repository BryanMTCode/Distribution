"""Sobres de visita: la unidad de sincronización.

Un **sobre** agrupa todo lo que pasó en una visita —alta del cliente, venta,
cobro— y se aplica en una sola transacción. O entra completo o no entra nada:
una venta sin su cobro, o un cobro contra un cliente que no existe, son estados
que el negocio no puede representar.

El sobre es también la **llave de idempotencia**. Su `operacion_id` lo genera el
dispositivo; el servidor guarda ese id junto con el resultado, y si el mismo id
vuelve a llegar responde lo mismo sin reprocesar. Por eso reenviar un lote
completo tras un corte de red no duplica un solo ticket.

Módulo de dominio puro: sin imports de FastAPI ni de SQLAlchemy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.domain.canonico import PayloadNoCanonico, hash_payload

__all__ = [
    "CodigoError",
    "LoteInvalido",
    "Operacion",
    "Sobre",
    "hash_de_sobre",
    "validar_lote",
]

# Tope por lote. Un vendedor sin señal una semana acumula miles de operaciones;
# mandarlas de un golpe por 3G en un mercado es la forma más segura de que el
# lote nunca complete. Se mandan por tandas y cada tanda confirma la anterior.
MAX_SOBRES_POR_LOTE = 200


class CodigoError(StrEnum):
    """Códigos estables: el dispositivo decide qué hacer según este valor, no
    según el texto del mensaje."""

    # El sobre no vuelve a intentarse: va a cuarentena para revisión humana.
    HASH_NO_COINCIDE = "hash_no_coincide"
    PAYLOAD_INVALIDO = "payload_invalido"
    TIPO_DESCONOCIDO = "tipo_desconocido"
    REFERENCIA_INEXISTENTE = "referencia_inexistente"
    CONFLICTO_DE_DATOS = "conflicto_de_datos"
    # Reintentable: el dispositivo lo vuelve a mandar más tarde.
    ERROR_INTERNO = "error_interno"

    @property
    def reintentable(self) -> bool:
        return self is CodigoError.ERROR_INTERNO


class LoteInvalido(ValueError):
    """El lote está mal formado a nivel estructura.

    Distinto de un sobre que falla: aquí no se procesa nada, porque no se puede
    confiar en el contenedor.
    """


@dataclass(frozen=True)
class Operacion:
    """Un documento dentro del sobre."""

    tipo: str
    entidad_id: uuid.UUID
    # Se conserva TAL CUAL llegó. Si se normalizara —convertir a Decimal y de
    # vuelta a texto, reordenar claves— el hash dejaría de coincidir con el que
    # calculó el dispositivo y todo sobre legítimo acabaría en cuarentena.
    datos: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Sobre:
    operacion_id: uuid.UUID
    secuencia: int
    hash_payload: str
    operaciones: list[Operacion]
    visita_id: uuid.UUID | None = None

    @property
    def tipos(self) -> list[str]:
        return [o.tipo for o in self.operaciones]


def hash_de_sobre(sobre: Sobre) -> str:
    """Recalcula el hash canónico del contenido del sobre.

    Es lo que permite detectar que un mismo `operacion_id` llegó con contenido
    distinto: bug del cliente o manipulación. Ver contracts/README.md §1.
    """
    return hash_payload(
        {
            "operacion_id": str(sobre.operacion_id),
            "visita_id": str(sobre.visita_id) if sobre.visita_id else None,
            "operaciones": [
                {"tipo": o.tipo, "entidad_id": str(o.entidad_id), "datos": o.datos}
                for o in sobre.operaciones
            ],
        }
    )


def verificar_hash(sobre: Sobre) -> bool:
    try:
        return hash_de_sobre(sobre) == sobre.hash_payload
    except PayloadNoCanonico:
        # Un payload que no admite forma canónica (un float suelto, por
        # ejemplo) no puede compararse: se trata como no coincidente.
        return False


def validar_lote(sobres: list[Sobre]) -> list[Sobre]:
    """Valida la estructura del lote y devuelve los sobres en orden FIFO.

    El orden importa: un cobro que referencia una venta creada offline tiene que
    aplicarse después de ella. El dispositivo numera sus sobres al encolarlos y
    el servidor respeta esa numeración en vez de confiar en el orden del JSON,
    que cualquier serializador puede alterar.
    """
    if not sobres:
        raise LoteInvalido("el lote no trae sobres")
    if len(sobres) > MAX_SOBRES_POR_LOTE:
        raise LoteInvalido(
            f"el lote trae {len(sobres)} sobres; el máximo es {MAX_SOBRES_POR_LOTE}"
        )

    ids = [s.operacion_id for s in sobres]
    if len(ids) != len(set(ids)):
        raise LoteInvalido("el lote repite un operacion_id")

    secuencias = [s.secuencia for s in sobres]
    if len(secuencias) != len(set(secuencias)):
        raise LoteInvalido("el lote repite un número de secuencia")

    for sobre in sobres:
        if not sobre.operaciones:
            raise LoteInvalido(f"el sobre {sobre.operacion_id} viene vacío")
        entidades = [(o.tipo, o.entidad_id) for o in sobre.operaciones]
        if len(entidades) != len(set(entidades)):
            raise LoteInvalido(
                f"el sobre {sobre.operacion_id} trae la misma entidad dos veces"
            )

    return sorted(sobres, key=lambda s: s.secuencia)
