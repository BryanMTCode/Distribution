"""Catálogo: productos, unidades y precios.

Propiedad del dato: SERVIDOR. En el dispositivo son de solo lectura y el
servidor siempre gana, así que no hay conflictos que resolver.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.models.base import Base


class Categoria(Base):
    __tablename__ = "categorias"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str] = mapped_column(Text)
    padre_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categorias.id"))
    orden: Mapped[int] = mapped_column(SmallInteger, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Marca(Base):
    __tablename__ = "marcas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class UnidadMedida(Base):
    __tablename__ = "unidades_medida"

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text)
    fraccionable: Mapped[bool] = mapped_column(Boolean, default=False)
    clave_sat: Mapped[str | None] = mapped_column(Text)


class Producto(Base):
    __tablename__ = "productos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    sku: Mapped[str] = mapped_column(Text, unique=True)
    codigo_barras: Mapped[str | None] = mapped_column(Text)
    nombre: Mapped[str] = mapped_column(Text)
    descripcion: Mapped[str | None] = mapped_column(Text)
    categoria_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categorias.id"))
    marca_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("marcas.id"))
    # Todo el inventario se lleva en esta unidad. Es la regla que evita el
    # descuadre caja/pieza.
    unidad_base: Mapped[str] = mapped_column(ForeignKey("unidades_medida.codigo"))
    peso_gramos: Mapped[int | None] = mapped_column(Integer)
    tasa_iva: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0"))
    tasa_ieps: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0"))
    maneja_lote: Mapped[bool] = mapped_column(Boolean, default=False)
    dias_caducidad: Mapped[int | None] = mapped_column(Integer)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    unidades: Mapped[list[ProductoUnidad]] = relationship(
        back_populates="producto", lazy="selectin", cascade="all, delete-orphan"
    )


class ProductoUnidad(Base):
    """Presentaciones vendibles: 1 CAJA = 24 PZA."""

    __tablename__ = "producto_unidades"

    producto_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("productos.id", ondelete="CASCADE"), primary_key=True
    )
    unidad_codigo: Mapped[str] = mapped_column(
        ForeignKey("unidades_medida.codigo"), primary_key=True
    )
    # Cuántas unidades base contiene. La conversión se congela en cada partida
    # de venta, para que un cambio de empaque no reescriba la historia.
    factor: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    es_default: Mapped[bool] = mapped_column(Boolean, default=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    producto: Mapped[Producto] = relationship(back_populates="unidades")


class ListaPrecios(Base):
    __tablename__ = "listas_precios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str] = mapped_column(Text)
    es_default: Mapped[bool] = mapped_column(Boolean, default=False)
    vigente_desde: Mapped[date] = mapped_column(Date)
    vigente_hasta: Mapped[date | None] = mapped_column(Date)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Precio(Base):
    __tablename__ = "precios"

    lista_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listas_precios.id", ondelete="CASCADE"), primary_key=True
    )
    producto_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("productos.id", ondelete="CASCADE"), primary_key=True
    )
    unidad_codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    precio: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    precio_minimo: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    # Viaja con la venta. Permite detectar que se vendió con una lista vieja
    # sin comparar importes uno a uno.
    version: Mapped[int] = mapped_column(Integer, default=1)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
