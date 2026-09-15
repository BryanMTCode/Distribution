"""Motor, sesión y utilidades transaccionales."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import obtener_config

_cfg = obtener_config()

motor = create_async_engine(
    _cfg.database_url,
    pool_size=_cfg.db_pool_size,
    max_overflow=_cfg.db_max_overflow,
    pool_pre_ping=True,   # el servidor es local: un reinicio no debe tirar la app
    echo=_cfg.debug,
)

CrearSesion = async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession)


async def obtener_sesion() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI. Una sesión por request."""
    async with CrearSesion() as sesion:
        yield sesion


@asynccontextmanager
async def lock_de_dispositivo(sesion: AsyncSession, dispositivo_id: uuid.UUID):
    """Serializa los lotes de sync de un mismo dispositivo.

    Un teléfono con red intermitente puede reintentar mientras el envío original
    sigue procesándose; dos ejecuciones concurrentes sobre el mismo outbox
    producen interlaces feos. El advisory lock se libera solo al terminar la
    transacción, y los lotes de dispositivos distintos siguen en paralelo.
    """
    await sesion.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('sync_push'), hashtext(:dev))"),
        {"dev": str(dispositivo_id)},
    )
    yield
