"""Modelos de la maquinaria de sincronización usados en la Fase 0."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class FolioRango(Base):
    """Rango de folios que el servidor asigna a un dispositivo.

    El teléfono genera su consecutivo local, pero dentro de este rango. Si la
    app se reinstala, recibe un rango nuevo y sus folios nunca chocan con los
    del equipo anterior — que es exactamente la forma de volver a imprimir un
    folio que ya está en papel en manos de un cliente.
    """

    __tablename__ = "folios_rangos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dispositivo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispositivos.id"))
    documento_tipo: Mapped[str] = mapped_column(Text)
    desde: Mapped[int] = mapped_column(Integer)
    hasta: Mapped[int] = mapped_column(Integer)
    consumido_hasta: Mapped[int] = mapped_column(Integer, default=0)
    asignado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agotado: Mapped[bool] = mapped_column(Boolean, default=False)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    estado: Mapped[str] = mapped_column(Text, default="pendiente")
    prioridad: Mapped[int] = mapped_column(SmallInteger, default=100)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    max_intentos: Mapped[int] = mapped_column(Integer, default=5)
    ejecutar_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clave_unica: Mapped[str | None] = mapped_column(Text)
    tomado_por: Mapped[str | None] = mapped_column(Text)
    tomado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_error: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    terminado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
