"""Motor, sesión y utilidades transaccionales."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import obtener_config

_cfg = obtener_config()


def _crear_motor(url: str):
    return create_async_engine(
        url,
        pool_size=_cfg.db_pool_size,
        max_overflow=_cfg.db_max_overflow,
        pool_pre_ping=True,   # el servidor es local: un reinicio no debe tirar la app
        echo=_cfg.debug,
    )


# ---------------------------------------------------------------------------
# DOS MOTORES, Y LA DIFERENCIA ES DE SEGURIDAD (Fase 9)
# ---------------------------------------------------------------------------
# `motor` usa el rol DUEÑO. Lo usan los workers, el CLI y Alembic: procesos de
# confianza que tienen que ver y escribir todo, y que no pertenecen a ninguna
# ruta.
#
# `motor_api` usa el rol RESTRINGIDO (`DSD_DATABASE_URL_API`), que no tiene
# BYPASSRLS. Con él, las políticas por renglón de la migración 0022 se aplican
# de verdad: un error en una consulta que olvide filtrar por ruta no devuelve la
# cartera completa, la devuelve vacía.
#
# Sin esa variable los dos motores son el mismo y RLS queda sin efecto. Se
# permite en desarrollo —y `/salud` lo dice— pero en producción la API no
# arranca sin ella (ver `core/config.py`).
motor = _crear_motor(_cfg.database_url)
motor_api = motor if not _cfg.rls_activa else _crear_motor(_cfg.url_de_la_api)

CrearSesion = async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession)
CrearSesionApi = async_sessionmaker(motor_api, expire_on_commit=False, class_=AsyncSession)

# Valor del alcance cuando todavía no se sabe quién manda la petición.
#
# `anonimo` NO es «sin restricción»: las políticas de la 0022 lo rechazan todo.
# Es la posición de FALLO CERRADO, y es la razón de que el alcance se fije al
# abrir la sesión y no solo al autenticar: las conexiones vienen de un pool, y
# sin esto una petición sin token heredaría el alcance de la anterior.
ROL_ANONIMO = "anonimo"


async def fijar_alcance(
    sesion: AsyncSession,
    *,
    rol: str,
    usuario_id: uuid.UUID | None = None,
    rutas: Iterable[uuid.UUID] = (),
) -> None:
    """Deja el alcance del actor en la sesión de PostgreSQL.

    `set_config(..., false)` —nivel de SESIÓN y no de transacción— a propósito.
    Con `SET LOCAL`, el alcance se perdería en el primer `commit()`, y una
    petición que escribe y luego lee —el alta de un cliente, el cierre de una
    liquidación— empezaría a recibir resultados vacíos después de guardar. Ese
    fallo aparecería solo en algunos endpoints y sería dificilísimo de atribuir.

    El riesgo de ensuciar la conexión del pool se cierra en el otro extremo:
    `obtener_sesion` fija el alcance anónimo ANTES de cualquier consulta, así
    que lo que dejó la petición anterior siempre queda sobreescrito.
    """
    await sesion.execute(
        text(
            "SELECT set_config('dsd.rol', :rol, false),"
            "       set_config('dsd.usuario_id', :usuario, false),"
            "       set_config('dsd.rutas', :rutas, false)"
        ),
        {
            "rol": rol,
            "usuario": str(usuario_id) if usuario_id else "",
            # Lista separada por comas: PostgreSQL no tiene un parámetro de
            # arreglo para `set_config`, y la política la parte con
            # `string_to_array`. Vacía significa «ninguna ruta», que para un
            # vendedor es no ver nada — el fallo cerrado otra vez.
            "rutas": ",".join(str(r) for r in rutas),
        },
    )


async def obtener_sesion() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI. Una sesión por request, con alcance anónimo."""
    async with CrearSesionApi() as sesion:
        await fijar_alcance(sesion, rol=ROL_ANONIMO)
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
