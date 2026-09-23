"""Resultados de la sincronización.

Tres estados y ninguno más. La diferencia entre ellos es lo que el dispositivo
hace después:

  · ACEPTADA   → marca el sobre como confirmado y lo saca de la cola.
  · DUPLICADA  → lo mismo. Ya estaba aplicado; el reenvío fue inofensivo, que
                 es exactamente el objetivo del diseño.
  · RECHAZADA  → lo saca de la cola y lo da por perdido *en el dispositivo*.
                 El payload íntegro quedó en cuarentena del lado del servidor
                 para revisión humana.

Ese último punto es el que mantiene viva la app: **una operación rechazada
nunca bloquea la cola**. Una cola atorada deja al vendedor sin poder vender.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["Estado", "ResultadoLote", "ResultadoSobre"]


class Estado(StrEnum):
    ACEPTADA = "aceptada"
    DUPLICADA = "duplicada"
    RECHAZADA = "rechazada"


@dataclass
class ResultadoSobre:
    operacion_id: uuid.UUID
    estado: Estado
    # Entidades que quedaron escritas, por si el dispositivo quiere conciliar.
    entidades: list[uuid.UUID] = field(default_factory=list)
    error_codigo: str | None = None
    error_mensaje: str | None = None


@dataclass
class ResultadoLote:
    lote_id: uuid.UUID
    resultados: list[ResultadoSobre] = field(default_factory=list)

    @property
    def aceptadas(self) -> int:
        return sum(1 for r in self.resultados if r.estado is Estado.ACEPTADA)

    @property
    def duplicadas(self) -> int:
        return sum(1 for r in self.resultados if r.estado is Estado.DUPLICADA)

    @property
    def rechazadas(self) -> int:
        return sum(1 for r in self.resultados if r.estado is Estado.RECHAZADA)
