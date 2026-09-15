"""UUIDv7.

Los documentos de campo traen el UUID **generado en el teléfono**: ese UUID es
la llave primaria en PostgreSQL, y es lo que hace que reenviar un lote no pueda
duplicar un ticket. Aquí solo se generan los identificadores que nacen en el
servidor, y se valida lo que llega.

`uuid.uuid7()` entró a la biblioteca estándar en Python 3.14. Mientras el
proyecto siga en 3.12/3.13 se usa la librería `uuid6`, que produce el mismo
formato (RFC 9562).

**Comportamiento en ráfaga (medido, no supuesto):** para garantizar
monotonicidad, la implementación adelanta su reloj interno ~1 ms por cada
identificador generado dentro del mismo milisegundo. Generar 5,000 seguidos
deja el timestamp embebido unos 5 segundos en el futuro.

Consecuencias:
  · El orden se conserva siempre — que es para lo que se usa v7.
  · `instante_de_uuid7()` es una **cota superior aproximada** del momento de
    creación, no un reloj. Para fechas de negocio están `fecha_dispositivo` y
    `fecha_servidor`; esto es solo forense.
"""

from __future__ import annotations

import uuid

try:  # Python >= 3.14
    from uuid import uuid7  # type: ignore[attr-defined]
except ImportError:  # Python 3.12 / 3.13
    from uuid6 import uuid7  # type: ignore[no-redef]

__all__ = ["es_uuid7", "instante_de_uuid7", "nuevo_id"]


def nuevo_id() -> uuid.UUID:
    """UUIDv7: ordenable por tiempo, que es toda su ventaja sobre v4."""
    return uuid7()


def es_uuid7(valor: uuid.UUID) -> bool:
    return valor.version == 7


def instante_de_uuid7(valor: uuid.UUID) -> int:
    """Milisegundos desde epoch embebidos en los primeros 48 bits.

    Útil en forense: revela cuándo se creó el documento en el teléfono, aunque
    el payload venga con la fecha manipulada.
    """
    if valor.version != 7:
        raise ValueError("el identificador no es UUIDv7")
    return valor.int >> 80
