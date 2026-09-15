"""Modelos de la Fase 0: identidad, RBAC y dispositivos.

Solo se mapean las columnas que el código usa. Las demás conservan sus valores
por defecto en PostgreSQL; el SQL es la fuente de verdad.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.models.base import Base


class Sucursal(Base):
    __tablename__ = "sucursales"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Rol(Base):
    __tablename__ = "roles"

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text)
    descripcion: Mapped[str | None] = mapped_column(Text)


class Permiso(Base):
    __tablename__ = "permisos"

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descripcion: Mapped[str] = mapped_column(Text)
    modulo: Mapped[str] = mapped_column(Text)


class RolPermiso(Base):
    __tablename__ = "roles_permisos"

    rol_codigo: Mapped[str] = mapped_column(ForeignKey("roles.codigo"), primary_key=True)
    permiso_codigo: Mapped[str] = mapped_column(ForeignKey("permisos.codigo"), primary_key=True)


class UsuarioPermiso(Base):
    """Excepción sobre el rol: `otorgado=False` es una revocación explícita."""

    __tablename__ = "usuarios_permisos"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("usuarios.id"), primary_key=True
    )
    permiso_codigo: Mapped[str] = mapped_column(ForeignKey("permisos.codigo"), primary_key=True)
    otorgado: Mapped[bool] = mapped_column(Boolean)


class Almacen(Base):
    __tablename__ = "almacenes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    # Dueño exclusivo del almacén. Obligatorio para camiones: es la garantía de
    # no concurrencia sobre la que descansa todo el modelo offline.
    responsable_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Ruta(Base):
    __tablename__ = "rutas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str] = mapped_column(Text)
    vendedor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class UsuarioRuta(Base):
    __tablename__ = "usuarios_rutas"

    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), primary_key=True)
    ruta_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rutas.id"), primary_key=True)


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))
    codigo: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    telefono: Mapped[str | None] = mapped_column(Text)
    # Cadena PHC de Argon2id. Se replica al dispositivo para el login offline.
    password_hash: Mapped[str] = mapped_column(Text)
    rol_codigo: Mapped[str] = mapped_column(ForeignKey("roles.codigo"))
    almacen_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("almacenes.id"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    dias_max_offline: Mapped[int] = mapped_column(SmallInteger, default=7)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    dispositivos: Mapped[list[Dispositivo]] = relationship(
        back_populates="usuario", lazy="selectin", foreign_keys="Dispositivo.usuario_id"
    )


class Dispositivo(Base):
    """Unidad de confianza y espacio de nombres de los folios locales.

    Sin registro previo no se acepta sincronización.
    """

    __tablename__ = "dispositivos"

    # Generado en el dispositivo: sin default del lado del servidor.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"))
    etiqueta: Mapped[str] = mapped_column(Text)
    modelo: Mapped[str | None] = mapped_column(Text)
    so_version: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(Text)
    impresora_mac: Mapped[str | None] = mapped_column(Text)
    impresora_ancho_mm: Mapped[int | None] = mapped_column(SmallInteger)
    estado: Mapped[str] = mapped_column(String, default="activo")
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocado_motivo: Mapped[str | None] = mapped_column(Text)
    ultima_sync_push_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_sync_pull_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_cursor_pull: Mapped[int] = mapped_column(default=0)
    registrado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    usuario: Mapped[Usuario] = relationship(
        back_populates="dispositivos", lazy="joined", foreign_keys=[usuario_id]
    )


class Sesion(Base):
    __tablename__ = "sesiones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"))
    dispositivo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dispositivos.id"))
    refresh_token_hash: Mapped[str] = mapped_column(Text)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revocada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entidad: Mapped[str] = mapped_column(Text)
    entidad_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    accion: Mapped[str] = mapped_column(Text)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id"))
    dispositivo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dispositivos.id"))
    motivo: Mapped[str | None] = mapped_column(Text)
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
