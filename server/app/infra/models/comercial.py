"""Clientes y su situación de crédito.

Propiedad MIXTA (§2.3): el dispositivo crea clientes en la calle; las
condiciones comerciales —línea de crédito, bloqueo, lista de precios— son del
servidor y el teléfono solo las lee. Como las zonas de campos no se traslapan,
tampoco hay conflicto.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.models.base import Base


class Canal(Base):
    __tablename__ = "canales"

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text)


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    codigo: Mapped[str | None] = mapped_column(Text)
    nombre_comercial: Mapped[str] = mapped_column(Text)
    razon_social: Mapped[str | None] = mapped_column(Text)
    rfc: Mapped[str | None] = mapped_column(Text)
    canal_codigo: Mapped[str | None] = mapped_column(ForeignKey("canales.codigo"))

    ruta_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rutas.id"))
    secuencia: Mapped[int | None] = mapped_column(Integer)

    contacto_nombre: Mapped[str | None] = mapped_column(Text)
    telefono: Mapped[str | None] = mapped_column(Text)

    calle: Mapped[str | None] = mapped_column(Text)
    numero: Mapped[str | None] = mapped_column(Text)
    colonia: Mapped[str | None] = mapped_column(Text)
    municipio: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str | None] = mapped_column(Text)
    codigo_postal: Mapped[str | None] = mapped_column(Text)
    referencias: Mapped[str | None] = mapped_column(Text)

    lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    ubicacion_precision_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    # 'manual' = el vendedor ajustó las coordenadas porque el GPS falló. Es dato
    # auditable, no una anomalía.
    ubicacion_origen: Mapped[str | None] = mapped_column(Text)
    ubicacion_capturada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- Condiciones comerciales: propiedad del SERVIDOR ----
    lista_precios_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("listas_precios.id"))
    permite_credito: Mapped[bool] = mapped_column(Boolean, default=False)
    limite_credito: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    dias_credito: Mapped[int] = mapped_column(SmallInteger, default=0)
    bloqueado: Mapped[bool] = mapped_column(Boolean, default=False)
    bloqueo_motivo: Mapped[str | None] = mapped_column(Text)

    estatus: Mapped[str] = mapped_column(Text, default="activo")

    origen_alta: Mapped[str] = mapped_column(Text, default="oficina")
    creado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id"))
    dispositivo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dispositivos.id"))
    fecha_dispositivo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    requiere_revision: Mapped[bool] = mapped_column(Boolean, default=False)
    revision_motivo: Mapped[str | None] = mapped_column(Text)


class CuentaPorCobrar(Base):
    __tablename__ = "cuentas_por_cobrar"

    venta_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cliente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clientes.id"))
    importe_original: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    importe_pagado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Columna generada en PostgreSQL: importe_original - importe_pagado.
    saldo: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fecha_emision: Mapped[date] = mapped_column(Date)
    fecha_vencimiento: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(Text, default="abierta")
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
